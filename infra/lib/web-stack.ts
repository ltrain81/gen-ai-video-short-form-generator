import { CfnOutput, Duration, RemovalPolicy, Stack, StackProps } from 'aws-cdk-lib';
import * as cloudfront from 'aws-cdk-lib/aws-cloudfront';
import * as origins from 'aws-cdk-lib/aws-cloudfront-origins';
import * as s3 from 'aws-cdk-lib/aws-s3';
import * as s3deploy from 'aws-cdk-lib/aws-s3-deployment';
import { Construct } from 'constructs';
import * as fs from 'fs';
import * as path from 'path';

/**
 * Static SPA hosting: private S3 bucket behind CloudFront (OAC).
 * The SPA shell is public by design (it is the login page); all data is behind the Cognito-gated API.
 */
export class WebStack extends Stack {
  readonly distribution: cloudfront.Distribution;
  readonly siteBucket: s3.Bucket;
  readonly origin: string;

  constructor(scope: Construct, id: string, props?: StackProps) {
    super(scope, id, props);
    this.siteBucket = new s3.Bucket(this, 'Site', {
      blockPublicAccess: s3.BlockPublicAccess.BLOCK_ALL,
      encryption: s3.BucketEncryption.S3_MANAGED,
      enforceSSL: true,
      removalPolicy: RemovalPolicy.DESTROY,
      autoDeleteObjects: true,
    });
    const headers = new cloudfront.ResponseHeadersPolicy(this, 'Headers', {
      securityHeadersBehavior: {
        strictTransportSecurity: { accessControlMaxAge: Duration.days(365), includeSubdomains: true, override: true },
        contentTypeOptions: { override: true },
        frameOptions: { frameOption: cloudfront.HeadersFrameOption.DENY, override: true },
        referrerPolicy: { referrerPolicy: cloudfront.HeadersReferrerPolicy.STRICT_ORIGIN_WHEN_CROSS_ORIGIN, override: true },
      },
    });
    this.distribution = new cloudfront.Distribution(this, 'Cdn', {
      defaultBehavior: {
        origin: origins.S3BucketOrigin.withOriginAccessControl(this.siteBucket),
        viewerProtocolPolicy: cloudfront.ViewerProtocolPolicy.REDIRECT_TO_HTTPS,
        responseHeadersPolicy: headers,
        cachePolicy: cloudfront.CachePolicy.CACHING_OPTIMIZED,
      },
      defaultRootObject: 'index.html',
      errorResponses: [403, 404].map((code) => ({
        httpStatus: code, responseHttpStatus: 200, responsePagePath: '/index.html', ttl: Duration.seconds(0),
      })),
      minimumProtocolVersion: cloudfront.SecurityPolicyProtocol.TLS_V1_2_2021,
    });
    this.origin = `https://${this.distribution.distributionDomainName}`;
    new CfnOutput(this, 'WebUrl', { value: this.origin });
  }
}

export interface WebDeployStackProps extends StackProps {
  web: WebStack;
  runtimeConfig: Record<string, string>;
}

/** Uploads the built SPA + runtime config.json (API URL, Cognito ids). Requires `pnpm --filter web build`. */
export class WebDeployStack extends Stack {
  constructor(scope: Construct, id: string, props: WebDeployStackProps) {
    super(scope, id, props);
    const dist = path.join(__dirname, '..', '..', 'web', 'dist');
    const sources = [s3deploy.Source.jsonData('config.json', props.runtimeConfig)];
    if (fs.existsSync(path.join(dist, 'index.html'))) sources.unshift(s3deploy.Source.asset(dist));
    new s3deploy.BucketDeployment(this, 'Deploy', {
      destinationBucket: props.web.siteBucket,
      sources,
      distribution: props.web.distribution,
      distributionPaths: ['/index.html', '/config.json'],
      prune: false,
      memoryLimit: 512,
    });
  }
}
