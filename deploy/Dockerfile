# syntax=docker/dockerfile:1.7
FROM rust:1-bookworm@sha256:77fac8b98f9f46062bb680b6d25d5bcaabfc400143952ebc572e924bcbedc3fa AS wasm-builder

RUN rustup target add wasm32-unknown-unknown \
    && cargo install wasm-pack --locked --version 0.13.1
WORKDIR /build
COPY packages/core ./packages/core
RUN wasm-pack build ./packages/core --target web --out-dir /wasm


FROM node:22-bookworm-slim@sha256:6c74791e557ce11fc957704f6d4fe134a7bc8d6f5ca4403205b2966bd488f6b3 AS web-builder

ARG ANNOTATION_STUDIO_VERSION=development
ARG ANNOTATION_STUDIO_REVISION=unknown
ENV ANNOTATION_STUDIO_VERSION=${ANNOTATION_STUDIO_VERSION} \
    ANNOTATION_STUDIO_REVISION=${ANNOTATION_STUDIO_REVISION}
RUN corepack enable && corepack prepare pnpm@11.16.0 --activate
WORKDIR /build
COPY package.json pnpm-lock.yaml pnpm-workspace.yaml ./
COPY packages/web/package.json ./packages/web/package.json
COPY packages/vscode/package.json ./packages/vscode/package.json
COPY packages/web/vendor ./packages/web/vendor
RUN pnpm install --frozen-lockfile
COPY packages/web ./packages/web
COPY examples ./examples
COPY --from=wasm-builder /wasm ./packages/web/src/wasm
RUN pnpm --dir packages/web exec tsc --noEmit \
    && pnpm --dir packages/web exec vite build


FROM python:3.12.10-slim-bookworm@sha256:fd95fa221297a88e1cf49c55ec1828edd7c5a428187e67b5d1805692d11588db

ARG ANNOTATION_STUDIO_VERSION=development
ARG ANNOTATION_STUDIO_REVISION=unknown
ARG ANNOTATION_STUDIO_INSTALL_RFD4_PROVIDER=false
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    RFPROTEINA_LIGHTWEIGHT_IMPORT=1 \
    ANNOTATION_STUDIO_MAX_ACTIVE_JOBS=1 \
    ANNOTATION_STUDIO_VERSION=${ANNOTATION_STUDIO_VERSION} \
    ANNOTATION_STUDIO_REVISION=${ANNOTATION_STUDIO_REVISION}

RUN apt-get update \
    && apt-get install --yes --no-install-recommends git libgomp1 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY packages/api ./packages/api
COPY providers/rfd4 ./providers/rfd4
COPY --from=web-builder /build/packages/web/dist ./packages/web/dist
RUN --mount=type=secret,id=github_token,required=true \
    github_token="$(cat /run/secrets/github_token)" \
    && export GIT_CONFIG_COUNT=1 \
       GIT_CONFIG_KEY_0="url.https://x-access-token:${github_token}@github.com/.insteadOf" \
       GIT_CONFIG_VALUE_0="https://github.com/" \
    && python -m pip install --no-cache-dir \
      --index-url https://download.pytorch.org/whl/cpu \
      torch==2.7.1 \
    && python -m pip install --no-cache-dir ./packages/api \
    && if [ "${ANNOTATION_STUDIO_INSTALL_RFD4_PROVIDER}" = "true" ]; then \
         python -m pip install --no-cache-dir ./providers/rfd4; \
       elif [ "${ANNOTATION_STUDIO_INSTALL_RFD4_PROVIDER}" != "false" ]; then \
         echo "ANNOTATION_STUDIO_INSTALL_RFD4_PROVIDER must be true or false" >&2; \
         exit 2; \
       fi \
    && rm -rf ./packages ./providers \
    && apt-get purge --yes --auto-remove git \
    && rm -rf /var/lib/apt/lists/*

RUN useradd --create-home --uid 10001 studio
USER 10001

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=3s --start-period=15s --retries=3 \
  CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=2)"]

CMD ["uvicorn", "annotation_studio.main:app", "--host", "0.0.0.0", "--port", "8000"]
