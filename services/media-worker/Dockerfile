# Media worker: ffmpeg + poppler + LibreOffice (PPT/PPTX → PDF) + CJK fonts.
FROM public.ecr.aws/docker/library/python:3.13-slim-bookworm@sha256:a5a7017efc5969e00ae2b91c8eb0631049a569652aa4ffc4501769049ae45c72
RUN apt-get update && apt-get install -y --no-install-recommends \
      ffmpeg poppler-utils libreoffice-impress libreoffice-writer fonts-noto-cjk fonts-noto-core \
    && rm -rf /var/lib/apt/lists/*
RUN useradd --create-home --uid 10001 worker
WORKDIR /app
COPY pyproject.toml ./
COPY media-worker/requirements.lock.txt ./
COPY src ./src
# Hash-locked dependencies (exported from uv.lock), then the package itself without resolving deps.
RUN pip install --no-cache-dir --require-hashes -r requirements.lock.txt && pip install --no-cache-dir --no-deps .
COPY media-worker/worker.py ./worker.py
USER worker
ENTRYPOINT ["python", "/app/worker.py"]
