FROM public.ecr.aws/lambda/microvms:al2023-minimal@sha256:3e45d46e969eb0d34ea954aff12747e15ca3f4a594788812c7218445777fa4bc

# OS setup
RUN dnf install -y tar gzip findutils shadow-utils ca-certificates \
 && dnf clean all \
 && useradd -m -u 1000 app

# Codex (pinned release; installer verified by SHA-256 before it runs)
ARG CODEX_INSTALLER_SHA256=5a8eea5c3716b555fd8e539db7e5b0e7470bcee5693191b4758a0de373624a70
RUN curl -fsSL https://chatgpt.com/codex/install.sh -o /tmp/install-codex.sh \
 && echo "${CODEX_INSTALLER_SHA256}  /tmp/install-codex.sh" | sha256sum -c - \
 && CODEX_RELEASE=0.169.0 CODEX_NON_INTERACTIVE=1 CODEX_INSTALL_DIR=/opt/bma/bin CODEX_HOME=/opt/bma/codex \
    sh /tmp/install-codex.sh \
 && chmod -R a+rX /opt/bma \
 && rm /tmp/install-codex.sh

# CloudWatch agent (pinned version, verified by SHA-256)
ARG CWAGENT_VERSION=1.300074.1b2005
RUN arch=$(uname -m | sed 's/x86_64/amd64/;s/aarch64/arm64/') \
 && case "$arch" in arm64) sum=eef6dc64d6abe70a861d0c1c071477631fc001b004b0a498d4bfad68eb4cae7c ;; \
      amd64) sum=46d441abfb67d8998e24e00a7967b116e390657dec2fdabed177d7213c22638f ;; esac \
 && curl -fsSL -o /tmp/cwagent.rpm \
    https://amazoncloudwatch-agent.s3.amazonaws.com/amazon_linux/${arch}/${CWAGENT_VERSION}/amazon-cloudwatch-agent.rpm \
 && echo "${sum}  /tmp/cwagent.rpm" | sha256sum -c - \
 && rpm -i /tmp/cwagent.rpm \
 && rm /tmp/cwagent.rpm

# uv and Python
# Pinned by digest (supply chain): uv 0.12.5
COPY --from=ghcr.io/astral-sh/uv:0.12.5@sha256:e85be844203885286c60ffad8a858d48afb6c5a5c237ca0e67f12e74b8f174b1 /uv /bin/
ARG UV_DEFAULT_INDEX
ARG UV_INDEX
ENV UV_PYTHON_INSTALL_DIR=/opt/python UV_PYTHON_BIN_DIR=/usr/local/bin
RUN uv python install --default

# Application. The layout of /opt/bma is the same as the layout of this directory.
WORKDIR /opt/bma
ENV UV_COMPILE_BYTECODE=1 UV_NO_PROGRESS=1 \
    UV_DEFAULT_INDEX=${UV_DEFAULT_INDEX} UV_INDEX=${UV_INDEX}
COPY agents/runtime/pyproject.toml agents/runtime/uv.lock* ./
RUN uv sync --frozen --no-dev
COPY agents/runtime/lifecycle/ lifecycle/
COPY agents/runtime/otel/ otel/
COPY agents/runtime/plugins/ plugins/
# Skills have one source of truth (shared with the Strands fallback).
COPY services/src/shorts/skills/ plugins/shorts/skills/
USER app

EXPOSE 8080
CMD ["uv", "run", "--no-sync", "opentelemetry-instrument", "python", "-u", "lifecycle/server.py"]
