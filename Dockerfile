FROM rust:1-bookworm AS wasm-builder

RUN rustup target add wasm32-unknown-unknown \
    && cargo install wasm-pack --locked --version 0.13.1
WORKDIR /build
COPY apps/annotation-studio/core ./core
RUN wasm-pack build ./core --target web --out-dir /wasm


FROM node:22-bookworm-slim AS web-builder

ARG ANNOTATION_STUDIO_VERSION=development
ARG ANNOTATION_STUDIO_REVISION=unknown
ENV ANNOTATION_STUDIO_VERSION=${ANNOTATION_STUDIO_VERSION} \
    ANNOTATION_STUDIO_REVISION=${ANNOTATION_STUDIO_REVISION}
RUN corepack enable && corepack prepare pnpm@11.16.0 --activate
WORKDIR /build/web
COPY apps/annotation-studio/web/package.json \
     apps/annotation-studio/web/pnpm-lock.yaml \
     apps/annotation-studio/web/pnpm-workspace.yaml ./
RUN pnpm install --frozen-lockfile
COPY apps/annotation-studio/web ./
COPY --from=wasm-builder /wasm ./src/wasm
RUN pnpm exec tsc --noEmit && pnpm exec vite build


FROM python:3.12.10-slim-bookworm

ARG ANNOTATION_STUDIO_VERSION=development
ARG ANNOTATION_STUDIO_REVISION=unknown
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    RFPROTEINA_LIGHTWEIGHT_IMPORT=1 \
    PYTHONPATH=/app/src \
    ANNOTATION_STUDIO_VERSION=${ANNOTATION_STUDIO_VERSION} \
    ANNOTATION_STUDIO_REVISION=${ANNOTATION_STUDIO_REVISION}

RUN apt-get update \
    && apt-get install --yes --no-install-recommends libgomp1 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY lib/atomworks ./lib/atomworks
RUN python -m pip install --no-cache-dir \
      --index-url https://download.pytorch.org/whl/cpu \
      torch==2.13.0 \
    && python -m pip install --no-cache-dir \
      ./lib/atomworks \
      "beartype>=0.18,<1" \
      "fastapi>=0.115,<1" \
      "jaxtyping>=0.2.17,<1" \
      "python-multipart>=0.0.20,<1" \
      "pyyaml>=6,<7" \
      "uvicorn[standard]>=0.34,<1"

COPY src/rfproteina ./src/rfproteina
COPY apps/annotation-studio/api ./apps/annotation-studio/api
COPY --from=web-builder /build/web/dist ./apps/annotation-studio/web/dist
RUN python -m pip install --no-cache-dir ./apps/annotation-studio/api

RUN useradd --create-home --uid 10001 studio
USER 10001

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=3s --start-period=15s --retries=3 \
  CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=2)"]

CMD ["uvicorn", "annotation_studio.main:app", "--host", "0.0.0.0", "--port", "8000"]
