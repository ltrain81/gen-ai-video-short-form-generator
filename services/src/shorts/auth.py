"""Caller identity.

API Gateway (HTTP API) verifies the Cognito JWT before the Lambda runs; we only read
verified claims from the request context and then load tenant/group membership from
DynamoDB. A request without verified claims is rejected (fail closed).
"""
from __future__ import annotations

from dataclasses import dataclass, field

from . import ddb


class AuthError(Exception):
    status = 401


class Forbidden(Exception):
    status = 403


@dataclass(frozen=True)
class Principal:
    sub: str
    tenant_id: str
    email: str = ""
    tenant_role: str = "member"  # admin | member
    groups: tuple[str, ...] = field(default_factory=tuple)

    @property
    def is_tenant_admin(self) -> bool:
        return self.tenant_role == "admin"

    def principal_ids(self) -> list[str]:
        """All ACL principal ids this caller acts as."""
        return [f"USER#{self.sub}", f"TENANT#{self.tenant_id}", *(f"GROUP#{g}" for g in self.groups)]


def claims_from_event(event: dict) -> dict:
    try:
        claims = event["requestContext"]["authorizer"]["jwt"]["claims"]
    except (KeyError, TypeError):
        raise AuthError("Unauthenticated")
    if not claims.get("sub"):
        raise AuthError("Unauthenticated")
    return claims


def load_principal(sub: str) -> Principal:
    profile = ddb.get(f"USER#{sub}", "PROFILE")
    if not profile or not profile.get("tenantId"):
        raise Forbidden("User is not provisioned to a tenant")
    tenant_id = profile["tenantId"]
    member = ddb.get(f"TENANT#{tenant_id}", f"MEMBER#{sub}")
    if not member:
        raise Forbidden("User is not a member of the tenant")
    groups = tuple(
        item["GSI1SK"].removeprefix("GROUP#")
        for item in ddb.query(f"USER#{sub}", "GROUP#", index="GSI1")
    )
    return Principal(sub=sub, tenant_id=tenant_id, email=profile.get("email", ""),
                     tenant_role=member.get("role", "member"), groups=groups)


def principal_from_event(event: dict) -> Principal:
    return load_principal(claims_from_event(event)["sub"])
