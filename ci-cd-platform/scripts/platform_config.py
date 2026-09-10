#!/usr/bin/env python3
"""Read, validate, and normalize the client-owned CI/CD platform configuration."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import uuid
from pathlib import Path
from typing import Any, Optional

import jsonschema
import yaml


DEFAULT_RUNTIME_VERSIONS = {
    "python": "3.12",
    "node": "22",
    "java": "21",
    "go": "1.23",
}

# Default quality commands per runtime.
# Python lint uses ruff (fast, modern linter) instead of compileall which only
# checks syntax. Clients should override these in config.yaml as needed.
DEFAULT_COMMANDS = {
    "python": {
        "install": "python -m pip install --disable-pip-version-check -r requirements.txt",
        "lint": "python -m ruff check .",
        "test": "python -m pytest",
    },
    "node": {
        "install": "npm ci",
        "lint": "npm run lint --if-present",
        "test": "npm test --if-present",
    },
    "java": {
        "install": "mvn -B -ntp dependency:go-offline",
        "lint": "mvn -B -ntp validate",
        "test": "mvn -B -ntp test",
    },
    "go": {
        "install": "go mod download",
        "lint": "go vet ./...",
        "test": "go test -race ./...",
    },
    "container": {
        "install": "true",
        "lint": "true",
        "test": "true",
    },
}

PLATFORM_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG_PATH = PLATFORM_ROOT / ".platform/config.yaml"
DEFAULT_SCHEMA_PATH = PLATFORM_ROOT / ".platform/platform-config.schema.json"


def load_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise SystemExit(f"File not found: {path}")
    with path.open("r", encoding="utf-8") as handle:
        data = yaml.safe_load(handle) or {}
    if not isinstance(data, dict):
        raise SystemExit(f"YAML root must be a mapping: {path}")
    return data


def load_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise SystemExit(f"File not found: {path}")
    with path.open("r", encoding="utf-8") as handle:
        data = json.load(handle)
    if not isinstance(data, dict):
        raise SystemExit(f"JSON root must be an object: {path}")
    return data


def bool_str(value: Any) -> str:
    return "true" if bool(value) else "false"


def exit_code(enabled: bool, fail_on_findings: bool) -> str:
    return "1" if enabled and fail_on_findings else "0"


def write_outputs(values: dict[str, Any]) -> None:
    output_path = os.environ.get("GITHUB_OUTPUT")
    if not output_path:
        print(json.dumps(values, indent=2, sort_keys=True))
        return
    with open(output_path, "a", encoding="utf-8") as handle:
        for key, value in values.items():
            text = "" if value is None else str(value)
            if "\n" in text:
                delimiter = f"PLATFORM_OUTPUT_{uuid.uuid4().hex}"
                handle.write(f"{key}<<{delimiter}\n{text}\n{delimiter}\n")
            else:
                handle.write(f"{key}={text}\n")


def validate_config(config_path: Path, schema_path: Path) -> dict[str, Any]:
    config = load_yaml(config_path)
    schema = load_json(schema_path)
    jsonschema.validate(instance=config, schema=schema)

    source_security = config["source_security"]
    image_security = config["release"]["image_security"]

    # Verify mandatory controls that the schema already enforces via const: true.
    # This secondary check provides a clear error message if schema validation
    # is somehow bypassed or the schema file is tampered with.
    mandatory = {
        "source_security.secret_scan.enabled": source_security["secret_scan"]["enabled"],
        "source_security.secret_scan.fail_on_findings": source_security["secret_scan"]["fail_on_findings"],
        "release.image_security.container_scan.enabled": image_security["container_scan"]["enabled"],
        # container_scan.fail_on_findings is configurable; enabled=true is still mandatory above.
    }
    disabled = [name for name, enabled in mandatory.items() if not enabled]
    if disabled:
        raise SystemExit("Mandatory security controls cannot be disabled: " + ", ".join(disabled))

    deploy = config["deploy"]
    automatic_environment = deploy.get("automatic_environment", "")
    if deploy.get("enabled", True) and automatic_environment not in deploy["environments"]:
        raise SystemExit(
            f"deploy.automatic_environment {automatic_environment!r} is not defined in deploy.environments"
        )
    return config


def normalized(config: dict[str, Any], target_environment: Optional[str] = None) -> dict[str, Any]:
    app = config.get("application", {})
    runtime = app.get("runtime", "python")
    if runtime not in DEFAULT_COMMANDS:
        raise SystemExit(f"Unsupported runtime: {runtime}")

    quality = config.get("quality", {})
    release = config.get("release", {})
    runtime_versions = {**DEFAULT_RUNTIME_VERSIONS, **quality.get("runtime_versions", {})}
    configured_commands = quality.get("commands", {}).get(runtime, {})
    commands = {**DEFAULT_COMMANDS[runtime], **configured_commands}
    container = release.get("container", {})
    aws = release.get("aws", {})
    source_security = config.get("source_security", {})
    image_security = release.get("image_security", {})
    ci = config.get("ci", {})
    deploy = config.get("deploy", {})
    github_integrations = config.get("integrations", {}).get("github", {})
    cost_controls = config.get("cost_controls", {})

    build_args = container.get("build_args", {}) or {}
    build_args_text = "\n".join(f"{key}={value}" for key, value in build_args.items())

    dependency_scan = source_security.get("dependency_scan", {})
    container_scan = image_security.get("container_scan", {})
    secret_scan = source_security.get("secret_scan", {})
    sbom = image_security.get("sbom", {})
    provenance = image_security.get("provenance", {})
    build_check = ci.get("container_build_check", {})
    code_scanning = github_integrations.get("code_scanning", {})
    artifact_attestation = github_integrations.get("artifact_attestation", {})
    deployment_environments = github_integrations.get("deployment_environments", {})
    docker_build_records = cost_controls.get("docker_build_records", {})
    trivy_cache = cost_controls.get("trivy_cache", {})

    deploy_enabled = deploy.get("enabled", True)
    automatic_environment = deploy.get("automatic_environment", "")
    # target_environment is passed for deployment workflows; None for CI/Release quality jobs.
    selected_environment = target_environment or automatic_environment
    environments = deploy.get("environments", {})
    if deploy_enabled and selected_environment not in environments:
        available = ", ".join(sorted(environments))
        raise SystemExit(
            f"Unknown deployment environment {selected_environment!r}; available environments: {available}"
        )
    environment_config = environments.get(selected_environment, {})
    deploy_aws = environment_config.get("aws", {})
    eks = environment_config.get("eks", {})
    helm = environment_config.get("helm", {})

    helm_values = helm.get("values", [])
    helm_values_args = "\n".join(helm_values)
    helm_set_values = helm.get("set_values", {}) or {}
    helm_set_values_args = "\n".join(f"{key}={value}" for key, value in helm_set_values.items())

    return {
        "application_name": app["name"],
        "runtime": runtime,
        "python_version": runtime_versions.get("python", DEFAULT_RUNTIME_VERSIONS["python"]),
        "node_version": runtime_versions.get("node", DEFAULT_RUNTIME_VERSIONS["node"]),
        "java_version": runtime_versions.get("java", DEFAULT_RUNTIME_VERSIONS["java"]),
        "go_version": runtime_versions.get("go", DEFAULT_RUNTIME_VERSIONS["go"]),
        "install_command": commands["install"],
        "lint_command": commands["lint"],
        "test_command": commands["test"],
        "dockerfile": container.get("dockerfile", "Dockerfile"),
        "image_context": container.get("context", "."),
        "platforms": ",".join(container.get("platforms", ["linux/amd64"])),
        "build_args": build_args_text,
        "ecr_repository": container["repository"],
        "aws_account_id": aws["account_id"],
        "aws_region": aws["region"],
        "aws_role_arn": aws["oidc_role_arn"],
        "release_branch": release.get("branch", "main"),
        # Secret scan: mandatory — enabled and fail_on_findings are always true per schema.
        "secret_scan_enabled": bool_str(secret_scan.get("enabled", True)),
        # Dependency scan: configurable by clients.
        "dependency_scan_enabled": bool_str(dependency_scan.get("enabled", True)),
        "dependency_scan_severity": dependency_scan.get("severity", "CRITICAL,HIGH"),
        "dependency_scan_exit_code": exit_code(
            dependency_scan.get("enabled", True),
            dependency_scan.get("fail_on_findings", True),
        ),
        # Container scan: mandatory — enabled and fail_on_findings are always true per schema.
        "container_scan_enabled": bool_str(container_scan.get("enabled", True)),
        "container_scan_severity": container_scan.get("severity", "CRITICAL,HIGH"),
        "container_scan_exit_code": exit_code(
            container_scan.get("enabled", True),
            container_scan.get("fail_on_findings", True),
        ),
        # SBOM storage is optional and disabled by default for private GitHub Free repositories.
        "sbom_enabled": bool_str(sbom.get("enabled", False)),
        "sbom_retention_days": sbom.get("retention_days", 7),
        # BuildKit provenance is registry-native and separate from GitHub attestations.
        "provenance_enabled": bool_str(provenance.get("enabled", True)),
        "code_scanning_enabled": bool_str(code_scanning.get("enabled", False)),
        "artifact_attestation_enabled": bool_str(artifact_attestation.get("enabled", False)),
        "github_environments_enabled": bool_str(deployment_environments.get("enabled", False)),
        "docker_build_record_enabled": bool_str(docker_build_records.get("enabled", False)),
        "docker_build_record_retention_days": docker_build_records.get("retention_days", 1),
        "trivy_cache_enabled": bool_str(trivy_cache.get("enabled", True)),
        "deployment_metadata_retention_days": release.get("deployment_metadata_retention_days", 1),
        "build_check_enabled": bool_str(build_check.get("enabled", False)),
        # Deployment configuration.
        "deploy_enabled": bool_str(deploy_enabled),
        "automatic_environment": automatic_environment,
        "deploy_environment": selected_environment,
        "github_environment": environment_config.get("github_environment", selected_environment),
        "deploy_aws_region": deploy_aws.get("region", aws.get("region", "")),
        "deploy_aws_role_arn": deploy_aws.get("oidc_role_arn", ""),
        "eks_cluster_name": eks.get("cluster_name", ""),
        "helm_release_name": helm.get("release_name", app["name"]),
        "helm_namespace": helm.get("namespace", app["name"]),
        "helm_chart": helm.get("chart", "helm-chart"),
        "helm_values_args": helm_values_args,
        "helm_set_values_args": helm_set_values_args,
        # Empty string means target the first container (post-renderer / rollout verify fallback).
        "helm_container_name": helm.get("container_name", ""),
        # Default false: helm_atomic default is false (safe for dev, set true for staging/prod).
        "helm_atomic": bool_str(helm.get("atomic", False)),
        "helm_create_namespace": bool_str(helm.get("create_namespace", True)),
        "helm_history_max": helm.get("history_max", 10),
        "helm_timeout": helm.get("timeout", "10m"),
    }


def validate_command(args: argparse.Namespace) -> int:
    config = validate_config(Path(args.config), Path(args.schema))
    values = normalized(config, args.environment or None)
    if args.github_output:
        write_outputs(values)
    else:
        print(json.dumps(values, indent=2, sort_keys=True))
    return 0


def run_configured_command(args: argparse.Namespace) -> int:
    """Execute install, lint, or test command for the configured runtime.

    Environment resolution is intentionally skipped here (normalized called
    with no target_environment) because install/lint/test commands are
    runtime-specific, not environment-specific. The automatic_environment is
    used only to satisfy the environment resolution step inside normalized().
    """
    config = validate_config(Path(args.config), Path(args.schema))
    values = normalized(config)
    command = values[f"{args.command}_command"]
    print(f"Running {args.command}: {command}")
    return subprocess.call(command, shell=True)


def main() -> int:
    parser = argparse.ArgumentParser()
    subcommands = parser.add_subparsers(dest="command_name", required=True)

    validate_parser = subcommands.add_parser("validate")
    validate_parser.add_argument("--config", default=str(DEFAULT_CONFIG_PATH))
    validate_parser.add_argument("--schema", default=str(DEFAULT_SCHEMA_PATH))
    validate_parser.add_argument("--environment", default="")
    validate_parser.add_argument("--github-output", action="store_true")
    validate_parser.set_defaults(func=validate_command)

    command_parser = subcommands.add_parser("command")
    command_parser.add_argument("--config", default=str(DEFAULT_CONFIG_PATH))
    command_parser.add_argument("--schema", default=str(DEFAULT_SCHEMA_PATH))
    command_parser.add_argument("--command", choices=["install", "lint", "test"], required=True)
    command_parser.set_defaults(func=run_configured_command)

    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
