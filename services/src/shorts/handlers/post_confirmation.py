"""Cognito PostConfirmation trigger.

Invited users are already provisioned (POST /users/invite). Self sign-ups get their own tenant
and become its admin. Federated (OIDC/SAML) users follow the same rule; map them to an existing
tenant by pre-provisioning or an admin invite.
"""
import uuid

from shorts import ddb, ingest, vectors
from shorts.api import provision_user


def handler(event, _context):
    attrs = event["request"]["userAttributes"]
    sub, email = attrs["sub"], attrs.get("email", "")
    if ddb.get(f"USER#{sub}", "PROFILE"):
        return event
    tenant_id = "t" + uuid.uuid4().hex[:16]
    name = attrs.get("custom:tenantName") or (email.split("@")[-1] if email else "My workspace")
    language = attrs.get("custom:tenantLanguage", ingest.DEFAULT_LANGUAGE)
    if language not in ingest.LANGUAGES:
        language = ingest.DEFAULT_LANGUAGE
    ddb.put({"PK": f"TENANT#{tenant_id}", "SK": "META", "tenantId": tenant_id, "name": name,
             "language": language, "createdAt": ddb.now_ms(), "createdBy": sub})
    provision_user(sub, email, tenant_id, "admin", attrs.get("name", ""))
    vectors.ensure_index(tenant_id)
    return event
