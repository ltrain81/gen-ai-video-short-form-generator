# Media worker: ffmpeg + poppler + LibreOffice (PPT/PPTX → PDF) + CJK fonts.
FROM public.ecr.aws/docker/library/python:3.13-slim-bookworm
RUN apt-get update && apt-get install -y --no-install-recommends \
      ffmpeg poppler-utils libreoffice-impress libreoffice-writer fonts-noto-cjk fonts-noto-core \
    && rm -rf /var/lib/apt/lists/*
RUN useradd --create-home --uid 10001 worker
WORKDIR /app
COPY pyproject.toml ./
COPY src ./src
RUN pip install --no-cache-dir .
COPY media-worker/worker.py ./worker.py
USER worker
ENTRYPOINT ["python", "/app/worker.py"]
