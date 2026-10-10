#!/usr/bin/env bash
set -euo pipefail

readonly source_sha=8daedbc4ed501f52b8ec1569684e3b82fb60b92f
readonly source_dir="$(git -C "${SOURCE_DIR:-.}" rev-parse --show-toplevel)"
readonly script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
readonly build_dir="$(mktemp -d)"
readonly proof_dir="${PROOF_DIR:-$PWD/image-proof}"
readonly run_id="${BUILD_RUN_ID:-local}-$(date -u +%Y%m%dT%H%M%SZ)-$$"
readonly image="${IMAGE_TAG:-git.cobley.io/lewis/litellm:customers-permissions-ci-${source_sha:0:12}-${run_id}}"
readonly registry_host="${REGISTRY_HOST:-git.cobley.io}"
readonly registry_user="${REGISTRY_USER:-lewis}"
export DOCKER_CONFIG="$build_dir/docker-config"
trap 'rm -rf -- "$build_dir"' EXIT

[[ "$run_id" =~ ^[A-Za-z0-9_.-]+$ ]]
[[ "$image" == "$registry_host/"* ]]
test "$(git -C "$source_dir" rev-parse "$source_sha^{commit}")" = "$source_sha"
test "$(docker info --format '{{.OSType}}/{{.Architecture}}')" = linux/x86_64
docker buildx inspect default
mkdir -p "$build_dir/context" "$DOCKER_CONFIG" "$proof_dir"
git -C "$source_dir" archive "$source_sha" | tar -x -C "$build_dir/context"

docker buildx build --builder default --platform linux/amd64 \
  --file "$build_dir/context/Dockerfile" \
  --build-arg "LITELLM_RELEASE_TAG=${image##*:}" \
  --build-arg BUILDKIT_SYNTAX=docker.io/docker/dockerfile:1.7@sha256:a57df69d0ea827fb7266491f2813635de6f17269be881f696fbfdf2d83dda33e \
  --label "org.opencontainers.image.revision=$source_sha" \
  --label org.opencontainers.image.source=https://github.com/lewisco/litellm \
  --label "io.cobley.workflow.revision=${WORKFLOW_SHA:-local}" \
  --cache-to type=inline --tag "$image" --load --progress plain \
  --metadata-file "$proof_dir/build-metadata.json" "$build_dir/context"

IMAGE_TAG="$image" SOURCE_DIR="$build_dir/context" PROOF_DIR="$proof_dir" \
  bash "$script_dir/smoke-customers-permissions-image.sh"

if [ "${PUBLISH_IMAGE:-false}" = true ]; then
  test -n "${REGISTRY_TOKEN:-}"
  printf '%s' "$REGISTRY_TOKEN" | docker login "$registry_host" --username "$registry_user" --password-stdin
  docker push "$image"
  docker image inspect "$image" --format '{{json .RepoDigests}}' > "$proof_dir/registry-digests.json"
  cat "$proof_dir/registry-digests.json"
fi
printf 'Image: %s\nSource: %s\nPlatform: linux/amd64\nSmoke: passed\n' "$image" "$source_sha"
