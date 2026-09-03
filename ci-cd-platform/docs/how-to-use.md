# How To Use This Template

## Prerequisites

Before running the pipeline, complete **all** of the following setup steps.
See the full checklist in the [Prerequisites](#prerequisites-checklist) section below.

---

## 1. Install the template files

Copy the following into the client repository root:

```
.github/
  workflows/
    ci.yaml
    release.yaml
    deploy.yaml
  actions/
    deploy-eks/
      action.yaml
    load-platform-config/
      action.yaml
  dependabot.yml
ci-cd-platform/
  .platform/
    config.yaml
    platform-config.schema.json
  scripts/
    deploy_eks.sh
    secret_scan.sh
    workflow_handoff.py
    platform_config.py
    helm_digest_post_renderer.py
    requirements.txt
```

GitHub discovers workflows only from `.github/workflows/` at the repository root.
`ci-cd-platform/` must stay beside the application source and Helm chart.

---

## 2. Configure the application

Edit `ci-cd-platform/.platform/config.yaml`:

### Application identity
- Set `application.name` (lowercase, hyphens only — used as a Helm release name default).
- Set `application.runtime`: `python` | `node` | `java` | `go` | `container`.
- Set `application.owner` for audit trail purposes.

### Quality pipeline
- Set the version for your runtime under `quality.runtime_versions`.
- Override `quality.commands.<runtime>.install`, `.lint`, and `.test` with your project's actual commands.
  The defaults are opinionated starting points; replace them with your real tool invocations.

### Source security
- `source_security.secret_scan` is **mandatory** — cannot be disabled.
- Configure `source_security.dependency_scan.severity` (default: `CRITICAL,HIGH`) and
  `dependency_scan.fail_on_findings` to match your policy.

### CI controls
- Toggle `ci.container_build_check.enabled` to enable/disable the PR Docker build check.

### Release pipeline
- Set `release.branch` to match the literal in `release.yaml`'s `on.push.branches`.
- Set `release.container.repository` to your ECR repository path.
- Set `release.container.dockerfile` and `release.container.context` (repository-root-relative).
- Set `release.container.platforms` (e.g., `linux/amd64`, `linux/arm64`).
- Add any `release.container.build_args` your Dockerfile requires.
- Fill in `release.aws.account_id`, `release.aws.region`, and `release.aws.oidc_role_arn`.
- Configure `release.image_security.container_scan.severity`; the immutable-image scan is mandatory.
- Leave `release.image_security.sbom.enabled: false` for the lowest-storage default, or enable it
  and set `retention_days` when an SPDX-JSON artifact is required.
- `release.image_security.provenance.enabled` controls free registry-native BuildKit provenance.

### Optional GitHub integrations and cost controls

- Keep `integrations.github.*.enabled: false` for the private GitHub Free default.
- Enable `code_scanning` only when GitHub Code Security is available for the private repository.
- Enable `artifact_attestation` only when the private repository is on GitHub Enterprise Cloud.
- Enable `deployment_environments` only when the client's plan supports the desired Environment
  features and the AWS OIDC trust policy has been configured for an environment subject.
- Keep `cost_controls.docker_build_records.enabled: false` unless Buildx diagnostic artifacts
  are worth the storage usage. Trivy caching normally remains cost-effective.

### Deploy pipeline
- Set `deploy.automatic_environment` (the environment deployed automatically after every Release).
- For each environment (`dev`, `staging`, `production`, or custom):
  - Set `github_environment` for future use even when the optional integration is disabled.
  - Fill in `aws.region` and `aws.oidc_role_arn` for the EKS deployer role.
  - Set `eks.cluster_name`.
  - Set `helm.release_name`, `helm.namespace`, `helm.chart`, `helm.values`.
  - Configure `helm.atomic` (`false` for dev, `true` for staging/production is recommended).
  - Optionally set `helm.container_name` when your Pod spec has sidecars.
  - Optionally set `helm.create_namespace: false` for pre-provisioned namespaces.
  - Optionally tune `helm.history_max` (default `10`) and `helm.timeout` (default `10m`).

---

## 3. Keep the release branch in sync

The `on.push.branches` filter in `release.yaml` **must be a literal**. GitHub cannot read it dynamically from config:

```yaml
on:
  push:
    branches:
      - main  # Must match release.branch in config.yaml
```

If you change the release branch, update **both** `release.yaml` and `config.yaml` together.

---

## 4. Configure AWS OIDC

**ECR publisher role** (used by Release):
- Trust: `token.actions.githubusercontent.com` audience `sts.amazonaws.com`
  — scoped to `repo:<org>/<repo>:ref:refs/heads/<release-branch>`
- Permissions: `ecr:GetAuthorizationToken`, `ecr:BatchCheckLayerAvailability`,
  `ecr:InitiateLayerUpload`, `ecr:UploadLayerPart`, `ecr:CompleteLayerUpload`,
  `ecr:PutImage`, `ecr:DescribeImages`
- **No EKS or unrelated AWS permissions.**

**EKS deployer role** (used by Deploy, one per environment is recommended):
- Free/default direct-job trust: scoped to `repo:<org>/<repo>:ref:refs/heads/main`.
- Optional Environment-job trust: scoped to `repo:<org>/<repo>:environment:<environment-name>`.
- Use the trust form matching `integrations.github.deployment_environments.enabled`; changing
  that switch changes the OIDC subject presented to AWS.
- Permissions: `ecr:DescribeImages`, `eks:DescribeCluster`
- Kubernetes RBAC: `get/list/create/update/patch/delete` on `deployments`,
  `replicasets`, `services`, `configmaps`, `secrets` in the target namespace only.

---

## 5. Optionally configure GitHub Environments

Skip this step for the free-default direct deployment path. When the client's GitHub plan
supports the required private-repository Environment features, create one environment per
deployment target and set `integrations.github.deployment_environments.enabled: true`.

For `staging` and `production`:
- Add required reviewers for manual approval.
- Add a deployment branch rule restricting to `main` (or your release branch).

---

## 6. Provide environment Helm values files

The pipeline does not generate values files. Create:
- `deployment-config/values/dev.yaml`
- `deployment-config/values/staging.yaml`
- `deployment-config/values/production.yaml`

Or adjust the `helm.values` list in `config.yaml` to match your actual paths.

---

## 7. Add a Dockerfile

The pipeline expects a `Dockerfile` at the repository root by default. Change
`release.container.dockerfile` and `release.container.context` in `config.yaml` if yours is elsewhere.

---

## 8. Run CI and Release

- Open a pull request → CI runs (no AWS credentials).
- Merge to the release branch → Release validates, scans, builds, and publishes to ECR.
- A successful push-triggered Release automatically deploys to `automatic_environment` (default: `dev`).
- Manual deploy: use the Deploy workflow's `workflow_dispatch` to select `dev`, `staging`, or `production`.

---

## Prerequisites Checklist

| # | Item | Where |
|---|---|---|
| 1 | GitHub repository with Actions enabled | GitHub |
| 2 | `config.yaml` fully populated (no placeholder values) | `ci-cd-platform/.platform/` |
| 3 | AWS account with ECR repository created | AWS |
| 4 | ECR publisher IAM role with OIDC trust | AWS IAM |
| 5 | EKS deployer IAM role(s) with OIDC trust | AWS IAM |
| 6 | EKS cluster running and accessible | AWS EKS |
| 7 | Kubernetes RBAC for deployer role in each target namespace | EKS |
| 8 | Helm values files exist for each enabled deployment environment | Repository |
| 9 | `Dockerfile` present at configured path | Repository |
| 10 | Helm chart present at configured `helm.chart` path | Repository |
| 11 | Dependabot enabled to keep Actions and Python dependencies updated | `.github/dependabot.yml` ✅ |

Optional, plan-dependent setup:

| Feature switch | Additional prerequisite for a private repository |
|---|---|
| `integrations.github.code_scanning.enabled` | GitHub Code Security enabled |
| `integrations.github.artifact_attestation.enabled` | GitHub Enterprise Cloud |
| `integrations.github.deployment_environments.enabled` | A plan supporting private-repository Environments and any desired protection rules; matching AWS OIDC environment trust |
