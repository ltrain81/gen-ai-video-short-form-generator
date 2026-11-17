import boto3
import pytest
from moto import mock_aws

from shorts import projects, storage
from shorts.api import provision_user
from shorts.auth import load_principal
from shorts.http import Conflict


def test_versioned_save_and_lock(table):
    with mock_aws():
        storage.s3.cache_clear()
        boto3.client("s3").create_bucket(Bucket="media")
        provision_user("alice", "a@x", "t1", "member")
        alice = load_principal("alice")
        item = projects.create(alice, "My edit", "9:16")
        project, role = projects.require_project(alice, item["projectId"], "edit")
        assert role == "owner"
        tl = projects.load_timeline(project)
        assert tl["settings"]["width"] == 1080 and tl["settings"]["height"] == 1920
        tl["meta"]["title"] = "v1"
        assert projects.save(alice, project, tl, 0, "user")["currentVersion"] == 1
        with pytest.raises(Conflict):  # stale base version
            projects.save(alice, project, tl, 0, "user")
        project = projects.require_project(alice, item["projectId"], "edit")[0]
        assert projects.acquire_lock(project, "agent:s1")
        assert not projects.acquire_lock(project, "agent:s2")
        project = projects.require_project(alice, item["projectId"], "edit")[0]
        with pytest.raises(Conflict):  # user save while agent holds the lock
            projects.save(alice, project, tl, 1, "user")
        projects.save(alice, project, tl, 1, "agent", holder="agent:s1")
        projects.release_lock(item["projectId"], "agent:s1")
        assert [v["version"] for v in projects.versions(item["projectId"])] == [2, 1, 0]


def test_folders_hierarchy_and_duplicate(table):
    with mock_aws():
        storage.s3.cache_clear()
        boto3.client("s3").create_bucket(Bucket="media")
        provision_user("bob", "b@x", "t2", "member")
        bob = load_principal("bob")
        top = projects.save_folder(bob, {"name": "Clients"})
        sub = projects.save_folder(bob, {"name": "AnyCompany", "parentId": top["folderId"]})
        with pytest.raises(ValueError):  # no cycles
            projects.save_folder(bob, {"parentId": sub["folderId"]}, top["folderId"])
        item = projects.create(bob, "Promo", "9:16")
        from shorts import ddb
        ddb.update(f"PROJ#{item['projectId']}", "META", {"folderId": sub["folderId"]})
        project = projects.require_project(bob, item["projectId"], "edit")[0]
        tl = projects.load_timeline(project)
        tl["meta"]["title"] = "v1"
        projects.save(bob, project, tl, 0, "user")
        project = projects.require_project(bob, item["projectId"], "edit")[0]
        dup = projects.duplicate(bob, project, "Promo 16:9")
        d = projects.require_project(bob, dup["projectId"], "edit")[0]
        assert d["folderId"] == sub["folderId"] and d["duplicatedFrom"]["projectId"] == item["projectId"]
        assert projects.load_timeline(d)["meta"]["title"] == "v1"
        # deleting a folder moves its contents up, never deletes projects
        projects.delete_folder(bob, sub["folderId"])
        assert projects.require_project(bob, item["projectId"], "view")[0]["folderId"] == top["folderId"]
        assert [f["folderId"] for f in projects.folders(bob.tenant_id)] == [top["folderId"]]
        listed = {p["projectId"]: p for p in projects.list_for(bob)}
        assert "folderId" in listed[item["projectId"]] and len(listed) == 2
