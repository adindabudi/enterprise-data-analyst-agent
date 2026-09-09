# syntax=docker/dockerfile:1.8
FROM python:3.12-slim-bookworm@sha256:4766d8b510c428e595d74b9cc5bbb2fae8e26316fffb4adc89908d79aacd58a2 AS document-skills
ARG EDA_DOCUMENT_TERMS_ACCEPTED=""
ENV EDA_DOCUMENT_TERMS_ACCEPTED=$EDA_DOCUMENT_TERMS_ACCEPTED
WORKDIR /src
RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates git \
    && rm -rf /var/lib/apt/lists/*
COPY skills.lock.json ./
COPY scripts/document_skills_contract.py scripts/document_skills_acquisition.py scripts/acquire-skills.py scripts/validate-skills.py scripts/
COPY scripts/document-skills-requirements.txt scripts/
COPY services/worker/document-skills/build-bundles.py /build-bundles.py
RUN python -m pip install --no-cache-dir --require-hashes -r scripts/document-skills-requirements.txt \
        && python scripts/acquire-skills.py --lock skills.lock.json --pack web --destination /opt/web-skills \
        && python scripts/validate-skills.py --lock skills.lock.json --pack web --destination /opt/web-skills \
        && mkdir -p /opt/document-skills \
        && if [ -n "$EDA_DOCUMENT_TERMS_ACCEPTED" ]; then \
                 python scripts/acquire-skills.py --lock skills.lock.json --pack documents --destination /opt/document-skills \
                 && python scripts/validate-skills.py --lock skills.lock.json --pack documents --destination /opt/document-skills \
         && python /build-bundles.py --source /opt/document-skills --output /opt/document-skills; \
       fi

FROM python:3.12-slim-bookworm@sha256:4766d8b510c428e595d74b9cc5bbb2fae8e26316fffb4adc89908d79aacd58a2 AS wheel-builder
WORKDIR /src
COPY --from=ghcr.io/astral-sh/uv:0.9.16 /uv /uvx /bin/
COPY pyproject.toml uv.lock ./
COPY packages/contracts/python packages/contracts/python
COPY packages/fabric-auth packages/fabric-auth
COPY packages/provenance packages/provenance
COPY packages/runtime-state packages/runtime-state
COPY services/worker services/worker
RUN uv export --frozen --package eda-worker --no-dev --no-emit-workspace --format requirements-txt --output-file requirements.txt \
    && python -m pip wheel --require-hashes --wheel-dir /wheels -r requirements.txt \
    && uv build --wheel --out-dir /wheels packages/contracts/python \
    && uv build --wheel --out-dir /wheels packages/fabric-auth \
    && uv build --wheel --out-dir /wheels packages/provenance \
    && uv build --wheel --out-dir /wheels packages/runtime-state \
    && uv build --wheel --out-dir /wheels services/worker

FROM python:3.12-slim-bookworm@sha256:4766d8b510c428e595d74b9cc5bbb2fae8e26316fffb4adc89908d79aacd58a2
ARG SOURCE_REVISION=unknown
LABEL org.opencontainers.image.revision=$SOURCE_REVISION
RUN groupadd --gid 10001 eda \
    && useradd --uid 10001 --gid 10001 --no-create-home --home-dir /app eda
WORKDIR /app
COPY --from=wheel-builder /wheels /wheels
RUN python -m pip install --no-index --no-cache-dir /wheels/*.whl \
    && rm -rf /wheels /root/.cache
COPY .artifacts/model-contract.json /app/config/model-contract.json
COPY .artifacts/tokenizer-calibration.json /app/config/tokenizer-calibration.json
COPY --from=document-skills --chown=10001:10001 /opt/document-skills /opt/document-skills
COPY --from=document-skills /opt/web-skills /opt/web-skills
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    HOME=/app
ENTRYPOINT ["python", "-m", "eda_worker.container_entrypoint"]
CMD ["run"]