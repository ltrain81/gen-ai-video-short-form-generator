import * as cdk from 'aws-cdk-lib';
import { DockerImage, Duration, Stack } from 'aws-cdk-lib';
import * as appscaling from 'aws-cdk-lib/aws-applicationautoscaling';
import * as cloudwatch from 'aws-cdk-lib/aws-cloudwatch';
import * as iam from 'aws-cdk-lib/aws-iam';
import * as lambda from 'aws-cdk-lib/aws-lambda';
import * as sources from 'aws-cdk-lib/aws-lambda-event-sources';
import * as s3 from 'aws-cdk-lib/aws-s3';
import * as s3assets from 'aws-cdk-lib/aws-s3-assets';
import * as sagemaker from 'aws-cdk-lib/aws-sagemaker';
import * as sqs from 'aws-cdk-lib/aws-sqs';
import { Construct } from 'constructs';
import * as path from 'path';
import { SERVICES_DIR } from './python';

/** Open-source generation models (Apache-2.0 / MIT) on SageMaker async endpoints that scale to zero. */
export const GEN_MODELS = [
  { kind: 'image', dir: 'image', modelId: 'Tongyi-MAI/Z-Image-Turbo', instance: 'ml.g6e.xlarge' },
  { kind: 'image-edit', dir: 'image_edit', modelId: 'Qwen/Qwen-Image-Edit-2509', instance: 'ml.g6e.4xlarge' },
  { kind: 'video', dir: 'video', modelId: 'Wan-AI/Wan2.2-TI2V-5B-Diffusers', instance: 'ml.g6e.2xlarge' },
  { kind: 'tts', dir: 'tts', modelId: 'openbmb/VoxCPM2', instance: 'ml.g6.xlarge' },
  { kind: 'music', dir: 'music', modelId: 'ACE-Step/ACE-Step-v1-3.5B', instance: 'ml.g5.xlarge' },
];
const DLC = (region: string) =>
  `763104351884.dkr.ecr.${region}.amazonaws.com/huggingface-pytorch-inference:2.6.0-transformers5.5.3-gpu-py312-cu124-ubuntu22.04-v2.1`;

/**
 * Lives in the Agent stack: the job queue + permissions/env for functions that start jobs. Endpoint names are
 * deterministic, so nothing depends on the (separately deployed, failure-isolated) GenerationStack.
 */
export class GenerationWiring extends Construct {
  readonly endpointPrefix: string;
  readonly pollQueue: sqs.Queue;

  constructor(scope: Construct, id: string, prefix: string) {
    super(scope, id);
    this.endpointPrefix = `${prefix.toLowerCase()}-gen`;
    this.pollQueue = new sqs.Queue(this, 'PollQueue', {
      visibilityTimeout: Duration.minutes(3), enforceSSL: true, encryption: sqs.QueueEncryption.SQS_MANAGED,
    });
  }

  wire(fn: lambda.Function) {
    const stack = Stack.of(fn);
    fn.addEnvironment('GEN_ENDPOINT_PREFIX', this.endpointPrefix);
    fn.addEnvironment('GEN_POLL_QUEUE_URL', this.pollQueue.queueUrl);
    fn.addToRolePolicy(new iam.PolicyStatement({
      actions: ['sagemaker:InvokeEndpointAsync', 'sagemaker:DescribeEndpoint'],
      resources: [`arn:aws:sagemaker:${stack.region}:${stack.account}:endpoint/${this.endpointPrefix}-*`],
    }));
    fn.addToRolePolicy(new iam.PolicyStatement({ actions: ['polly:SynthesizeSpeech'], resources: ['*'] }));
    fn.addToRolePolicy(new iam.PolicyStatement({
      actions: ['secretsmanager:GetSecretValue', 'secretsmanager:DescribeSecret', 'secretsmanager:PutSecretValue', 'secretsmanager:CreateSecret'],
      resources: [`arn:aws:secretsmanager:${stack.region}:${stack.account}:secret:shorts/*`],
    }));
    this.pollQueue.grantSendMessages(fn);
  }
}

export interface GenerationStackProps extends cdk.StackProps {
  mediaBucket: s3.IBucket;
  wiring: GenerationWiring;
  poller: (scope: Construct) => lambda.Function;
}

/** Shared pieces of generation mode: the SageMaker execution role and the output poller. */
export class GenerationStack extends Stack {
  readonly role: iam.Role;

  constructor(scope: Construct, id: string, props: GenerationStackProps) {
    super(scope, id, props);
    const role = new iam.Role(this, 'SageMakerRole', {
      assumedBy: new iam.ServicePrincipal('sagemaker.amazonaws.com'),
      description: 'Generation endpoints: read inputs/write outputs under gen/ and gen-out/, pull AWS DLC images',
    });
    this.role = role;
    props.mediaBucket.grantRead(role, 'gen/*');
    props.mediaBucket.grantReadWrite(role, 'gen-out/*');
    // model.tar.gz (inference code) lives in the CDK bootstrap assets bucket of this account/region.
    role.addToPolicy(new iam.PolicyStatement({
      actions: ['s3:GetObject'], resources: [`arn:aws:s3:::cdk-*-assets-${this.account}-${this.region}/*`],
    }));
    role.addToPolicy(new iam.PolicyStatement({ actions: ['ecr:GetAuthorizationToken'], resources: ['*'] }));
    role.addToPolicy(new iam.PolicyStatement({
      actions: ['ecr:BatchGetImage', 'ecr:GetDownloadUrlForLayer', 'ecr:BatchCheckLayerAvailability'],
      resources: [`arn:aws:ecr:${this.region}:763104351884:repository/*`],
    }));
    role.addToPolicy(new iam.PolicyStatement({
      actions: ['logs:CreateLogGroup', 'logs:CreateLogStream', 'logs:PutLogEvents', 'logs:DescribeLogStreams', 'cloudwatch:PutMetricData'],
      resources: ['*'],
    }));
    const poller = props.poller(this);
    props.wiring.wire(poller);
    poller.addEventSource(new sources.SqsEventSource(props.wiring.pollQueue, { batchSize: 5, reportBatchItemFailures: true }));
  }
}

export interface GenerationModelStackProps extends cdk.StackProps {
  mediaBucket: s3.IBucket;
  role: iam.IRole;
  endpointPrefix: string;
  model: (typeof GEN_MODELS)[number];
  /** Override the GPU type (e.g. when a type has no capacity): -c genInstance-music=ml.g5.2xlarge */
  instance?: string;
}

/**
 * One SageMaker async endpoint per stack: if a model cannot get GPU capacity (InsufficientInstanceCapacity)
 * or fails to load, only that model's stack rolls back — the others stay up.
 */
export class GenerationModelStack extends Stack {
  constructor(scope: Construct, id: string, props: GenerationModelStackProps) {
    super(scope, id, props);
    const m = props.model;
    const instance = props.instance ?? m.instance;
    const genDir = path.join(SERVICES_DIR, 'generation');
    // model.tar.gz = code/{inference.py, requirements.txt, shared.py}; weights come from Hugging Face on start.
    const code = new s3assets.Asset(this, 'Code', {
      path: genDir,
      bundling: {
        image: DockerImage.fromRegistry('public.ecr.aws/docker/library/alpine:3.20@sha256:d9e853e87e55526f6b2917df91a2115c36dd7c696a35be12163d44e6e2a4b6bc'),
        outputType: cdk.BundlingOutput.SINGLE_FILE,
        command: ['sh', '-c', `mkdir -p /tmp/code && cp -r /asset-input/${m.dir}/code/. /tmp/code/ && cp /asset-input/common/shared.py /tmp/code/ && tar czf /asset-output/model.tar.gz -C /tmp code`],
      },
    });
    const name = `${props.endpointPrefix}-${m.kind}`;
    const model = new sagemaker.CfnModel(this, 'Model', {
      executionRoleArn: props.role.roleArn,
      primaryContainer: {
        image: DLC(this.region),
        modelDataUrl: code.s3ObjectUrl,
        environment: {
          MODEL_ID: m.modelId, SAGEMAKER_PROGRAM: 'inference.py', SAGEMAKER_SUBMIT_DIRECTORY: '/opt/ml/model/code',
          HF_HOME: '/tmp/hf', ACESTEP_DIR: '/tmp/acestep', SAGEMAKER_MODEL_SERVER_WORKERS: '1',
          SAGEMAKER_MODEL_SERVER_TIMEOUT: '3600', MMS_DEFAULT_RESPONSE_TIMEOUT: '3600',
          MMS_MAX_RESPONSE_SIZE: '536870912', TS_MAX_RESPONSE_SIZE: '536870912',
        },
      },
    });
    const config = new sagemaker.CfnEndpointConfig(this, 'Config', {
      productionVariants: [{
        variantName: 'AllTraffic', modelName: model.attrModelName, instanceType: instance, initialInstanceCount: 1,
        containerStartupHealthCheckTimeoutInSeconds: 3600, modelDataDownloadTimeoutInSeconds: 3600,
      }],
      asyncInferenceConfig: {
        clientConfig: { maxConcurrentInvocationsPerInstance: 1 },
        outputConfig: {
          s3OutputPath: `s3://${props.mediaBucket.bucketName}/gen-out/${m.kind}/`,
          s3FailurePath: `s3://${props.mediaBucket.bucketName}/gen-out/${m.kind}-failed/`,
        },
      },
    });
    const endpoint = new sagemaker.CfnEndpoint(this, 'Endpoint', { endpointName: name, endpointConfigName: config.attrEndpointConfigName });

    // Scale to zero when idle; wake on backlog.
    const target = new appscaling.ScalableTarget(this, 'Scale', {
      serviceNamespace: appscaling.ServiceNamespace.SAGEMAKER,
      resourceId: `endpoint/${name}/variant/AllTraffic`,
      scalableDimension: 'sagemaker:variant:DesiredInstanceCount',
      minCapacity: 0, maxCapacity: 1,
    });
    target.node.addDependency(endpoint);
    target.scaleToTrackMetric('Backlog', {
      targetValue: 1,
      customMetric: new cloudwatch.Metric({
        namespace: 'AWS/SageMaker', metricName: 'ApproximateBacklogSizePerInstance',
        dimensionsMap: { EndpointName: name }, statistic: 'Average', period: Duration.minutes(1),
      }),
      scaleInCooldown: Duration.minutes(20), scaleOutCooldown: Duration.minutes(2),
    });
    target.scaleOnMetric('Wake', {
      metric: new cloudwatch.Metric({
        namespace: 'AWS/SageMaker', metricName: 'HasBacklogWithoutCapacity',
        dimensionsMap: { EndpointName: name }, statistic: 'Average', period: Duration.minutes(1),
      }),
      scalingSteps: [{ upper: 0.5, change: 0 }, { lower: 0.5, change: 1 }],
      adjustmentType: appscaling.AdjustmentType.CHANGE_IN_CAPACITY,
      cooldown: Duration.minutes(5),
    });
    new cdk.CfnOutput(this, 'EndpointName', { value: name });
    new cdk.CfnOutput(this, 'Instance', { value: instance });
  }
}
