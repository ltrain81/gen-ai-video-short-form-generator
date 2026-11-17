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
