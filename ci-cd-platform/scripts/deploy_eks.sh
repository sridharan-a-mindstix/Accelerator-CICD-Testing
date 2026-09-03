#!/usr/bin/env bash
set -Eeuo pipefail

require_env() {
  local name="$1"
  if [[ -z "${!name:-}" ]]; then
    echo "${name} is required" >&2
    exit 1
  fi
}

append_configured_helm_args() {
  while IFS= read -r values_file; do
    [[ -z "${values_file}" ]] || HELM_COMMAND_ARGS+=(-f "${values_file}")
  done <<< "${HELM_VALUES_FILES:-}"

  while IFS= read -r set_value; do
    [[ -z "${set_value}" ]] || HELM_COMMAND_ARGS+=(--set-string "${set_value}")
  done <<< "${HELM_SET_VALUES:-}"
}

resolve_image() {
  require_env AWS_ACCOUNT_ID
  require_env ECR_AWS_REGION
  require_env ECR_REPOSITORY
  require_env SOURCE_SHA
  require_env GITHUB_OUTPUT

  local repository tagged_digest image_digest verified_digest
  repository="${AWS_ACCOUNT_ID}.dkr.ecr.${ECR_AWS_REGION}.amazonaws.com/${ECR_REPOSITORY}"
  if [[ -n "${METADATA_REPOSITORY:-}" && "${METADATA_REPOSITORY}" != "${repository}" ]]; then
    echo "Release metadata repository does not match the configured ECR repository" >&2
    exit 1
  fi

  local describe_err
  describe_err="$(mktemp)"
  tagged_digest="$(aws ecr describe-images \
    --region "${ECR_AWS_REGION}" \
    --registry-id "${AWS_ACCOUNT_ID}" \
    --repository-name "${ECR_REPOSITORY}" \
    --image-ids imageTag="${SOURCE_SHA}" \
    --query 'imageDetails[0].imageDigest' \
    --output text 2>"${describe_err}" || true)"
  if [[ ! "${tagged_digest}" =~ ^sha256:[0-9a-f]{64}$ ]]; then
    echo "No immutable ECR image was found for source SHA ${SOURCE_SHA}" >&2
    if [[ -s "${describe_err}" ]]; then
      echo "AWS CLI error details:" >&2
      cat "${describe_err}" >&2
    fi
    rm -f "${describe_err}"
    exit 1
  fi
  rm -f "${describe_err}"

  image_digest="${REQUESTED_DIGEST:-${tagged_digest}}"
  if [[ "${image_digest}" != "${tagged_digest}" ]]; then
    echo "Requested digest does not match the image built from source SHA ${SOURCE_SHA}" >&2
    echo "Expected: ${tagged_digest}" >&2
    echo "Got:      ${image_digest}" >&2
    exit 1
  fi

  verified_digest="$(aws ecr describe-images \
    --region "${ECR_AWS_REGION}" \
    --registry-id "${AWS_ACCOUNT_ID}" \
    --repository-name "${ECR_REPOSITORY}" \
    --image-ids imageDigest="${image_digest}" \
    --query 'imageDetails[0].imageDigest' \
    --output text)"
  if [[ "${verified_digest}" != "${image_digest}" ]]; then
    echo "The requested digest does not exist in the configured ECR repository" >&2
    exit 1
  fi

  echo "repository=${repository}" >> "${GITHUB_OUTPUT}"
  echo "digest=${image_digest}" >> "${GITHUB_OUTPUT}"
}

lint_chart() {
  require_env HELM_CHART
  HELM_COMMAND_ARGS=(lint "${HELM_CHART}")
  append_configured_helm_args
  helm "${HELM_COMMAND_ARGS[@]}"
}

deploy_release() {
  for name in DEPLOY_IMAGE_DIGEST DEPLOY_IMAGE_REPOSITORY HELM_CHART HELM_HISTORY_MAX HELM_NAMESPACE HELM_RELEASE HELM_TIMEOUT; do
    require_env "${name}"
  done
  chmod +x ci-cd-platform/scripts/helm_digest_post_renderer.py

  HELM_COMMAND_ARGS=(
    upgrade --install "${HELM_RELEASE}" "${HELM_CHART}"
    --namespace "${HELM_NAMESPACE}"
    --wait
    --timeout "${HELM_TIMEOUT}"
    --history-max "${HELM_HISTORY_MAX}"
    --post-renderer ./ci-cd-platform/scripts/helm_digest_post_renderer.py
  )
  [[ "${HELM_ATOMIC:-false}" == "true" ]] && HELM_COMMAND_ARGS+=(--atomic)
  [[ "${HELM_CREATE_NAMESPACE:-true}" == "true" ]] && HELM_COMMAND_ARGS+=(--create-namespace)
  append_configured_helm_args
  helm "${HELM_COMMAND_ARGS[@]}"
}

verify_rollout() {
  for name in EXPECTED_IMAGE HELM_NAMESPACE HELM_RELEASE HELM_TIMEOUT; do
    require_env "${name}"
  done

  helm status "${HELM_RELEASE}" --namespace "${HELM_NAMESPACE}"
  kubectl rollout status deployment \
    --namespace "${HELM_NAMESPACE}" \
    --selector "app.kubernetes.io/instance=${HELM_RELEASE}" \
    --timeout "${HELM_TIMEOUT}"

  local jsonpath deployed_image
  local -a deployed_images
  if [[ -n "${DEPLOY_CONTAINER_NAME:-}" ]]; then
    jsonpath="{range .items[*].spec.template.spec.containers[?(@.name==\"${DEPLOY_CONTAINER_NAME}\")]}{.image}{\"\n\"}{end}"
  else
    jsonpath='{range .items[*].spec.template.spec.containers[0]}{.image}{"\n"}{end}'
  fi
  readarray -t deployed_images < <(kubectl get deployment \
    --namespace "${HELM_NAMESPACE}" \
    --selector "app.kubernetes.io/instance=${HELM_RELEASE}" \
    -o jsonpath="${jsonpath}")

  if [[ "${#deployed_images[@]}" -eq 0 ]]; then
    echo "No Deployments were found for Helm release ${HELM_RELEASE}" >&2
    exit 1
  fi
  for deployed_image in "${deployed_images[@]}"; do
    [[ -z "${deployed_image}" ]] && continue
    if [[ "${deployed_image}" != "${EXPECTED_IMAGE}" ]]; then
      echo "A Deployment is not using the expected immutable image digest" >&2
      echo "Expected: ${EXPECTED_IMAGE}" >&2
      echo "Got:      ${deployed_image}" >&2
      exit 1
    fi
  done
}

collect_diagnostics() {
  require_env HELM_NAMESPACE
  require_env HELM_RELEASE
  set +e
  helm status "${HELM_RELEASE}" --namespace "${HELM_NAMESPACE}"
  kubectl get deployments,replicasets,pods \
    --namespace "${HELM_NAMESPACE}" \
    --selector "app.kubernetes.io/instance=${HELM_RELEASE}" \
    -o wide
  kubectl get events \
    --namespace "${HELM_NAMESPACE}" \
    --sort-by='.lastTimestamp' \
    | tail -n 50
}

case "${1:-}" in
  resolve-image) resolve_image ;;
  lint) lint_chart ;;
  deploy) deploy_release ;;
  verify) verify_rollout ;;
  diagnostics) collect_diagnostics ;;
  *)
    echo "Usage: $0 {resolve-image|lint|deploy|verify|diagnostics}" >&2
    exit 2
    ;;
esac
