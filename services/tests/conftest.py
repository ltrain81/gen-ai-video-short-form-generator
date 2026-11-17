import os

import boto3
import pytest
from moto import mock_aws

os.environ.update(AWS_DEFAULT_REGION="us-east-1", AWS_REGION="us-east-1", TABLE_NAME="Main", MEDIA_BUCKET="media",
                  VECTOR_BUCKET="vec", ACCOUNT_ID="123456789012", AWS_ACCESS_KEY_ID="x", AWS_SECRET_ACCESS_KEY="x")


@pytest.fixture
def table():
    with mock_aws():
        from shorts import ddb
        ddb.table.cache_clear()
        ddb_res = boto3.resource("dynamodb")
        gsis = [{"IndexName": n, "KeySchema": [{"AttributeName": f"{n}PK", "KeyType": "HASH"},
                                               {"AttributeName": f"{n}SK", "KeyType": "RANGE"}],
                 "Projection": {"ProjectionType": "ALL"}} for n in ("GSI1", "GSI2", "GSI3")]
        attrs = [{"AttributeName": a, "AttributeType": "S"} for a in
                 ["PK", "SK", "GSI1PK", "GSI1SK", "GSI2PK", "GSI2SK", "GSI3PK", "GSI3SK"]]
        ddb_res.create_table(TableName="Main", BillingMode="PAY_PER_REQUEST", AttributeDefinitions=attrs,
                             KeySchema=[{"AttributeName": "PK", "KeyType": "HASH"},
                                        {"AttributeName": "SK", "KeyType": "RANGE"}],
                             GlobalSecondaryIndexes=gsis)
        yield ddb.table()
        ddb.table.cache_clear()
