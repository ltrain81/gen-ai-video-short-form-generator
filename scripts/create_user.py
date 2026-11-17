"""Create a user (invite-only pools) and provision it into a workspace.

  uv run --project services python scripts/create_user.py --email you@example.com --tenant-name "My team" --language ko
  uv run --project services python scripts/create_user.py --email teammate@example.com --tenant-id t123 --role member

Without --tenant-id a new workspace is created and the user becomes its admin.
Cognito emails a temporary password (or pass --password to set a permanent one for tests).
Reads stack outputs via CloudFormation; needs admin credentials for the target account.
"""
import argparse
import os
import sys
import uuid

import boto3

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "services", "src"))


def output(cfn, stack, key):
    outs = cfn.describe_stacks(StackName=stack)["Stacks"][0]["Outputs"]
    return next(o["OutputValue"] for o in outs if o["OutputKey"] == key)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--email", required=True)
    ap.add_argument("--tenant-id")
    ap.add_argument("--tenant-name", default="My workspace")
    ap.add_argument("--language", default="ko")
    ap.add_argument("--role", default="admin", choices=["admin", "member"])
    ap.add_argument("--password", help="Set a permanent password (tests only)")
    ap.add_argument("--prefix", default="Shorts")
    ap.add_argument("--region", default=os.environ.get("AWS_REGION", "us-east-1"))
    a = ap.parse_args()
    os.environ["AWS_DEFAULT_REGION"] = os.environ["AWS_REGION"] = a.region
    cfn = boto3.client("cloudformation")
    os.environ["TABLE_NAME"] = output(cfn, f"{a.prefix}-Core", "TableName")
    os.environ.setdefault("MEDIA_BUCKET", output(cfn, f"{a.prefix}-Core", "MediaBucketName"))
    os.environ.setdefault("VECTOR_BUCKET", f"{a.prefix.lower()}-vectors-{boto3.client('sts').get_caller_identity()['Account']}")
    pool = output(cfn, f"{a.prefix}-Core", "UserPoolId")
    from shorts import ddb, vectors
    from shorts.api import provision_user

    idp = boto3.client("cognito-idp")
    try:
        u = idp.admin_create_user(UserPoolId=pool, Username=a.email, DesiredDeliveryMediums=["EMAIL"],
                                  UserAttributes=[{"Name": "email", "Value": a.email},
                                                  {"Name": "email_verified", "Value": "true"}],
                                  **({"MessageAction": "SUPPRESS"} if a.password else {}))["User"]
    except idp.exceptions.UsernameExistsException:
        u = idp.admin_get_user(UserPoolId=pool, Username=a.email)
        u["Attributes"] = u["UserAttributes"]
    sub = next(x["Value"] for x in u["Attributes"] if x["Name"] == "sub")
    if a.password:
        idp.admin_set_user_password(UserPoolId=pool, Username=a.email, Password=a.password, Permanent=True)
    if ddb.get(f"USER#{sub}", "PROFILE"):
        print(f"{a.email} already provisioned ({sub})")
        return
    tenant = a.tenant_id
    if not tenant:
        tenant = "t" + uuid.uuid4().hex[:16]
        ddb.put({"PK": f"TENANT#{tenant}", "SK": "META", "tenantId": tenant, "name": a.tenant_name,
                 "language": a.language, "createdAt": ddb.now_ms(), "createdBy": sub})
        vectors.ensure_index(tenant)
    provision_user(sub, a.email, tenant, a.role if a.tenant_id else "admin")
    print(f"created {a.email} sub={sub} tenant={tenant}")


if __name__ == "__main__":
    main()
