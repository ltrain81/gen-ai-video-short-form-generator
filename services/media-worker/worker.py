"""Fargate media worker entrypoint. JOB env var carries the job JSON."""
import json
import logging
import os
import sys

from shorts import ingest, media

logging.basicConfig(level=logging.INFO)


def main() -> int:
    job = json.loads(os.environ["JOB"])
    if job.get("job") != "prepare":
        raise SystemExit(f"Unknown job {job.get('job')}")
    media.prepare(job, ingest.send_ingest_event)
    return 0


if __name__ == "__main__":
    sys.exit(main())
