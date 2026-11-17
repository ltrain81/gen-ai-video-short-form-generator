import { Duration, Stack } from 'aws-cdk-lib';
import * as lambda from 'aws-cdk-lib/aws-lambda';
import * as logs from 'aws-cdk-lib/aws-logs';
import { Construct } from 'constructs';
import * as path from 'path';

export const SERVICES_DIR = path.join(__dirname, '..', '..', 'services');

const cachedCode = new WeakMap<Stack, lambda.Code>();

/** One asset per stack for every Python Lambda: the `shorts` package + pinned boto3 (S3 Vectors, Bedrock async APIs). */
export function servicesCode(scope: Construct): lambda.Code {
  const stack = Stack.of(scope);
  const existing = cachedCode.get(stack);
  if (existing) return existing;
  const code = lambda.Code.fromAsset(SERVICES_DIR, {
    exclude: ['.venv', 'tests', '**/__pycache__', '.pytest_cache', 'media-worker', 'uv.lock'],
    bundling: {
      image: lambda.Runtime.PYTHON_3_13.bundlingImage,
      platform: 'linux/arm64',
      command: ['bash', '-c', 'pip install --no-cache-dir "boto3>=1.40" -t /asset-output -q && cp -r src/shorts /asset-output/'],
    },
  });
  cachedCode.set(stack, code);
  return code;
}

export interface PyFnProps {
  handler: string;
  environment?: Record<string, string>;
  timeout?: Duration;
  memorySize?: number;
  reservedConcurrentExecutions?: number;
  description?: string;
}

export function pyFunction(scope: Construct, id: string, props: PyFnProps): lambda.Function {
  return new lambda.Function(scope, id, {
    runtime: lambda.Runtime.PYTHON_3_13,
    architecture: lambda.Architecture.ARM_64,
    code: servicesCode(scope),
    handler: props.handler,
    timeout: props.timeout ?? Duration.seconds(30),
    memorySize: props.memorySize ?? 512,
    environment: props.environment,
    reservedConcurrentExecutions: props.reservedConcurrentExecutions,
    description: props.description,
    tracing: lambda.Tracing.ACTIVE,
    logGroup: new logs.LogGroup(scope, `${id}Logs`, { retention: logs.RetentionDays.ONE_MONTH }),
  });
}
