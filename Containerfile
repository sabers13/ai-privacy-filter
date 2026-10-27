# Gate service for the local n8n end-to-end check.
#
# The image is built from the same source tree and the same lock file as the
# host environment, so the gate that n8n talks to is the reviewed code. It is
# published only on the compose network; no port is exposed to the host.

FROM docker.io/library/python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    UV_LINK_MODE=copy

RUN pip install --no-cache-dir "uv==0.13.5"

WORKDIR /app

COPY pyproject.toml uv.lock README.md LICENSE NOTICE THIRD_PARTY.md ./

# Dependencies first, and the pinned model wheel in a layer that does not depend
# on the source tree, so editing src/ does not download 541 MiB again.
RUN uv sync --frozen --no-dev --no-install-project \
    && pip download --no-cache-dir --no-deps --dest /opt/model-wheels \
        "https://github.com/explosion/spacy-models/releases/download/de_core_news_lg-3.8.0/de_core_news_lg-3.8.0-py3-none-any.whl" \
    && rm -rf /root/.cache

COPY src ./src

RUN uv sync --frozen --no-dev \
    && uv pip install --python /app/.venv/bin/python --no-index \
        --find-links /opt/model-wheels "de-core-news-lg==3.8.0"

ENV PATH="/app/.venv/bin:${PATH}"

EXPOSE 8765

CMD ["aipf", "serve", "--host", "0.0.0.0", "--port", "8765"]
