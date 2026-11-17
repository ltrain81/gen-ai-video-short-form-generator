#!/usr/bin/env node
import { App } from 'aws-cdk-lib';
import { AgentStack } from '../lib/agent-stack';
import { ApiStack } from '../lib/api-stack';
import { CoreStack } from '../lib/core-stack';
import { GenerationStack } from '../lib/generation';
import { pyFunction } from '../lib/python';
import { Duration } from 'aws-cdk-lib';
import { Construct } from 'constructs';
import { IngestStack } from '../lib/ingest-stack';
import { WebDeployStack, WebStack } from '../lib/web-stack';

const app = new App();
const prefix: string = app.node.tryGetContext('prefix') ?? 'Shorts';
const mode: string = app.node.tryGetContext('mode') ?? 'edit'; // edit | full
if (!['edit', 'full'].includes(mode)) throw new Error(`mode must be edit|full, got ${mode}`);
// Invite-only by default; opt in with -c selfSignUp=true (each sign-up then gets its own workspace).
const selfSignUp = String(app.node.tryGetContext('selfSignUp') ?? 'false') === 'true';
const localOrigins: string[] = app.node.tryGetContext('allowedLocalOrigins') ?? [];
const env = { account: process.env.CDK_DEFAULT_ACCOUNT, region: process.env.CDK_DEFAULT_REGION ?? 'us-east-1' };

const web = new WebStack(app, `${prefix}-Web`, { env });
const origins = [web.origin, ...localOrigins];
const core = new CoreStack(app, `${prefix}-Core`, { env, prefix, uploadOrigins: origins, selfSignUp });
const ingest = new IngestStack(app, `${prefix}-Ingest`, {
  env,
  mediaBucket: core.mediaBucket,
  table: core.table,
  vectorBucketName: core.vectorBucketName,
  vectorBucketArn: core.vectorBucketArn,
});
const agent = new AgentStack(app, `${prefix}-Agent`, {
  env, prefix, mediaBucket: core.mediaBucket, table: core.table, userPool: core.userPool, ingest,
  bmaModelId: app.node.tryGetContext('bmaModelId') ?? 'openai.gpt-5.6-luna',
  mode,
});
if (mode === 'full' && agent.generation) {
  new GenerationStack(app, `${prefix}-Generation`, {
    env, mediaBucket: core.mediaBucket, wiring: agent.generation,
    poller: (scope: Construct) => {
      const fn = pyFunction(scope, 'GenPoller', {
        handler: 'shorts.handlers.gen_poller.handler', environment: { ...agent.commonEnv, DEPLOY_MODE: mode },
        timeout: Duration.minutes(2), memorySize: 1024, description: 'Polls generation outputs and registers assets',
      });
      core.table.grantReadWriteData(fn);
      core.mediaBucket.grantReadWrite(fn);
      agent.eventsApi.grantPublish(fn);
      return fn;
    },
  });
}
const api = new ApiStack(app, `${prefix}-Api`, {
  env, mode, allowedOrigins: origins, mediaBucket: core.mediaBucket, table: core.table,
  userPool: core.userPool, userPoolClient: core.userPoolClient, ingest, agent,
});
new WebDeployStack(app, `${prefix}-WebDeploy`, {
  env,
  web,
  runtimeConfig: {
    apiUrl: api.url,
    region: env.region,
    userPoolId: core.userPool.userPoolId,
    userPoolClientId: core.userPoolClient.userPoolClientId,
    mode,
    selfSignUp: String(selfSignUp),
    eventsRealtimeDomain: agent.eventsRealtimeDomain,
    eventsHttpDomain: agent.eventsHttpDomain,
  },
});
