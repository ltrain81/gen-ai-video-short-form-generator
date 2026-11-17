import { Duration, Stack } from 'aws-cdk-lib';
import * as lambda from 'aws-cdk-lib/aws-lambda';
import * as logs from 'aws-cdk-lib/aws-logs';
import { Construct } from 'constructs';
import * as path from 'path';

export const SERVICES_DIR = path.join(__dirname, '..', '..', 'services');

const cachedCode = new WeakMap<Stack, Map<string, lambda.Code>>();

/**
 * One asset per stack (and package set) for Python Lambdas: the `shorts` package + pinned boto3.
 * `extra` adds pip packages (e.g. openai, imageio-ffmpeg for agent functions).
 */
export function servicesCode(scope: Construct, extra: string[] = []): lambda.Code {
  const stack = Stack.of(scope);
  const key = extra.join(' ');
  const perStack = cachedCode.get(stack) ?? new Map<string, lambda.Code>();
  cachedCode.set(stack, perStack);
  const existing = perStack.get(key);
  if (existing) return existing;
  const pkgs = ['"boto3>=1.40"', ...extra.map((e) => `"${e}"`)].join(' ');
  const code = lambda.Code.fromAsset(SERVICES_DIR, {
    exclude: ['.venv', 'tests', '**/__pycache__', '.pytest_cache', 'media-worker', 'uv.lock'],
    bundling: {
      image: lambda.Runtime.PYTHON_3_13.bundlingImage,
      platform: 'linux/arm64',
      command: ['bash', '-c', `pip install --no-cache-dir ${pkgs} -t /asset-output -q && cp -r src/shorts /asset-output/`],
    },
  });
  perStack.set(key, code);
  return code;
}

export interface PyFnProps {
  handler: string;
  environment?: Record<string, string>;
  timeout?: Duration;
  memorySize?: number;
  reservedConcurrentExecutions?: number;
  description?: string;
  extraPackages?: string[];
}

export function pyFunction(scope: Construct, id: string, props: PyFnProps): lambda.Function {
  return new lambda.Function(scope, id, {
    runtime: lambda.Runtime.PYTHON_3_13,
    architecture: lambda.Architecture.ARM_64,
    code: servicesCode(scope, props.extraPackages),
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
