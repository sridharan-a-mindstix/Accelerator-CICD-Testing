# Pipeline Walkthrough

## CI — Pull Request Validation

CI runs on every pull request and on manual dispatch (`workflow_dispatch`).
**No AWS credentials are issued at any point.**

### Jobs (run in parallel where possible)

```
validate ──┬── quality ─────────────┐
           ├── security ────────────┼──> build-check
           └── code-scanning (opt.) ┘
```

### Steps

1. **Validate configuration** (`validate` job)
   - Checks out the PR source branch.
   - Installs Python helper dependencies (`jsonschema`, `PyYAML`).
   - Validates `ci-cd-platform/.platform/config.yaml` against `platform-config.schema.json`.
   - Rejects any config that disables mandatory controls (secret scan and container scan).
   - Emits normalized outputs (runtime, versions, commands, and feature flags) for
     downstream jobs.

2. **Runtime quality checks** (`quality` job — depends on `validate`)
   - Installs only the runtime matching `application.runtime`.
   - Runs `quality.commands.<runtime>.install`, then `.lint`, then `.test`.

3. **Source security checks** (`security` job — depends on `validate`, runs in parallel with `quality`)
   - Checks out full Git history (`fetch-depth: 0`).
   - Runs **Gitleaks** (`ghcr.io/gitleaks/gitleaks:v8.30.1`) as a read-only Docker container against `/repo`.
     Secret scanning is mandatory; any finding fails the pipeline with no override.
   - Runs **Trivy** filesystem/dependency scan when `source_security.dependency_scan.enabled: true`.
     Severity and fail threshold are configurable.
   - Uses table output so the blocking scan works without GitHub Code Security.

4. **Code Scanning upload** (`code-scanning` job — optional)
   - Runs only when `integrations.github.code_scanning.enabled: true`.
   - Generates Trivy SARIF and uploads it with narrowly scoped `security-events: write` permission.
   - Skips pull requests from forks because GitHub does not grant the required write token there.
   - This is an additional reporting job; disabling it does not disable the blocking core scan.

5. **Container build check** (`build-check` job — depends on `validate`, `quality`, `security`)
   - Runs only when `ci.container_build_check.enabled: true`; the free/cost-sensitive default is off.
   - Builds the configured Dockerfile using the same `platforms` and `build_args` as Release.
   - Does **not** push; no registry credentials needed.
   - Confirms the image can be built before any code merges.

---

## Release — Build & Publish

Release runs automatically on push to the configured release branch (`main` by default),
or by manual dispatch (restricted to the same branch).

### Jobs (sequential)

```
validate → quality → source-security → publish ──┬── image-code-scanning (optional)
                                                 └── artifact-attestation (optional)
```

### Steps

1. **Validate configuration** (`validate` job)
   - Same validation as CI.
   - Additionally emits AWS, ECR, SBOM, and deployment metadata outputs.
   - Enforces branch restriction for manual dispatch: `workflow_dispatch` from any branch
     other than `release.branch` fails immediately.

2. **Runtime quality checks** (`quality` job)
   - Identical to CI quality — prevents direct pushes from bypassing tests.

3. **Source security checks** (`source-security` job)
   - Identical to CI security — blocking Gitleaks scan + Trivy dependency scan.

4. **Build, scan, and publish** (`publish` job — depends on all above)
   - Authenticates to AWS using GitHub OIDC (ECR publisher role only).
   - Logs in to Amazon ECR.
   - Builds and pushes the image tagged with the full Git commit SHA.
   - Captures the immutable `sha256:...` digest from ECR.
   - Runs **Trivy** container scan against the exact pushed digest.
   - Optionally generates and uploads one **SPDX-JSON SBOM** using Syft/anchore.
     It is disabled by default to avoid private-repository artifact storage.
   - Optionally emits registry-native BuildKit provenance with the image.
   - Creates and uploads `deployment-metadata.json` containing:
     - `source_sha` — the full 40-char commit SHA
     - `image_repository` — ECR URI without tag
     - `image_digest` — the immutable `sha256:...` digest
     - `deploy_enabled` — whether an automatic deployment job is allowed
     - `automatic_environment` — the configured default deploy target
   - If any mandatory control fails, the publish job fails and automatic Deploy does not trigger.

5. **Paid integration jobs** (optional, after `publish`)
   - `image-code-scanning` re-scans the immutable image and uploads SARIF only when
     `integrations.github.code_scanning.enabled: true`.
   - `artifact-attestation` creates the GitHub-native attestation only when
     `integrations.github.artifact_attestation.enabled: true`.
   - If an enabled integration fails, the overall Release fails, so automatic Deploy does not run.

---

## Deploy — Kubernetes Deployment

Deploy starts automatically after a successful push-triggered Release, or by manual dispatch.

### Triggers

| Trigger | Condition | Target |
|---|---|---|
| `workflow_run` (automatic) | Release succeeded + triggered by `push` | `automatic_environment` from metadata |
| `workflow_dispatch` (manual) | User selects environment | `dev`, `staging`, or `production` |

### Jobs

```
prepare ──┬── deploy-direct (free default)
          └── deploy-protected (optional GitHub Environment)
```

### Steps

**`prepare` job** — validates source and loads config:

1. Downloads the `deployment-metadata` artifact from the triggering Release run (automatic only).
2. Selects `source_sha`, `target_environment`, and `image_digest`:
   - Automatic: from metadata; verifies `source_sha` matches the triggering Release's `head_sha`.
   - Manual: from dispatch inputs; defaults SHA to current HEAD.
3. Validates formats: 40-char hex SHA, `^[a-z0-9-]+$` environment name, `sha256:[64 hex]` digest.
4. Checks out the exact `source_sha` commit.
5. **Verifies the source SHA belongs to the configured release branch history** (derived from
   `release.branch` in `config.yaml`) before any repository-owned helper runs.
6. Loads the target environment's configuration via `load-platform-config`.
7. Confirms automatic deployments target `automatic_environment` from config.
8. Confirms the Helm chart directory and all configured values files exist.

Exactly one deployment job runs:

- **`deploy-direct`** is the free default and does not reference a GitHub Environment.
- **`deploy-protected`** runs in the configured GitHub Environment only when
  `integrations.github.deployment_environments.enabled: true`.

Both variants use the same local deployment action and security checks:

1. Configures AWS credentials via GitHub OIDC (EKS deployer role — separate from ECR publisher).
2. **Validates and resolves the immutable ECR image digest**:
   - If a digest was provided manually, cross-verifies it matches the SHA tag in ECR (prevents
     deploying an image built from a different commit).
   - If no digest, resolves by looking up `imageTag=<source_sha>` in ECR.
   - Confirms the resolved digest exists in the configured ECR repository.
3. Configures `kubectl` using `aws eks update-kubeconfig` (ephemeral kubeconfig).
4. Verifies EKS connectivity and RBAC preflight (`get`, `list`, `create`, `update`, `patch`
   on `deployments.apps` in the target namespace).
5. Installs pinned Helm version.
6. Lints the Helm chart with environment values files.
7. Runs `helm upgrade --install --wait` with:
   - `--timeout` from `helm.timeout`
   - `--history-max` from `helm.history_max`
   - `--atomic` when `helm.atomic: true` (staging/production)
   - `--create-namespace` when `helm.create_namespace: true`
   - The Helm digest post-renderer (`helm_digest_post_renderer.py`) which replaces
     the application container image with `repository@sha256:digest`, bypassing any
     mutable image tag in the values files.
8. **Verifies rollout and digest** — checks every Deployment's application container image
   matches `repository@sha256:digest`. Uses `helm.container_name` when set; falls back to
   `containers[0]` when empty.
9. On failure: reports `helm status`, pod state, and recent cluster events (no container logs).

### Rollback Behaviour

| Environment | `atomic` | On Helm failure | On post-deploy verify failure |
|---|---|---|---|
| `dev` | `false` | No rollback (state preserved for debugging) | No automatic rollback |
| `staging` | `true` | Helm rolls back to previous release | No automatic rollback |
| `production` | `true` | Helm rolls back to previous release | No automatic rollback |

> Post-deployment verification failures (after Helm succeeds) do not trigger an automatic
> rollback. An explicit manual rollback via `helm rollback` or a new Deploy run is required.

---

## Concurrency Controls

| Scope | Behaviour |
|---|---|
| CI | Cancels in-progress runs for the same PR branch. |
| Release | Never cancels in-progress runs (prevents race conditions on `main`). |
| Deploy | Serializes per application + environment; different environments run independently. |
