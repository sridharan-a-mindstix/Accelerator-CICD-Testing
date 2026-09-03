#!/usr/bin/env bash
set -Eeuo pipefail

: "${GITLEAKS_IMAGE:?GITLEAKS_IMAGE is required}"
: "${GITHUB_WORKSPACE:?GITHUB_WORKSPACE is required}"

docker run --rm \
  --user "$(id -u):$(id -g)" \
  -e GIT_CONFIG_COUNT=1 \
  -e GIT_CONFIG_KEY_0=safe.directory \
  -e GIT_CONFIG_VALUE_0=* \
  --mount "type=bind,source=${GITHUB_WORKSPACE},target=/repo,readonly" \
  "${GITLEAKS_IMAGE}" git \
  --redact \
  --no-banner \
  --verbose \
  /repo
