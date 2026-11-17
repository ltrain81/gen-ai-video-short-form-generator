import { CfnOutput, Duration, Stack, StackProps } from 'aws-cdk-lib';
import * as apigw from 'aws-cdk-lib/aws-apigatewayv2';
import * as authorizers from 'aws-cdk-lib/aws-apigatewayv2-authorizers';
import * as integrations from 'aws-cdk-lib/aws-apigatewayv2-integrations';
import * as cognito from 'aws-cdk-lib/aws-cognito';
import * as dynamodb from 'aws-cdk-lib/aws-dynamodb';
import * as iam from 'aws-cdk-lib/aws-iam';
import * as logs from 'aws-cdk-lib/aws-logs';
import * as s3 from 'aws-cdk-lib/aws-s3';
import { Construct } from 'constructs';
import { AgentStack } from './agent-stack';
import { IngestStack } from './ingest-stack';
import { pyFunction } from './python';

export interface ApiStackProps extends StackProps {
  mediaBucket: s3.IBucket;
  table: dynamodb.ITableV2;
  userPool: cognito.IUserPool;
  userPoolClient: cognito.IUserPoolClient;
  ingest: IngestStack;
  agent: AgentStack;
  allowedOrigins: string[];
  mode: string;
}

/** REST API. Every route requires a verified Cognito JWT; per-action ACL is enforced in the handler. */
export class ApiStack extends Stack {
  readonly url: string;

  constructor(scope: Construct, id: string, props: ApiStackProps) {
    super(scope, id, props);
    const fn = pyFunction(this, 'Api', {
      handler: 'shorts.handlers.api.handler',
      // The API never launches the media worker; keep its (frequently changing) task-def ARN out of cross-stack refs.
      environment: {
        ...props.ingest.sharedEnv,
        USER_POOL_ID: props.userPool.userPoolId,
        DEPLOY_MODE: props.mode,
        BROKER_FUNCTION: props.agent.broker.functionArn,
        EVENTS_HTTP_DOMAIN: props.agent.eventsHttpDomain,
      },
      timeout: Duration.seconds(29),
      memorySize: 1024,
      description: 'Archive / sharing / search API',
    });
    props.table.grantReadWriteData(fn);
    props.mediaBucket.grantReadWrite(fn);
    props.ingest.grantModels(fn);
    props.agent.broker.grantInvoke(fn);
    props.agent.generation?.wire(fn);
    // Re-analysis / language layer dispatch from the API.
    props.ingest.modelQueue.grantSendMessages(fn);
    props.ingest.ingestQueue.grantSendMessages(fn);
    fn.addToRolePolicy(new iam.PolicyStatement({
      actions: ['cognito-idp:AdminCreateUser'],
      resources: [props.userPool.userPoolArn],
    }));

    const authorizer = new authorizers.HttpJwtAuthorizer('Cognito',
      `https://cognito-idp.${this.region}.amazonaws.com/${props.userPool.userPoolId}`,
      { jwtAudience: [props.userPoolClient.userPoolClientId] });

    const api = new apigw.HttpApi(this, 'Http', {
      corsPreflight: {
        allowOrigins: props.allowedOrigins,
        allowMethods: [apigw.CorsHttpMethod.ANY],
        allowHeaders: ['authorization', 'content-type'],
        maxAge: Duration.hours(1),
      },
      defaultAuthorizer: authorizer,
      createDefaultStage: false,
    });
    const accessLogs = new logs.LogGroup(this, 'AccessLogs', { retention: logs.RetentionDays.ONE_MONTH });
    new apigw.HttpStage(this, 'Default', {
      httpApi: api, stageName: '$default', autoDeploy: true,
      throttle: { rateLimit: 200, burstLimit: 400 },
      accessLogSettings: { destination: new apigw.LogGroupLogDestination(accessLogs) },
    });
    api.addRoutes({
      path: '/{proxy+}',
      methods: [apigw.HttpMethod.GET, apigw.HttpMethod.POST, apigw.HttpMethod.PUT, apigw.HttpMethod.PATCH, apigw.HttpMethod.DELETE],
      integration: new integrations.HttpLambdaIntegration('Lambda', fn),
    });
    this.url = api.apiEndpoint;
    new CfnOutput(this, 'ApiUrl', { value: this.url });
  }
}
