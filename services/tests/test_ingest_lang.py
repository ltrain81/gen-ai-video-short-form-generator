"""Two tenants (ko, en) upload identical bytes: base analysis runs once, language layer once per language."""
from unittest import mock

import boto3
from moto import mock_aws

from shorts import ddb, ingest, storage


def _asset(aid, tenant):
    ddb.put({"PK": f"ASSET#{aid}", "SK": "META", "assetId": aid, "tenantId": tenant, "ownerId": "u",
             "status": "queued", "filename": "x.mp4"})


def test_dedupe_with_two_languages(table):
    with mock_aws():
        storage.s3.cache_clear()
        boto3.client("s3").create_bucket(Bucket="media")
        ddb.put({"PK": "TENANT#tko", "SK": "META", "language": "ko"})
        ddb.put({"PK": "TENANT#ten", "SK": "META", "language": "en"})
        _asset("a1", "tko")
        _asset("a2", "ten")
        tasks, workers = [], []
        with mock.patch.object(ingest, "send_model_task", tasks.append), \
             mock.patch.object(ingest, "run_media_worker", workers.append), \
             mock.patch.object(ingest, "content_key_from_s3", return_value=("ck1", 10)), \
             mock.patch.object(ingest.vectors, "put", return_value=3):
            ingest.on_object_created("media", "tenants/tko/assets/a1/original/x.mp4")
            ingest.on_object_created("media", "tenants/ten/assets/a2/original/x.mp4")
            assert len(workers) == 1  # second upload is deduped
            manifest = {"kind": "video", "durationSec": 20,
                        "chunks": [{"index": 0, "startSec": 0, "endSec": 20, "key": "k", "audioKey": "a"}]}
            ingest.on_prepared({"assetId": "a1", "contentKey": "ck1", "manifest": manifest})
            names = sorted((t["task"], t.get("lang")) for t in tasks)
            assert names == [("marengo.start", None), ("pegasus.segments", "en"), ("pegasus.segments", "ko"),
                             ("pegasus.summary", "en"), ("pegasus.summary", "ko"), ("transcribe.start", None)]
            for st in ("marengo:0", "transcribe:0"):
                ingest.on_stage_done("ck1", st)
            for lang in ("ko", "en"):
                ingest.on_stage_done("ck1", "pegasus.summary", lang=lang)
                ingest.on_stage_done("ck1", "pegasus.segments:0", lang=lang)
            assert sorted(t.get("lang") for t in tasks if t["task"] == "describe.embed") == ["en", "ko"]
            assert ddb.get("ASSET#a1", "META")["status"] == "processing"
            ingest.on_stage_done("ck1", "describe", lang="ko")
            assert ddb.get("ASSET#a1", "META")["status"] == "ready"
            assert ddb.get("ASSET#a2", "META")["status"] == "processing"
            ingest.on_stage_done("ck1", "describe", lang="en")
            a2 = ddb.get("ASSET#a2", "META")
            assert a2["status"] == "ready" and a2["analysisLanguage"] == "en"
