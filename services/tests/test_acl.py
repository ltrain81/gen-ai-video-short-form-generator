import pytest

from shorts import acl, ddb
from shorts.api import provision_user
from shorts.auth import Forbidden, load_principal


def _asset(aid, tenant, owner, collections=()):
    ddb.put({"PK": f"ASSET#{aid}", "SK": "META", "assetId": aid, "tenantId": tenant, "ownerId": owner,
             "collectionIds": list(collections), "GSI2PK": f"TENANT#{tenant}", "GSI2SK": f"ASSET#1#{aid}"})
    acl.grant(f"ASSET#{aid}", f"USER#{owner}", "owner", tenant, owner)
    return ddb.get(f"ASSET#{aid}", "META")


def test_roles_groups_collections_and_isolation(table):
    provision_user("alice", "a@x", "t1", "member")
    provision_user("bob", "b@x", "t1", "member")
    provision_user("carol", "c@x", "t1", "admin")
    provision_user("mallory", "m@x", "t2", "admin")
    ddb.put({"PK": "TENANT#t1", "SK": "GROUP#g1", "name": "video"})
    ddb.put({"PK": "GROUP#g1", "SK": "MEMBER#bob", "GSI1PK": "USER#bob", "GSI1SK": "GROUP#g1"})

    a1 = _asset("a1", "t1", "alice")
    a2 = _asset("a2", "t1", "alice", ["c1"])
    alice, bob, carol, mallory = (load_principal(x) for x in ("alice", "bob", "carol", "mallory"))

    assert acl.effective_role(alice, a1) == "owner"
    assert acl.effective_role(bob, a1) is None
    assert acl.effective_role(carol, a1) == "owner"          # tenant admin
    assert acl.effective_role(mallory, a1) is None           # other tenant admin

    acl.grant("ASSET#a1", "GROUP#g1", "user", "t1", "alice")
    assert acl.effective_role(bob, a1) == "user"
    with pytest.raises(Forbidden):
        acl.require(bob, a1, "download")                      # user role cannot download originals
    acl.require(bob, a1, "use")

    ddb.put({"PK": "COLL#c1", "SK": "ASSET#a2"})
    acl.grant("COLL#c1", "USER#bob", "editor", "t1", "alice")
    assert acl.effective_role(bob, a2) == "editor"
    assert acl.accessible_asset_ids(bob) == {"a1": "user", "a2": "editor"}

    with pytest.raises(Forbidden):
        acl.grant("ASSET#a1", "USER#mallory", "user", "t1", "alice")   # cross-tenant
    with pytest.raises(acl.NotFound):
        acl.require(mallory, a1, "view")                                # invisible

    acl.grant("ASSET#a1", "USER#bob", "editor", "t1", "alice", expires_at=1)  # expired
    assert acl.effective_role(bob, a1) == "user"
