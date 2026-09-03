#!/usr/bin/env python3
"""Create and consume the trusted Release-to-Deploy metadata handoff."""

from __future__ import annotations

import argparse
import json
import os
import re
from pathlib import Path
from typing import Any


SHA_PATTERN = re.compile(r"^[0-9a-f]{40}$")
DIGEST_PATTERN = re.compile(r"^sha256:[0-9a-f]{64}$")
ENVIRONMENT_PATTERN = re.compile(r"^[a-z0-9]([-a-z0-9]*[a-z0-9])?$")


def required_env(name: str) -> str:
    value = os.environ.get(name, "")
    if not value:
        raise SystemExit(f"{name} is required")
    return value


def write_outputs(values: dict[str, str]) -> None:
    output_path = required_env("GITHUB_OUTPUT")
    with open(output_path, "a", encoding="utf-8") as output:
        for name, value in values.items():
            if "\n" in value:
                raise SystemExit(f"Output {name!r} must be a single line")
            output.write(f"{name}={value}\n")


def load_metadata(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise SystemExit(f"Deployment metadata not found: {path}")
    with path.open(encoding="utf-8") as metadata_file:
        metadata = json.load(metadata_file)
    if not isinstance(metadata, dict):
        raise SystemExit("Deployment metadata must be a JSON object")
    return metadata


def create_metadata(_: argparse.Namespace) -> int:
    source_sha = required_env("SOURCE_SHA")
    image_repository = required_env("IMAGE_REPOSITORY")
    image_digest = required_env("IMAGE_DIGEST")
    automatic_environment = os.environ.get("AUTOMATIC_ENVIRONMENT", "")
    deploy_enabled_text = required_env("DEPLOY_ENABLED")

    if not SHA_PATTERN.fullmatch(source_sha):
        raise SystemExit("SOURCE_SHA must be a full 40-character Git commit SHA")
    if not DIGEST_PATTERN.fullmatch(image_digest):
        raise SystemExit("IMAGE_DIGEST must be an immutable sha256 digest")
    if deploy_enabled_text not in {"true", "false"}:
        raise SystemExit("DEPLOY_ENABLED must be true or false")
    if deploy_enabled_text == "true" and not ENVIRONMENT_PATTERN.fullmatch(automatic_environment):
        raise SystemExit("AUTOMATIC_ENVIRONMENT is invalid")

    output_path = Path(os.environ.get("METADATA_OUTPUT", "deployment-metadata.json"))
    output_path.write_text(
        json.dumps(
            {
                "source_sha": source_sha,
                "image_repository": image_repository,
                "image_digest": image_digest,
                "deploy_enabled": deploy_enabled_text == "true",
                "automatic_environment": automatic_environment,
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    return 0


def select_deployment(_: argparse.Namespace) -> int:
    event_name = required_env("EVENT_NAME")
    if event_name == "workflow_run":
        metadata = load_metadata(Path(required_env("METADATA_FILE")))
        source_sha = str(metadata.get("source_sha", ""))
        target_environment = str(metadata.get("automatic_environment", ""))
        image_digest = str(metadata.get("image_digest", ""))
        image_repository = str(metadata.get("image_repository", ""))
        deploy_enabled = metadata.get("deploy_enabled", True)
        if not isinstance(deploy_enabled, bool):
            raise SystemExit("Release metadata deploy_enabled must be a boolean")
        if not deploy_enabled:
            # Fall back to automatic_environment or "dev" if empty so validation passes.
            # Downstream jobs will be skipped because deploy_enabled evaluates to false.
            if not target_environment:
                target_environment = "dev"
        if source_sha != required_env("AUTOMATIC_SHA"):
            raise SystemExit("Release metadata source SHA does not match the triggering workflow run")
    elif event_name == "workflow_dispatch":
        source_sha = os.environ.get("MANUAL_SHA") or required_env("DEFAULT_MANUAL_SHA")
        target_environment = required_env("MANUAL_ENVIRONMENT")
        image_digest = os.environ.get("MANUAL_DIGEST", "")
        image_repository = ""
    else:
        raise SystemExit(f"Unsupported deployment event: {event_name}")

    if not SHA_PATTERN.fullmatch(source_sha):
        raise SystemExit("source_sha must be a full 40-character Git commit SHA")
    if not ENVIRONMENT_PATTERN.fullmatch(target_environment):
        raise SystemExit("target_environment is invalid")
    if image_digest and not DIGEST_PATTERN.fullmatch(image_digest):
        raise SystemExit("image_digest must be an immutable sha256 digest")

    write_outputs(
        {
            "source_sha": source_sha,
            "target_environment": target_environment,
            "image_digest": image_digest,
            "image_repository": image_repository,
        }
    )
    return 0


def validate_deployment(_: argparse.Namespace) -> int:
    event_name = required_env("EVENT_NAME")
    release_branch = required_env("RELEASE_BRANCH")
    if event_name == "workflow_run":
        trigger_branch = os.environ.get("TRIGGER_BRANCH", "")
        if trigger_branch and trigger_branch != release_branch:
            raise SystemExit(
                f"Release workflow ran on branch {trigger_branch!r}, but the platform "
                f"configuration requires releases to run on {release_branch!r}."
            )

    deploy_enabled = required_env("DEPLOY_ENABLED")
    if deploy_enabled == "false":
        print("Deployment is disabled by platform configuration; no deployment job will run.")
        return 0
    if deploy_enabled != "true":
        raise SystemExit("DEPLOY_ENABLED must be true or false")

    event_name = required_env("EVENT_NAME")
    target_environment = required_env("TARGET_ENVIRONMENT")
    automatic_environment = required_env("AUTOMATIC_ENVIRONMENT")
    if event_name == "workflow_run" and target_environment != automatic_environment:
        raise SystemExit("Release metadata does not target the configured automatic environment")

    chart = Path(required_env("HELM_CHART"))
    if not chart.is_dir():
        raise SystemExit(f"Helm chart directory not found: {chart}")
    for values_file in os.environ.get("HELM_VALUES_FILES", "").splitlines():
        if values_file and not Path(values_file).is_file():
            raise SystemExit(f"Helm values file not found: {values_file}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("create-metadata").set_defaults(func=create_metadata)
    commands.add_parser("select").set_defaults(func=select_deployment)
    commands.add_parser("validate-deployment").set_defaults(func=validate_deployment)
    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
