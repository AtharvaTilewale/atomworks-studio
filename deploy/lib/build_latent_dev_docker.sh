#!/usr/bin/env bash
# Build the latent-dev GPU Docker image from an RFD4-Proteina checkout.
#
# Vendored into atomworks-studio from RFD4-Proteina scripts/build_latent_dev_docker.sh.
# Unlike the in-tree original, the RFD4 root is passed explicitly (the submodule
# external/RFD4-Proteina-dev, or ANNOTATION_STUDIO_RFD4_ROOT); it holds the Dockerfile
# and the lib/atomworks submodule the image builds from.
#
# Usage:
#   deploy/lib/build_latent_dev_docker.sh <rfd4_root> [image-tag]
#
# Example:
#   deploy/lib/build_latent_dev_docker.sh external/RFD4-Proteina-dev latent-dev:local
set -euo pipefail

REPO_ROOT="${1:?Usage: build_latent_dev_docker.sh <rfd4_root> [image-tag]}"
REPO_ROOT="$(cd "$REPO_ROOT" && pwd)"
IMAGE_TAG="${2:-latent-dev:local}"
DOCKERFILE="$REPO_ROOT/.ipd/docker/latent-dev.Dockerfile"
STACK="${RFPROTEINA_CUDA_STACK:-cuda13}"
case "$STACK" in
    cuda12|cuda13) ;;
    *) echo "Error: RFPROTEINA_CUDA_STACK must be cuda12 or cuda13" >&2; exit 2 ;;
esac

command -v docker >/dev/null 2>&1 || { echo "Error: docker not found on PATH" >&2; exit 1; }
[ -f "$DOCKERFILE" ] || { echo "Error: missing Dockerfile: $DOCKERFILE" >&2; exit 1; }
[ -f "$REPO_ROOT/lib/atomworks/pyproject.toml" ] || {
    echo "Error: atomworks submodule missing. Run: git submodule update --init lib/atomworks" >&2
    exit 1
}

echo ">>> building $IMAGE_TAG from $DOCKERFILE (stack=$STACK)"
docker build \
    --file "$DOCKERFILE" \
    --tag "$IMAGE_TAG" \
    --build-arg STACK="$STACK" \
    --build-arg DEPS_LAYER_REV=10 \
    "$REPO_ROOT"

echo ">>> done. Run with:"
cat <<EOF
docker run --rm -it --gpus all \\
  --volume "$REPO_ROOT:/modelhub_host" \\
  --workdir /modelhub_host \\
  $IMAGE_TAG -c "import torch; print(torch.__version__)"
EOF
