"""Single-table DynamoDB access.

Key layout (PK / SK):
  USER#{sub}            PROFILE                 user profile (tenantId, email, name)
  TENANT#{t}            META                    tenant
  TENANT#{t}            MEMBER#{sub}            tenant membership (role: admin|member)
  TENANT#{t}            GROUP#{g}               group
  GROUP#{g}             MEMBER#{sub}            group membership  (GSI1: USER#{sub} / GROUP#{g})
  ASSET#{a}             META                    asset             (GSI2: TENANT#{t} / ASSET#{createdAt}#{a})
                                                                  (GSI3: TENANT#{t}#CONTENT#{ck} / ASSET#{a})
  ASSET#{a}|COLL#{c}    GRANT#{ptype}#{pid}     ACL grant         (GSI1: PRINCIPAL#{ptype}#{pid} / RES#{kind}#{id})
  ASSET#{a}             REQUEST#{sub}           access request    (GSI1: INBOX#{ownerSub} / REQUEST#{ts}#{a}#{sub})
  ASSET#{a}             INGEST                  ingest stage tracker
  COLL#{c}              META                    collection        (GSI2: TENANT#{t} / COLL#{c})
  COLL#{c}              ASSET#{a}               collection membership
  CONTENT#{ck}          META                    content-addressed analysis (dedupe across uploads)
  AUDIT#{t}#{yyyy-mm-dd} {ts}#{id}              audit log
"""
from __future__ import annotations

import time
from decimal import Decimal
from functools import lru_cache
from typing import Any, Iterable

import boto3
from boto3.dynamodb.conditions import Key

from .config import get_config


@lru_cache(maxsize=1)
def table():
    return boto3.resource("dynamodb").Table(get_config().table_name)


def now_ms() -> int:
    return int(time.time() * 1000)


def to_ddb(value: Any) -> Any:
    """Convert floats to Decimal recursively (DynamoDB does not accept float)."""
    if isinstance(value, float):
        return Decimal(str(value))
    if isinstance(value, dict):
        return {k: to_ddb(v) for k, v in value.items() if v is not None}
    if isinstance(value, (list, tuple)):
        return [to_ddb(v) for v in value]
    return value


def from_ddb(value: Any) -> Any:
    if isinstance(value, Decimal):
        return int(value) if value == value.to_integral_value() else float(value)
    if isinstance(value, dict):
        return {k: from_ddb(v) for k, v in value.items()}
    if isinstance(value, list):
        return [from_ddb(v) for v in value]
    return value


def get(pk: str, sk: str, consistent: bool = False) -> dict | None:
    item = table().get_item(Key={"PK": pk, "SK": sk}, ConsistentRead=consistent).get("Item")
    return from_ddb(item) if item else None


def put(item: dict, condition: str | None = None, **kwargs) -> None:
    args: dict[str, Any] = {"Item": to_ddb(item)}
    if condition:
        args["ConditionExpression"] = condition
    args.update(kwargs)
    table().put_item(**args)


def delete(pk: str, sk: str) -> None:
    table().delete_item(Key={"PK": pk, "SK": sk})


def update(pk: str, sk: str, fields: dict, condition: str | None = None, **kwargs) -> dict:
    """SET the given top-level fields. Returns the new item."""
    names, values, sets = {}, {}, []
    for i, (k, v) in enumerate(fields.items()):
        names[f"#f{i}"] = k
        values[f":v{i}"] = to_ddb(v)
        sets.append(f"#f{i} = :v{i}")
    args: dict[str, Any] = {
        "Key": {"PK": pk, "SK": sk},
        "UpdateExpression": "SET " + ", ".join(sets),
        "ExpressionAttributeNames": names,
        "ExpressionAttributeValues": values,
        "ReturnValues": "ALL_NEW",
    }
    if condition:
        args["ConditionExpression"] = condition
    for k, v in kwargs.pop("extra_names", {}).items():
        names[k] = v
    for k, v in kwargs.pop("extra_values", {}).items():
        values[k] = to_ddb(v)
    args.update(kwargs)
    return from_ddb(table().update_item(**args)["Attributes"])


def update_raw(pk: str, sk: str, expression: str, names: dict | None = None, values: dict | None = None,
               condition: str | None = None) -> dict:
    """Run an explicit UpdateExpression. Returns the new item."""
    args: dict[str, Any] = {"Key": {"PK": pk, "SK": sk}, "UpdateExpression": expression, "ReturnValues": "ALL_NEW"}
    if names:
        args["ExpressionAttributeNames"] = names
    if values:
        args["ExpressionAttributeValues"] = to_ddb(values)
    if condition:
        args["ConditionExpression"] = condition
    return from_ddb(table().update_item(**args)["Attributes"])


def conditional_failed(exc: Exception) -> bool:
    return getattr(exc, "response", {}).get("Error", {}).get("Code") == "ConditionalCheckFailedException"


def query(pk: str, sk_prefix: str | None = None, index: str | None = None, limit: int | None = None,
          forward: bool = True) -> list[dict]:
    pk_name, sk_name = ("PK", "SK") if index is None else (f"{index}PK", f"{index}SK")
    cond = Key(pk_name).eq(pk)
    if sk_prefix:
        cond = cond & Key(sk_name).begins_with(sk_prefix)
    args: dict[str, Any] = {"KeyConditionExpression": cond, "ScanIndexForward": forward}
    if index:
        args["IndexName"] = index
    items: list[dict] = []
    while True:
        if limit:
            args["Limit"] = limit - len(items)
        resp = table().query(**args)
        items.extend(from_ddb(i) for i in resp.get("Items", []))
        if "LastEvaluatedKey" not in resp or (limit and len(items) >= limit):
            return items
        args["ExclusiveStartKey"] = resp["LastEvaluatedKey"]


def batch_get(keys: Iterable[tuple[str, str]]) -> list[dict]:
    keys = list(dict.fromkeys(keys))
    out: list[dict] = []
    client = boto3.resource("dynamodb")
    name = get_config().table_name
    for i in range(0, len(keys), 100):
        request = {name: {"Keys": [{"PK": pk, "SK": sk} for pk, sk in keys[i:i + 100]]}}
        while request:
            resp = client.batch_get_item(RequestItems=request)
            out.extend(from_ddb(it) for it in resp["Responses"].get(name, []))
            request = resp.get("UnprocessedKeys") or None
    return out
