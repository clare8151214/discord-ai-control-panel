#!/usr/bin/env bash
# DEPLOY_IMAGE must be a previously published immutable image tag or digest.
set -euo pipefail
: "${DEPLOY_IMAGE:?Set DEPLOY_IMAGE to ghcr.io/owner/repository:sha-<full-commit>}"
deploy_namespace="${DEPLOY_NAMESPACE:-discord-ai}"
project_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
python3 "$project_root/scripts/render_k8s.py" --image "$DEPLOY_IMAGE" --namespace "$deploy_namespace" \
  | kubectl apply --namespace "$deploy_namespace" -f -
kubectl --namespace "$deploy_namespace" rollout status deployment/discord-ai --timeout=180s
