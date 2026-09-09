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

FROM node:24.12.0-bookworm-slim@sha256:7326fb2dbdce998edd72140946851be64ef4a643e8715e138ca467e8e9d92c99 AS node-tools
WORKDIR /build
COPY services/sandbox/package.json services/sandbox/package-lock.json ./
ENV PUPPETEER_SKIP_DOWNLOAD=true
RUN npm ci --omit=dev --ignore-scripts=false

FROM python:3.12-slim-bookworm@sha256:4766d8b510c428e595d74b9cc5bbb2fae8e26316fffb4adc89908d79aacd58a2 AS wheel-builder
WORKDIR /build
COPY services/sandbox/requirements.lock ./
RUN python -m pip wheel --require-hashes --wheel-dir /wheels -r requirements.lock
COPY packages/artifacts /src/packages/artifacts
COPY packages/contracts/python /src/packages/contracts/python
COPY services/sandbox /src/services/sandbox
COPY --from=document-skills /opt/document-skills /opt/document-skills
RUN python -m pip wheel --no-deps --wheel-dir /wheels /src/packages/contracts/python /src/packages/artifacts /src/services/sandbox

FROM python:3.12-slim-bookworm@sha256:4766d8b510c428e595d74b9cc5bbb2fae8e26316fffb4adc89908d79aacd58a2
ARG SOURCE_REVISION=unknown
LABEL org.opencontainers.image.revision=$SOURCE_REVISION
ARG DEBIAN_SNAPSHOT=20260726T000000Z
RUN printf 'deb [check-valid-until=no] https://snapshot.debian.org/archive/debian/%s bookworm main\n' "$DEBIAN_SNAPSHOT" > /etc/apt/sources.list \
    && apt-get -o Acquire::Check-Valid-Until=false update \
    && apt-get upgrade -y \
    && apt-get install -y --no-install-recommends \
       chromium file fontconfig fonts-dejavu-core jq libreoffice-nogui pandoc poppler-utils tini unzip util-linux zip \
    && rm -rf /var/lib/apt/lists/*
COPY --from=node-tools /usr/local/bin/node /usr/local/bin/node
COPY --from=node-tools /build/node_modules /opt/eda/node_modules
COPY --from=wheel-builder /opt/document-skills /opt/document-skills
COPY --from=document-skills /opt/web-skills /opt/web-skills
COPY services/sandbox/scripts/inline-web-artifact.mjs /opt/eda/inline-web-artifact.mjs
COPY services/sandbox/scripts/render-mermaid.mjs /opt/eda/render-mermaid.mjs
COPY services/sandbox/scripts/verify-web-artifact.mjs /opt/eda/verify-web-artifact.mjs
COPY --from=wheel-builder /wheels /wheels
RUN python -m pip install --no-index --no-cache-dir /wheels/*.whl \
    && rm -rf /wheels /root/.cache \
    && chmod 0555 /opt/eda/render-mermaid.mjs \
    && ln -s /opt/eda/render-mermaid.mjs /usr/local/bin/mmdc \
    && groupadd --gid 10001 sandbox \
    && useradd --uid 10001 --gid 10001 --no-create-home --home-dir /workspace/task/home sandbox \
    && mkdir -p /workspace/task \
    && chown -R 10001:10001 /workspace
ENV NODE_PATH=/opt/eda/node_modules \
    PUPPETEER_EXECUTABLE_PATH=/usr/bin/chromium \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1
USER 10001:10001
WORKDIR /workspace/task
EXPOSE 8080
ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["eda-sandbox"]