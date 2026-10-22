"""Render an image-pinned deployment without modifying checked-in manifests."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import subprocess
import tempfile


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", required=True, help="Registry image with explicit tag or sha256 digest")
    parser.add_argument("--namespace", default="discord-ai")
    parser.add_argument("--kubectl", default="kubectl")
    args = parser.parse_args()
    if not re.fullmatch(r"[a-z0-9][a-z0-9./_-]*(?::[0-9]+)?(?:/[a-z0-9._/-]+)?(?::[A-Za-z0-9_.-]+|@sha256:[a-f0-9]{64})", args.image):
        parser.error("--image must include a registry/repository and explicit tag or sha256 digest")
    if args.image.endswith(":latest"):
        parser.error("Use an immutable sha-<commit> tag or sha256 digest instead of latest")
    if not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", args.namespace):
        parser.error("Invalid Kubernetes namespace")
    if "@sha256:" in args.image:
        image_name, image_digest = args.image.split("@", 1)
        image = {"name": "discord-ai-control-panel", "newName": image_name, "digest": image_digest}
    else:
        image_name, image_tag = args.image.rsplit(":", 1)
        image = {"name": "discord-ai-control-panel", "newName": image_name, "newTag": image_tag}
    k8s_dir = Path(__file__).resolve().parents[1] / "deploy" / "k8s"
    # Keep the temporary overlay beside base so Kustomize's root restrictions
    # permit the reference, and TemporaryDirectory cleans it even on failure.
    with tempfile.TemporaryDirectory(prefix=".render-", dir=k8s_dir) as overlay:
        (Path(overlay) / "kustomization.yaml").write_text(json.dumps({
            "apiVersion": "kustomize.config.k8s.io/v1beta1",
            "kind": "Kustomization",
            "namespace": args.namespace,
            "resources": ["../base"],
            "images": [image],
        }), encoding="utf-8")
        subprocess.run([args.kubectl, "kustomize", overlay], check=True)


if __name__ == "__main__":
    main()
