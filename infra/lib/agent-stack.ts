import * as cdk from 'aws-cdk-lib';
import { CfnOutput, Duration, Stack, StackProps } from 'aws-cdk-lib';
import * as apigw from 'aws-cdk-lib/aws-apigatewayv2';
import * as authorizers from 'aws-cdk-lib/aws-apigatewayv2-authorizers';
import * as integrations from 'aws-cdk-lib/aws-apigatewayv2-integrations';
import * as appsync from 'aws-cdk-lib/aws-appsync';
import * as agentcore from 'aws-cdk-lib/aws-bedrockagentcore';
import * as cognito from 'aws-cdk-lib/aws-cognito';
import * as dynamodb from 'aws-cdk-lib/aws-dynamodb';
import * as ecr_assets from 'aws-cdk-lib/aws-ecr-assets';
import * as iam from 'aws-cdk-lib/aws-iam';
import * as lambda from 'aws-cdk-lib/aws-lambda';
import * as s3 from 'aws-cdk-lib/aws-s3';
import { Construct } from 'constructs';
import * as path from 'path';
import { GenerationWiring } from './generation';
import { IngestStack } from './ingest-stack';
import { pyFunction } from './python';

export interface AgentStackProps extends StackProps {
  prefix: string;
  mediaBucket: s3.IBucket;
  table: dynamodb.ITableV2;
  userPool: cognito.IUserPool;
  ingest: IngestStack;
  bmaModelId: string;
  mode: string;
}

const AGENT_PACKAGES = ['openai>=3.16', 'aws-bedrock-token-generator>=1.1', 'imageio-ffmpeg>=0.6', 'strands-agents==1.65.0'];

/**
 * Orchestrator: Bedrock Managed Agents (OpenAI Codex harness) with our tools running inside an AgentCore Runtime
 * microVM; broker Lambda relays turns to the browser through AppSync Events.
 */
export class AgentStack extends Stack {
  readonly broker: lambda.Function;
  readonly eventsHttpDomain: string;
  readonly eventsRealtimeDomain: string;
  readonly generation?: GenerationWiring;
  readonly commonEnv: Record<string, string>;
  readonly eventsApi: appsync.EventApi;

  constructor(scope: Construct, id: string, props: AgentStackProps) {
    super(scope, id, props);
    const name = `${props.prefix}_orchestrator`.replace(/[^A-Za-z0-9_]/g, '_');

    // ---------------------------------------------------------------- realtime events (browser)
    const eventsApi = new appsync.EventApi(this, 'Events', {
      apiName: `${props.prefix}-events`,
      authorizationConfig: {
        authProviders: [
          { authorizationType: appsync.AppSyncAuthorizationType.USER_POOL, cognitoConfig: { userPool: props.userPool } },
          { authorizationType: appsync.AppSyncAuthorizationType.IAM },
        ],
        connectionAuthModeTypes: [appsync.AppSyncAuthorizationType.USER_POOL, appsync.AppSyncAuthorizationType.IAM],
        defaultPublishAuthModeTypes: [appsync.AppSyncAuthorizationType.IAM],
        defaultSubscribeAuthModeTypes: [appsync.AppSyncAuthorizationType.USER_POOL],
      },
    });
    // Browsers may only subscribe to /projects/{projectId}/{their own sub}; only backend IAM roles publish.
    eventsApi.addChannelNamespace('projects', {
      code: appsync.Code.fromInline(`
export function onSubscribe(ctx) {
  const segs = ctx.info.channel.path.split('/');
  if (segs.length !== 4 || !ctx.identity || ctx.identity.sub !== segs[3]) {
    util.unauthorized();
  }
}`),
    });
    this.eventsHttpDomain = eventsApi.httpDns;
    this.eventsRealtimeDomain = eventsApi.realtimeDns;

    // ---------------------------------------------------------------- tools API (IAM: runtime role only)
    const runtimeRole = new iam.Role(this, 'RuntimeRole', {
      assumedBy: new iam.ServicePrincipal('bedrock-agentcore.amazonaws.com', {
        conditions: { StringEquals: { 'aws:SourceAccount': this.account } },
      }),
      description: 'AgentCore Runtime execution role for the BMA environment (exec-server + tool proxy)',
    });
    const common = {
      ...props.ingest.sharedEnv,
      EVENTS_HTTP_DOMAIN: this.eventsHttpDomain,
      DEPLOY_MODE: props.mode,
    };
    const tools = pyFunction(this, 'Tools', {
      handler: 'shorts.handlers.tools_api.handler',
      environment: { ...common, AGENT_ROLE_NAMES: runtimeRole.roleName },
      timeout: Duration.seconds(300),
      memorySize: 2048,
      extraPackages: AGENT_PACKAGES,
      description: 'Agent tools (archive search, analysis, framing, timeline edits)',
    });
    props.table.grantReadWriteData(tools);
    props.mediaBucket.grantReadWrite(tools);
    props.ingest.grantModels(tools);
    eventsApi.grantPublish(tools);

    const toolsApi = new apigw.HttpApi(this, 'ToolsApi', {
      defaultAuthorizer: new authorizers.HttpIamAuthorizer(),
      description: 'Agent tools API — AWS_IAM only (agent runtime role)',
    });
    const integration = new integrations.HttpLambdaIntegration('ToolsFn', tools);
    toolsApi.addRoutes({ path: '/agent/tools', methods: [apigw.HttpMethod.GET], integration });
    toolsApi.addRoutes({ path: '/agent/tools/call', methods: [apigw.HttpMethod.POST], integration });
    runtimeRole.addToPolicy(new iam.PolicyStatement({
      actions: ['execute-api:Invoke'],
      resources: [`arn:aws:execute-api:${this.region}:${this.account}:${toolsApi.apiId}/*/*/agent/tools*`],
    }));

    // ---------------------------------------------------------------- AgentCore Runtime (BMA environment)
    // Build context = repo root (runtime files + shared skills); everything else is ignored.
    const image = new ecr_assets.DockerImageAsset(this, 'RuntimeImage', {
      directory: path.join(__dirname, '..', '..'),
      file: 'agents/runtime/Dockerfile',
      platform: ecr_assets.Platform.LINUX_ARM64,
      ignoreMode: cdk.IgnoreMode.DOCKER,
      exclude: ['**', '!agents/runtime/**', '!services/src/shorts/skills/**'],
    });
    image.repository.grantPull(runtimeRole);
    runtimeRole.addToPolicy(new iam.PolicyStatement({
      actions: ['bedrock-mantle:RegisterEnvironment', 'bedrock-mantle:ConnectEnvironment'],
      resources: [`arn:aws:bedrock-mantle:${this.region}:${this.account}:project/*`],
    }));
    runtimeRole.addToPolicy(new iam.PolicyStatement({
      actions: ['ecr:GetAuthorizationToken', 'xray:PutTelemetryRecords', 'xray:PutTraceSegments', 'logs:DescribeLogGroups'],
      resources: ['*'],
    }));
    runtimeRole.addToPolicy(new iam.PolicyStatement({
      actions: ['logs:CreateLogGroup', 'logs:CreateLogStream', 'logs:DescribeLogStreams', 'logs:PutLogEvents'],
      resources: [`arn:aws:logs:${this.region}:${this.account}:log-group:/aws/bedrock-agentcore/runtimes/*`],
    }));
    runtimeRole.addToPolicy(new iam.PolicyStatement({
      actions: ['cloudwatch:PutMetricData'], resources: ['*'],
      conditions: { StringEquals: { 'cloudwatch:namespace': 'bedrock-agentcore' } },
    }));

    const runtime = new agentcore.CfnRuntime(this, 'Runtime', {
      agentRuntimeName: name,
      description: 'AWS Shorts V2 orchestrator environment (Bedrock Managed Agents)',
      roleArn: runtimeRole.roleArn,
      agentRuntimeArtifact: { containerConfiguration: { containerUri: image.imageUri } },
      networkConfiguration: { networkMode: 'PUBLIC' },
      protocolConfiguration: 'HTTP',
      lifecycleConfiguration: { idleRuntimeSessionTimeout: 1800, maxLifetime: 28800 },
      environmentVariables: {
        SHORTS_TOOLS_URL: toolsApi.apiEndpoint,
        AWS_REGION: this.region,
      },
    });
    runtime.node.addDependency(runtimeRole);
    const runtimeArn = runtime.attrAgentRuntimeArn;

    // ---------------------------------------------------------------- BMA session role (assumed by bedrock-mantle)
    const sessionRole = new iam.Role(this, 'BmaSessionRole', {
      assumedBy: new iam.ServicePrincipal('bedrock-mantle.amazonaws.com', {
        conditions: {
          StringEquals: { 'aws:SourceAccount': this.account },
          ArnLike: { 'aws:SourceArn': `arn:aws:bedrock-mantle:${this.region}:${this.account}:*` },
        },
      }),
      description: 'Bedrock Managed Agents session role: model inference + this project runtime only',
    });
    sessionRole.addToPolicy(new iam.PolicyStatement({
      actions: ['bedrock-mantle:CreateInference'], resources: ['*'],
      conditions: { StringEquals: { 'bedrock-mantle:Model': props.bmaModelId } },
    }));
    sessionRole.addToPolicy(new iam.PolicyStatement({
      actions: ['bedrock-agentcore:InvokeAgentRuntime', 'bedrock-agentcore:StopRuntimeSession'],
      resources: [runtimeArn, `${runtimeArn}/runtime-endpoint/*`],
    }));

    // ---------------------------------------------------------------- broker
    this.broker = pyFunction(this, 'Broker', {
      handler: 'shorts.handlers.broker.handler',
      environment: {
        ...common,
        ORCHESTRATOR: 'bma',
        BMA_MODEL_ID: props.bmaModelId,
        BMA_RUNTIME_ARN: runtimeArn,
        BMA_SESSION_ROLE_ARN: sessionRole.roleArn,
        STRANDS_MODEL_ID: 'us.openai.gpt-6.1-sol',
      },
      timeout: Duration.minutes(15),
      memorySize: 1024,
      extraPackages: AGENT_PACKAGES,
      description: 'Runs one orchestrator turn and relays events',
    });
    props.table.grantReadWriteData(this.broker);
    // Strands fallback runs the tools in-process (as the bound user) and keeps sessions in S3.
    props.mediaBucket.grantReadWrite(this.broker);
    props.ingest.grantModels(this.broker);
    eventsApi.grantPublish(this.broker);
    this.broker.addToRolePolicy(new iam.PolicyStatement({
      actions: ['bedrock-mantle:CreateAgentSession', 'bedrock-mantle:GetAgentSession', 'bedrock-mantle:ListAgentSessions',
        'bedrock-mantle:CreateAgentSessionEvent', 'bedrock-mantle:ListAgentSessionEvents',
        'bedrock-mantle:ListAgentSessionItems', 'bedrock-mantle:DeleteAgentSession', 'bedrock-mantle:CallWithBearerToken'],
      resources: ['*'],
    }));
    this.broker.addToRolePolicy(new iam.PolicyStatement({
      actions: ['iam:PassRole'], resources: [sessionRole.roleArn],
      conditions: { StringEquals: { 'iam:PassedToService': 'bedrock-mantle.amazonaws.com' } },
    }));

    // ---------------------------------------------------------------- generation mode (-c mode=full)
    this.commonEnv = common;
    this.eventsApi = eventsApi;
    if (props.mode === 'full') {
      this.generation = new GenerationWiring(this, 'Generation', props.prefix);
      for (const fn of [tools, this.broker]) this.generation.wire(fn);
    }

    new CfnOutput(this, 'RuntimeArn', { value: runtimeArn });
    new CfnOutput(this, 'ToolsApiUrl', { value: toolsApi.apiEndpoint });
    new CfnOutput(this, 'EventsRealtimeDomain', { value: this.eventsRealtimeDomain });
    new CfnOutput(this, 'EventsHttpDomain', { value: this.eventsHttpDomain });
  }
}
