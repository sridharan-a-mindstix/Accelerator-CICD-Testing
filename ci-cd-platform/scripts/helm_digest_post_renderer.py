#!/usr/bin/env python3
"""Helm post-renderer that pins Deployment application images by digest.

Known limitation: yaml.safe_load + yaml.safe_dump cannot fully round-trip all
valid YAML constructs. Anchors, aliases, and comments are lost in translation.
This is acceptable for Helm-rendered output which does not use those features,
but the output should not be treated as semantically equivalent to the input for
arbitrary Kubernetes manifests.
"""

from __future__ import annotations

import os
import re
import sys
from typing import Any

import yaml


DIGEST_PATTERN = re.compile(r"^sha256:[0-9a-f]{64}$")


def main() -> int:
    repository = os.environ.get("DEPLOY_IMAGE_REPOSITORY", "").strip()
    digest = os.environ.get("DEPLOY_IMAGE_DIGEST", "").strip()
    container_name = os.environ.get("DEPLOY_CONTAINER_NAME", "").strip()

    if not repository:
        raise SystemExit("DEPLOY_IMAGE_REPOSITORY is required")
    if not DIGEST_PATTERN.fullmatch(digest):
        raise SystemExit("DEPLOY_IMAGE_DIGEST must be a sha256 digest")

    documents = list(yaml.safe_load_all(sys.stdin))
    patched = 0
    immutable_image = f"{repository}@{digest}"

    for document in documents:
        if not isinstance(document, dict):
            continue
        if document.get("apiVersion") != "apps/v1" or document.get("kind") != "Deployment":
            continue

        containers: list[dict[str, Any]] = (
            document.get("spec", {})
            .get("template", {})
            .get("spec", {})
            .get("containers", [])
        )
        if container_name:
            # Target the named container explicitly — required when sidecars exist.
            targets = [item for item in containers if item.get("name") == container_name]
        else:
            # No name configured: target only the first container and warn.
            targets = containers[:1]
            if targets:
                print(
                    f"Warning: helm_container_name not set; patching first container "
                    f"'{targets[0].get('name', '<unnamed>')}'. "
                    f"Set helm.container_name in config.yaml to suppress this warning.",
                    file=sys.stderr,
                )
        for target in targets:
            target["image"] = immutable_image
            patched += 1

    if patched == 0:
        target_desc = f" named {container_name!r}" if container_name else ""
        raise SystemExit(f"No Deployment application container{target_desc} was found in Helm output")

    yaml.safe_dump_all(documents, sys.stdout, explicit_start=True, sort_keys=False)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
