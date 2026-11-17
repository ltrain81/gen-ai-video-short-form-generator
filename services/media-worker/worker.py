"""Fargate media worker entrypoint. JOB env var carries the job JSON."""
import json
import logging
import os
import sys

from shorts import ingest, media

logging.basicConfig(level=logging.INFO)


def main() -> int:
    job = json.loads(os.environ["JOB"])
    kind = job.get("job")
    if kind == "prepare":
        media.prepare(job, ingest.send_ingest_event)
    elif kind == "render":
        from shorts import renders
        renders.run_job(job)
    else:
        raise SystemExit(f"Unknown job {kind}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
