import { Duration, Stack, StackProps } from 'aws-cdk-lib';
import * as dynamodb from 'aws-cdk-lib/aws-dynamodb';
import * as ec2 from 'aws-cdk-lib/aws-ec2';
import * as ecr_assets from 'aws-cdk-lib/aws-ecr-assets';
import * as ecs from 'aws-cdk-lib/aws-ecs';
import * as events from 'aws-cdk-lib/aws-events';
import * as targets from 'aws-cdk-lib/aws-events-targets';
import * as iam from 'aws-cdk-lib/aws-iam';
import * as lambda from 'aws-cdk-lib/aws-lambda';
import * as sources from 'aws-cdk-lib/aws-lambda-event-sources';
import * as logs from 'aws-cdk-lib/aws-logs';
import * as s3 from 'aws-cdk-lib/aws-s3';
import * as sqs from 'aws-cdk-lib/aws-sqs';
import { Construct } from 'constructs';
import { pyFunction, SERVICES_DIR } from './python';

export interface IngestStackProps extends StackProps {
  mediaBucket: s3.IBucket;
  table: dynamodb.ITableV2;
  vectorBucketName: string;
  vectorBucketArn: string;
  /** Max concurrent model calls (Pegasus/Marengo/Transcribe starts). */
  modelConcurrency?: number;
}

const MODEL_MAX_RECEIVE = 4;

/** Event-driven ingest: S3 → EventBridge → SQS → coordinator → Fargate media worker / model workers / poller. */
export class IngestStack extends Stack {
  readonly vpc: ec2.Vpc;
  readonly cluster: ecs.Cluster;
  readonly runtimeEnv: Record<string, string>;
  readonly bedrockPolicy: iam.PolicyStatement[];
  readonly ingestQueue: sqs.Queue;
  readonly modelQueue: sqs.Queue;

  constructor(scope: Construct, id: string, props: IngestStackProps) {
    super(scope, id, props);

    this.vpc = new ec2.Vpc(this, 'Vpc', {
      maxAzs: 2,
      natGateways: 1,
      subnetConfiguration: [
        { name: 'public', subnetType: ec2.SubnetType.PUBLIC, cidrMask: 24 },
        { name: 'private', subnetType: ec2.SubnetType.PRIVATE_WITH_EGRESS, cidrMask: 20 },
      ],
      gatewayEndpoints: { S3: { service: ec2.GatewayVpcEndpointAwsService.S3 } },
    });
    this.vpc.addFlowLog('FlowLog');

    const dlq = (name: string) => new sqs.Queue(this, `${name}Dlq`, {
      retentionPeriod: Duration.days(14), enforceSSL: true, encryption: sqs.QueueEncryption.SQS_MANAGED,
    });
    const queue = (name: string, visibility: Duration, maxReceive: number) => new sqs.Queue(this, name, {
      visibilityTimeout: visibility,
      enforceSSL: true,
      encryption: sqs.QueueEncryption.SQS_MANAGED,
      deadLetterQueue: { queue: dlq(name), maxReceiveCount: maxReceive },
    });
    const ingestQueue = queue('IngestQueue', Duration.minutes(6), 5);
    const modelQueue = queue('ModelQueue', Duration.minutes(16), MODEL_MAX_RECEIVE);
    this.ingestQueue = ingestQueue;
    this.modelQueue = modelQueue;
    const pollQueue = queue('PollQueue', Duration.minutes(3), 5);

    // Original uploads only: tenants/{t}/assets/{a}/original/{file}
    new events.Rule(this, 'OriginalUploaded', {
      eventPattern: {
        source: ['aws.s3'],
        detailType: ['Object Created'],
        detail: {
          bucket: { name: [props.mediaBucket.bucketName] },
          object: { key: [{ wildcard: 'tenants/*/assets/*/original/*' }] },
        },
      },
      targets: [new targets.SqsQueue(ingestQueue)],
    });

    // ---------------------------------------------------------------- media worker (Fargate)
    this.cluster = new ecs.Cluster(this, 'Cluster', { vpc: this.vpc, containerInsightsV2: ecs.ContainerInsights.ENABLED });
    const taskDef = new ecs.FargateTaskDefinition(this, 'MediaWorker', {
      cpu: 4096,
      memoryLimitMiB: 16384,
      ephemeralStorageGiB: 200,
      runtimePlatform: { cpuArchitecture: ecs.CpuArchitecture.ARM64, operatingSystemFamily: ecs.OperatingSystemFamily.LINUX },
    });
    const workerSg = new ec2.SecurityGroup(this, 'WorkerSg', {
      vpc: this.vpc, allowAllOutbound: true, description: 'Media worker: egress only, no ingress',
    });

    this.runtimeEnv = {
      ACCOUNT_ID: this.account,
      TABLE_NAME: props.table.tableName,
      MEDIA_BUCKET: props.mediaBucket.bucketName,
      VECTOR_BUCKET: props.vectorBucketName,
      INGEST_QUEUE_URL: ingestQueue.queueUrl,
      MODEL_QUEUE_URL: modelQueue.queueUrl,
      POLL_QUEUE_URL: pollQueue.queueUrl,
      MEDIA_WORKER_CLUSTER: this.cluster.clusterArn,
      MEDIA_WORKER_TASK_DEF: taskDef.taskDefinitionArn,
      MEDIA_WORKER_SUBNETS: this.vpc.privateSubnets.map((s) => s.subnetId).join(','),
      MEDIA_WORKER_SG: workerSg.securityGroupId,
      MODEL_MAX_RECEIVE: String(MODEL_MAX_RECEIVE),
    };

    taskDef.addContainer('worker', {
      image: ecs.ContainerImage.fromAsset(SERVICES_DIR, {
        file: 'media-worker/Dockerfile',
        platform: ecr_assets.Platform.LINUX_ARM64,
        exclude: ['.venv', 'tests', '**/__pycache__', '.pytest_cache'],
      }),
      logging: ecs.LogDrivers.awsLogs({ streamPrefix: 'media', logRetention: logs.RetentionDays.ONE_MONTH }),
      environment: {
        ...Object.fromEntries(Object.entries(this.runtimeEnv).filter(([k]) => !k.startsWith('MEDIA_WORKER_'))),
        AWS_DEFAULT_REGION: this.region,
      },
    });
    props.mediaBucket.grantReadWrite(taskDef.taskRole);
    ingestQueue.grantSendMessages(taskDef.taskRole);

    // ---------------------------------------------------------------- Bedrock / Transcribe / S3 Vectors permissions
    this.bedrockPolicy = [
      new iam.PolicyStatement({
        actions: ['bedrock:InvokeModel', 'bedrock:InvokeModelWithResponseStream', 'bedrock:StartAsyncInvoke',
          'bedrock:GetAsyncInvoke', 'bedrock:Converse', 'bedrock:ConverseStream'],
        resources: [
          `arn:aws:bedrock:*::foundation-model/*`,
          `arn:aws:bedrock:*:${this.account}:inference-profile/*`,
          `arn:aws:bedrock:${this.region}:${this.account}:async-invoke/*`,
        ],
      }),
      new iam.PolicyStatement({
        actions: ['s3vectors:GetIndex', 's3vectors:CreateIndex', 's3vectors:PutVectors', 's3vectors:QueryVectors',
          's3vectors:GetVectors', 's3vectors:DeleteVectors', 's3vectors:ListVectors'],
        resources: [props.vectorBucketArn, `${props.vectorBucketArn}/index/*`],
      }),
    ];

    const coordinator = pyFunction(this, 'Coordinator', {
      handler: 'shorts.handlers.ingest_coordinator.handler',
      environment: this.runtimeEnv,
      timeout: Duration.minutes(5),
      memorySize: 1024,
      description: 'Ingest coordinator',
    });
    const modelWorker = pyFunction(this, 'ModelWorker', {
      handler: 'shorts.handlers.model_worker.handler',
      environment: this.runtimeEnv,
      timeout: Duration.minutes(15),
      memorySize: 1024,
      reservedConcurrentExecutions: props.modelConcurrency ?? 20,
      description: 'Pegasus / Marengo / Transcribe tasks',
    });
    const poller = pyFunction(this, 'Poller', {
      handler: 'shorts.handlers.poller.handler',
      environment: this.runtimeEnv,
      timeout: Duration.minutes(2),
      memorySize: 1024,
      description: 'Polls async model / transcribe jobs',
    });

    for (const fn of [coordinator, modelWorker, poller]) {
      props.table.grantReadWriteData(fn);
      props.mediaBucket.grantReadWrite(fn);
      ingestQueue.grantSendMessages(fn);
      modelQueue.grantSendMessages(fn);
      pollQueue.grantSendMessages(fn);
      this.bedrockPolicy.forEach((s) => fn.addToRolePolicy(s));
    }
    for (const fn of [modelWorker, poller]) {
      fn.addToRolePolicy(new iam.PolicyStatement({
        actions: ['transcribe:StartTranscriptionJob', 'transcribe:GetTranscriptionJob'],
        resources: ['*'],
      }));
    }
    coordinator.addToRolePolicy(new iam.PolicyStatement({ actions: ['ecs:RunTask'], resources: [taskDef.taskDefinitionArn] }));
    coordinator.addToRolePolicy(new iam.PolicyStatement({
      actions: ['iam:PassRole'],
      resources: [taskDef.taskRole.roleArn, taskDef.executionRole!.roleArn],
    }));

    const sqsSource = (q: sqs.IQueue, batchSize: number) =>
      new sources.SqsEventSource(q, { batchSize, reportBatchItemFailures: true });
    coordinator.addEventSource(sqsSource(ingestQueue, 5));
    modelWorker.addEventSource(new sources.SqsEventSource(modelQueue, {
      batchSize: 1, reportBatchItemFailures: true, maxConcurrency: props.modelConcurrency ?? 20,
    }));
    poller.addEventSource(sqsSource(pollQueue, 10));
  }

  /** Grant a function the model/vector permissions used by search & tools. */
  grantModels(fn: lambda.Function) {
    this.bedrockPolicy.forEach((s) => fn.addToRolePolicy(s));
  }
}
