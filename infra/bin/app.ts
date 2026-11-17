#!/usr/bin/env node
import { App } from 'aws-cdk-lib';
import { ApiStack } from '../lib/api-stack';
import { CoreStack } from '../lib/core-stack';
import { IngestStack } from '../lib/ingest-stack';
import { WebDeployStack, WebStack } from '../lib/web-stack';

const app = new App();
const prefix: string = app.node.tryGetContext('prefix') ?? 'Shorts';
const mode: string = app.node.tryGetContext('mode') ?? 'edit'; // edit | full
if (!['edit', 'full'].includes(mode)) throw new Error(`mode must be edit|full, got ${mode}`);
const localOrigins: string[] = app.node.tryGetContext('allowedLocalOrigins') ?? [];
const env = { account: process.env.CDK_DEFAULT_ACCOUNT, region: process.env.CDK_DEFAULT_REGION ?? 'us-east-1' };

const web = new WebStack(app, `${prefix}-Web`, { env });
const origins = [web.origin, ...localOrigins];
const core = new CoreStack(app, `${prefix}-Core`, { env, prefix, uploadOrigins: origins });
const ingest = new IngestStack(app, `${prefix}-Ingest`, {
  env,
  mediaBucket: core.mediaBucket,
  table: core.table,
  vectorBucketName: core.vectorBucketName,
  vectorBucketArn: core.vectorBucketArn,
});
const api = new ApiStack(app, `${prefix}-Api`, {
  env, mode, allowedOrigins: origins, mediaBucket: core.mediaBucket, table: core.table,
  userPool: core.userPool, userPoolClient: core.userPoolClient, ingest,
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
  },
});
