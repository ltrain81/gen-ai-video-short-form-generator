"""Runtime configuration from environment variables (set by CDK)."""
import os
from functools import lru_cache


def _env(name: str, default: str | None = None) -> str:
    value = os.environ.get(name, default)
    if value is None:
        raise RuntimeError(f"Missing required environment variable {name}")
    return value


class Config:
    @property
    def region(self) -> str:
        return _env("AWS_REGION", "us-east-1")

    @property
    def account_id(self) -> str:
        return _env("ACCOUNT_ID")

    @property
    def table_name(self) -> str:
        return _env("TABLE_NAME")

    @property
    def media_bucket(self) -> str:
        return _env("MEDIA_BUCKET")

    @property
    def vector_bucket(self) -> str:
        return _env("VECTOR_BUCKET")

    @property
    def ingest_queue_url(self) -> str:
        return _env("INGEST_QUEUE_URL")

    @property
    def model_queue_url(self) -> str:
        return _env("MODEL_QUEUE_URL")

    @property
    def poll_queue_url(self) -> str:
        return _env("POLL_QUEUE_URL")

    @property
    def marengo_model_id(self) -> str:
        return _env("MARENGO_MODEL_ID", "twelvelabs.marengo-embed-3-0-v1:0")

    @property
    def marengo_query_model_id(self) -> str:
        return _env("MARENGO_QUERY_MODEL_ID", "us.twelvelabs.marengo-embed-3-0-v1:0")

    @property
    def pegasus_model_id(self) -> str:
        return _env("PEGASUS_MODEL_ID", "us.twelvelabs.pegasus-1-5-v1:0")

    @property
    def vision_model_id(self) -> str:
        return _env("VISION_MODEL_ID", "us.openai.gpt-6.1-sol")

    @property
    def analysis_chunk_sec(self) -> int:
        # Pegasus 1.5: <=2h/<2GB, Marengo 3.0: <=4h/6GB. Chunk to bound latency and parallelize.
        return int(_env("ANALYSIS_CHUNK_SEC", "1800"))

    @property
    def media_worker_cluster(self) -> str:
        return _env("MEDIA_WORKER_CLUSTER")

    @property
    def media_worker_task_def(self) -> str:
        return _env("MEDIA_WORKER_TASK_DEF")

    @property
    def media_worker_subnets(self) -> list[str]:
        return [s for s in _env("MEDIA_WORKER_SUBNETS", "").split(",") if s]

    @property
    def media_worker_security_group(self) -> str:
        return _env("MEDIA_WORKER_SG")

    @property
    def upload_cors_origin(self) -> str:
        return _env("UPLOAD_CORS_ORIGIN", "")


@lru_cache(maxsize=1)
def get_config() -> Config:
    return Config()
