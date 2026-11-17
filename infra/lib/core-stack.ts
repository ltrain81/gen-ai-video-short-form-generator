import { CfnOutput, Duration, RemovalPolicy, Stack, StackProps } from 'aws-cdk-lib';
import * as cognito from 'aws-cdk-lib/aws-cognito';
import * as dynamodb from 'aws-cdk-lib/aws-dynamodb';
import * as s3 from 'aws-cdk-lib/aws-s3';
import * as s3vectors from 'aws-cdk-lib/aws-s3vectors';
import * as iam from 'aws-cdk-lib/aws-iam';
import { Construct } from 'constructs';
import { pyFunction } from './python';

export interface CoreStackProps extends StackProps {
  prefix: string;
  /** Origins allowed to PUT presigned upload parts (web app + local dev). */
  uploadOrigins: string[];
}

/** Durable data + identity: media bucket, single table, vector bucket, Cognito. */
export class CoreStack extends Stack {
  readonly mediaBucket: s3.Bucket;
  readonly table: dynamodb.TableV2;
  readonly vectorBucketName: string;
  readonly vectorBucketArn: string;
  readonly userPool: cognito.UserPool;
  readonly userPoolClient: cognito.UserPoolClient;

  constructor(scope: Construct, id: string, props: CoreStackProps) {
    super(scope, id, props);
    const p = props.prefix.toLowerCase();

    const accessLogs = new s3.Bucket(this, 'AccessLogs', {
      blockPublicAccess: s3.BlockPublicAccess.BLOCK_ALL,
      encryption: s3.BucketEncryption.S3_MANAGED,
      enforceSSL: true,
      objectOwnership: s3.ObjectOwnership.BUCKET_OWNER_PREFERRED,
      lifecycleRules: [{ expiration: Duration.days(90) }],
      removalPolicy: RemovalPolicy.RETAIN,
    });

    // Private media bucket. Browser access only through short-lived presigned URLs issued after ACL checks.
    this.mediaBucket = new s3.Bucket(this, 'Media', {
      blockPublicAccess: s3.BlockPublicAccess.BLOCK_ALL,
      encryption: s3.BucketEncryption.S3_MANAGED,
      enforceSSL: true,
      eventBridgeEnabled: true,
      serverAccessLogsBucket: accessLogs,
      serverAccessLogsPrefix: 'media/',
      removalPolicy: RemovalPolicy.RETAIN,
      cors: [
        {
          allowedMethods: [s3.HttpMethods.GET, s3.HttpMethods.PUT, s3.HttpMethods.HEAD],
          allowedOrigins: props.uploadOrigins,
          allowedHeaders: ['*'],
          exposedHeaders: ['ETag', 'Content-Length', 'Content-Range', 'Accept-Ranges'],
          maxAge: 3600,
        },
      ],
      lifecycleRules: [
        { abortIncompleteMultipartUploadAfter: Duration.days(7) },
        { prefix: 'tmp/', expiration: Duration.days(3) },
      ],
    });

    this.table = new dynamodb.TableV2(this, 'Main', {
      partitionKey: { name: 'PK', type: dynamodb.AttributeType.STRING },
      sortKey: { name: 'SK', type: dynamodb.AttributeType.STRING },
      billing: dynamodb.Billing.onDemand(),
      pointInTimeRecoverySpecification: { pointInTimeRecoveryEnabled: true },
      deletionProtection: true,
      removalPolicy: RemovalPolicy.RETAIN,
      timeToLiveAttribute: 'ttl',
      dynamoStream: dynamodb.StreamViewType.NEW_AND_OLD_IMAGES,
      globalSecondaryIndexes: ['GSI1', 'GSI2', 'GSI3'].map((n) => ({
        indexName: n,
        partitionKey: { name: `${n}PK`, type: dynamodb.AttributeType.STRING },
        sortKey: { name: `${n}SK`, type: dynamodb.AttributeType.STRING },
      })),
    });

    const vectorBucket = new s3vectors.CfnVectorBucket(this, 'Vectors', {
      vectorBucketName: `${p}-vectors-${this.account}`,
    });
    vectorBucket.applyRemovalPolicy(RemovalPolicy.RETAIN);
    this.vectorBucketName = vectorBucket.vectorBucketName!;
    this.vectorBucketArn = vectorBucket.attrVectorBucketArn;

    this.userPool = new cognito.UserPool(this, 'Users', {
      selfSignUpEnabled: true,
      signInAliases: { email: true },
      autoVerify: { email: true },
      standardAttributes: { email: { required: true, mutable: true }, fullname: { required: false, mutable: true } },
      customAttributes: {
        tenantName: new cognito.StringAttribute({ mutable: true, maxLen: 128 }),
        tenantLanguage: new cognito.StringAttribute({ mutable: true, maxLen: 8 }),
      },
      passwordPolicy: { minLength: 12, requireSymbols: false },
      mfa: cognito.Mfa.OPTIONAL,
      mfaSecondFactor: { sms: false, otp: true },
      accountRecovery: cognito.AccountRecovery.EMAIL_ONLY,
      featurePlan: cognito.FeaturePlan.ESSENTIALS,
      removalPolicy: RemovalPolicy.RETAIN,
      deletionProtection: true,
    });
    // Managed login domain enables OIDC/SAML federation (add identity providers per deployment).
    const domain = this.userPool.addDomain('Domain', { cognitoDomain: { domainPrefix: `${p}-${this.account}` } });

    this.userPoolClient = this.userPool.addClient('Web', {
      // adminUserPassword: server-side only (needs IAM admin creds) — used by e2e tests.
      authFlows: { userSrp: true, adminUserPassword: true },
      preventUserExistenceErrors: true,
      generateSecret: false,
      accessTokenValidity: Duration.hours(1),
      idTokenValidity: Duration.hours(1),
      refreshTokenValidity: Duration.days(30),
      oAuth: {
        flows: { authorizationCodeGrant: true },
        scopes: [cognito.OAuthScope.OPENID, cognito.OAuthScope.EMAIL, cognito.OAuthScope.PROFILE],
        callbackUrls: props.uploadOrigins.map((o) => `${o}/`),
        logoutUrls: props.uploadOrigins.map((o) => `${o}/`),
      },
    });

    // Self sign-up → personal tenant (admin). Invited users are pre-provisioned by the API.
    const postConfirmation = pyFunction(this, 'PostConfirmation', {
      handler: 'shorts.handlers.post_confirmation.handler',
      environment: {
        TABLE_NAME: this.table.tableName,
        VECTOR_BUCKET: this.vectorBucketName,
        MEDIA_BUCKET: this.mediaBucket.bucketName,
        ACCOUNT_ID: this.account,
      },
      description: 'Cognito post-confirmation: provision tenant/user',
    });
    this.table.grantReadWriteData(postConfirmation);
    postConfirmation.addToRolePolicy(new iam.PolicyStatement({
      actions: ['s3vectors:GetIndex', 's3vectors:CreateIndex'],
      resources: [this.vectorBucketArn, `${this.vectorBucketArn}/index/*`],
    }));
    this.userPool.addTrigger(cognito.UserPoolOperation.POST_CONFIRMATION, postConfirmation);

    new CfnOutput(this, 'UserPoolId', { value: this.userPool.userPoolId });
    new CfnOutput(this, 'UserPoolClientId', { value: this.userPoolClient.userPoolClientId });
    new CfnOutput(this, 'CognitoDomain', { value: domain.baseUrl() });
    new CfnOutput(this, 'MediaBucketName', { value: this.mediaBucket.bucketName });
    new CfnOutput(this, 'TableName', { value: this.table.tableName });
  }
}
