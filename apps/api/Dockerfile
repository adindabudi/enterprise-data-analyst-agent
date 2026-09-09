# syntax=docker/dockerfile:1.8
FROM node:24.12.0-bookworm-slim@sha256:7326fb2dbdce998edd72140946851be64ef4a643e8715e138ca467e8e9d92c99 AS web-build
WORKDIR /src
COPY package.json package-lock.json ./
COPY tsconfig.base.json ./
COPY apps/web/package.json apps/web/package.json
COPY packages/contracts/typescript/package.json packages/contracts/typescript/package.json
RUN npm ci
COPY apps/web apps/web
COPY packages/contracts/typescript packages/contracts/typescript
COPY packages/contracts/schema packages/contracts/schema
RUN npm run build --workspace=@eda/web

FROM python:3.12-slim-bookworm@sha256:4766d8b510c428e595d74b9cc5bbb2fae8e26316fffb4adc89908d79aacd58a2 AS wheel-builder
WORKDIR /src
COPY --from=ghcr.io/astral-sh/uv:0.9.16 /uv /uvx /bin/
COPY pyproject.toml uv.lock ./
COPY apps/api apps/api
COPY packages/contracts/python packages/contracts/python
COPY packages/fabric-auth packages/fabric-auth
COPY packages/provenance packages/provenance
COPY packages/runtime-state packages/runtime-state
RUN uv export --frozen --package eda-api --no-dev --no-emit-workspace --format requirements-txt --output-file requirements.txt \
    && python -m pip wheel --require-hashes --wheel-dir /wheels -r requirements.txt \
    && uv build --wheel --out-dir /wheels packages/contracts/python \
    && uv build --wheel --out-dir /wheels packages/fabric-auth \
    && uv build --wheel --out-dir /wheels packages/provenance \
    && uv build --wheel --out-dir /wheels packages/runtime-state \
    && uv build --wheel --out-dir /wheels apps/api

FROM python:3.12-slim-bookworm@sha256:4766d8b510c428e595d74b9cc5bbb2fae8e26316fffb4adc89908d79aacd58a2
ARG SOURCE_REVISION=unknown
LABEL org.opencontainers.image.revision=$SOURCE_REVISION
RUN groupadd --gid 10001 eda \
    && useradd --uid 10001 --gid 10001 --no-create-home --home-dir /app eda
WORKDIR /app
COPY --from=wheel-builder /wheels /wheels
RUN python -m pip install --no-index --no-cache-dir /wheels/*.whl \
    && rm -rf /wheels /root/.cache
COPY --from=web-build /src/apps/web/dist /app/static
COPY .artifacts/model-contract.json /app/config/model-contract.json
COPY services/worker/src/eda_worker/agent/prompts/gpt-5.6-terra-v1.md /app/config/gpt-5.6-terra-v1.md
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    EDA_FRONTEND_DIST=/app/static
USER 10001:10001
EXPOSE 8000
CMD ["uvicorn", "eda_api.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--forwarded-allow-ips", "10.0.0.0/8,172.16.0.0/12,192.168.0.0/16"]