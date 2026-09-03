# Configuration Reference

`ci-cd-platform/.platform/config.yaml` is the single file that controls all CI, Release,
and Deploy pipeline behaviour. It is validated against
`ci-cd-platform/.platform/platform-config.schema.json` at the start of every pipeline run.

---

## `application` — Application Identity

| Field | Type | Required | Default | Description |
|---|---|---|---|---|
| `name` | string | ✅ | — | Lowercase alphanumeric + hyphens. Used as a Helm release name and artifact label default. |
| `runtime` | enum | — | `python` | `python` \| `node` \| `java` \| `go` \| `container` |
| `owner` | string | — | — | Team or individual owner; informational only. |

---

## `quality` — Runtime Quality Pipeline

Executed by both CI (on PRs) and Release (on merge). Only the runtime matching
`application.runtime` is installed.

### `quality.runtime_versions`

| Field | Type | Default | Description |
|---|---|---|---|
| `python` | string | `"3.12"` | Python version passed to `actions/setup-python`. |
| `node` | string | `"22"` | Node version passed to `actions/setup-node`. |
| `java` | string | `"21"` | Java version passed to `actions/setup-java` (Temurin distribution). |
| `go` | string | `"1.23"` | Go version passed to `actions/setup-go`. |

### `quality.commands.<runtime>`

Each runtime block must have `install`, `lint`, and `test` — all shell commands run from
the repository root.

| Runtime | Default `install` | Default `lint` | Default `test` |
|---|---|---|---|
| `python` | `pip install -r requirements.txt` | `ruff check .` | `pytest` |
| `node` | `npm ci` | `npm run lint --if-present` | `npm test --if-present` |
| `java` | `mvn dependency:go-offline` | `mvn validate` | `mvn test` |
| `go` | `go mod download` | `go vet ./...` | `go test -race ./...` |
| `container` | `true` (no-op) | `true` (no-op) | `true` (no-op) |

Override any command in `config.yaml` to use your project's real tools (e.g., `ruff check`,
`eslint`, `golangci-lint`).

---

## `source_security` — Source & Dependency Scanning

### `source_security.secret_scan` — **Mandatory**

| Field | Type | Allowed | Description |
|---|---|---|---|
| `enabled` | bool | `true` only | Cannot be disabled. Gitleaks scans full Git history. |
| `fail_on_findings` | bool | `true` only | Cannot be soft-failed. Any finding blocks the pipeline. |

### `source_security.dependency_scan` — Configurable

| Field | Type | Default | Description |
|---|---|---|---|
| `enabled` | bool | `true` | Toggle Trivy filesystem/dependency scan. |
| `fail_on_findings` | bool | `true` | Whether findings block the pipeline. |
| `severity` | string | `CRITICAL,HIGH` | Comma-separated Trivy severities: `CRITICAL`, `HIGH`, `MEDIUM`, `LOW`. |

---

## `ci` — Pull-Request Pipeline Controls

### `ci.container_build_check`

| Field | Type | Default | Description |
|---|---|---|---|
| `enabled` | bool | `false` | Build the configured Dockerfile on every PR without pushing. This duplicates the Release build, so the cost-sensitive default disables it. |

`ci.timeout_minutes` is accepted only for compatibility with older copied configurations.
Current workflows do not apply job-level timeouts.

---

## `release` — Build & Publish Pipeline

### `release` top-level

| Field | Type | Default | Description |
|---|---|---|---|
| `branch` | string | `main` | The trusted release branch. Used by Deploy to verify source ancestry. **Must match the literal in `release.yaml` `on.push.branches`.** |
| `deployment_metadata_retention_days` | int (1–90) | `1` | Days to retain the small automatic-Deploy hand-off artifact. |

### `release.container`

| Field | Type | Required | Default | Description |
|---|---|---|---|---|
| `registry` | enum | ✅ | — | Must be `ecr`. |
| `repository` | string | ✅ | — | ECR repository path, e.g., `client-a/sample-app`. |
| `dockerfile` | string | — | `Dockerfile` | Repository-root-relative path to the Dockerfile. |
| `context` | string | — | `.` | Repository-root-relative Docker build context. |
| `platforms` | list | — | `[linux/amd64]` | Docker Buildx platform targets, e.g., `linux/amd64`, `linux/arm64`. |
| `build_args` | map | — | `{}` | Build-time ARGs passed to Docker Buildx. Never store secrets here. |

### `release.aws`

| Field | Type | Required | Description |
|---|---|---|---|
| `account_id` | string (12 digits) | ✅ | AWS account ID hosting the ECR repository. |
| `region` | string | ✅ | AWS region, e.g., `us-east-1`. |
| `oidc_role_arn` | string | ✅ | ARN of the IAM role assumed via GitHub OIDC for ECR push. No EKS permissions. |

### `release.image_security`

#### `release.image_security.container_scan` — **Mandatory**

| Field | Type | Allowed | Default | Description |
|---|---|---|---|---|
| `enabled` | bool | `true` only | — | Cannot be disabled. Trivy scans the pushed digest. |
| `fail_on_findings` | bool | `true` only | — | Cannot be soft-failed. |
| `severity` | string | — | `CRITICAL,HIGH` | Comma-separated Trivy severities to fail on. |

#### `release.image_security.sbom` — Optional artifact

| Field | Type | Default | Description |
|---|---|---|---|
| `enabled` | bool | `false` | Generate and upload an SPDX-JSON SBOM with Syft. Disabled by default to avoid artifact storage in private repositories. |
| `retention_days` | int (1–90) | `7` | Days to retain the SBOM artifact when enabled. |

#### `release.image_security.provenance` — Optional

| Field | Type | Default | Description |
|---|---|---|---|
| `enabled` | bool | `true` | Generate registry-native BuildKit/OCI provenance with the image. This does not enable GitHub artifact attestation. |

---

## `integrations.github` — Plan-Dependent GitHub Features

All switches default to `false`, allowing a private repository to use the core pipeline on
GitHub Free. Enable a switch only after confirming that the client's GitHub plan and repository
settings support it.

| Field | Default | Effect when enabled |
|---|---|---|
| `code_scanning.enabled` | `false` | Adds separate non-blocking-format SARIF upload jobs. Private repositories require GitHub Code Security. Core Trivy scans remain blocking even when this is disabled. |
| `artifact_attestation.enabled` | `false` | Adds GitHub artifact attestation after publishing. Private repository support requires GitHub Enterprise Cloud. BuildKit provenance remains independently controlled by `release.image_security.provenance.enabled`. |
| `deployment_environments.enabled` | `false` | Runs deployment through the configured GitHub Environment, enabling any available gates and environment-scoped policies. Private repository availability depends on the GitHub plan. When disabled, deployment runs directly with the same digest and OIDC checks. |

---

## `cost_controls` — Actions Usage Controls

| Field | Default | Description |
|---|---|---|
| `docker_build_records.enabled` | `false` | Upload Docker Buildx diagnostic records. These consume artifact storage, so the free default disables them. |
| `docker_build_records.retention_days` | `1` | Build-record retention when enabled. |
| `trivy_cache.enabled` | `true` | Cache the Trivy database. Cache storage is quota-metered, but this normally saves more runner time and network traffic than disabling it. |

---

## `deploy` — Kubernetes Deployment Pipeline

### `deploy` top-level

| Field | Type | Default | Description |
|---|---|---|---|
| `enabled` | bool | `true` | Master toggle. Set `false` to make the Deploy workflow validate and then skip both deployment jobs. No environment definitions are required when disabled. |
| `automatic_environment` | string | — | The environment deployed automatically after every successful push-triggered Release. Must exist in `environments`. |

### `deploy.environments.<name>` — Per-Environment Configuration

Define one block per environment. Names must be lowercase alphanumeric + hyphens.

#### Identity

| Field | Type | Required | Description |
|---|---|---|---|
| `github_environment` | string | ✅ | GitHub Environment name used only when `integrations.github.deployment_environments.enabled` is true. |

#### `aws`

| Field | Type | Required | Description |
|---|---|---|---|
| `region` | string | ✅ | AWS region for EKS and ECR access. |
| `oidc_role_arn` | string | ✅ | ARN of the EKS deployer IAM role. Separate from the ECR publisher role. |

#### `eks`

| Field | Type | Required | Description |
|---|---|---|---|
| `cluster_name` | string | ✅ | EKS cluster name. Used with `aws eks update-kubeconfig`. |

#### `helm`

| Field | Type | Required | Default | Description |
|---|---|---|---|---|
| `release_name` | string | ✅ | — | Helm release name. |
| `namespace` | string | ✅ | — | Kubernetes namespace to deploy into. |
| `chart` | string | ✅ | — | Repository-root-relative path to the Helm chart directory. |
| `values` | list | ✅ | — | Repository-root-relative paths to values files. Empty list `[]` is valid when using only `set_values`. |
| `set_values` | map | — | `{}` | Explicit `--set-string` overrides. Non-sensitive only — never store secrets here. |
| `container_name` | string | — | `""` | Name of the application container in the Pod spec. Empty targets the first container. **Set this when your chart has sidecars** to ensure digest verification patches and checks the correct container. |
| `create_namespace` | bool | — | `true` | Pass `--create-namespace` to Helm. Set `false` when namespaces must be pre-provisioned with RBAC/LimitRange/ResourceQuota. |
| `history_max` | int (1–50) | — | `10` | Maximum Helm release revisions retained in the cluster (`--history-max`). Prevents etcd growth. |
| `timeout` | string | — | `10m` | Helm wait timeout. Accepts simple (`10m`, `30s`) and compound (`10m30s`, `1h30m`) durations. |
| `atomic` | bool | — | `false` | When `true`, Helm rolls back to the previous successful release on failure. Recommended `true` for `staging` and `production`. |

---

## Mandatory vs. Configurable Controls Summary

| Control | Mandatory | Configurable |
|---|---|---|
| Secret scanning (Gitleaks) | ✅ Always on, always fails | Severity N/A |
| Dependency scan (Trivy fs) | — | Enable/disable, severity, fail threshold |
| Container build check (PR) | — | Enable/disable |
| Container scan (Trivy image) | ✅ Always on, always fails | Severity |
| SBOM generation (Syft/SPDX) | — | Enable/disable, retention days; default off |
| BuildKit/OCI provenance | — | Enable/disable; default on |
| GitHub artifact attestation | — | Plan-dependent toggle; default off |
| GitHub Code Scanning upload | — | Plan-dependent toggle; default off |
| GitHub Environment job | — | Plan-dependent toggle; default off |
| Docker build records | — | Enable/disable, retention; default off |
| Helm atomic rollback | — | Per environment |
| Deployment enabled | — | Global toggle |
