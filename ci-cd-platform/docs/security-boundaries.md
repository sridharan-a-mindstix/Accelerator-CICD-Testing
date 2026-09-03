# Security Boundaries

## Pull-Request CI

Core CI receives only `contents: read`. The optional Code Scanning job receives
`security-events: write` only when that integration is enabled.

**No credentials are issued:**
- No AWS credentials
- No ECR access
- No EKS or Kubernetes credentials
- No repository secrets

Gitleaks runs as a read-only Docker container mounted at `/repo`. The container is run
with the runner's UID/GID and `--rm`. No `GITHUB_TOKEN` or AWS credentials are passed
to the container.

---

## Release

Only the `publish` job in Release can request a GitHub OIDC token (`id-token: write`).

```
GitHub OIDC → ECR publisher IAM role → configured ECR repository only
```

The OIDC trust should be scoped to:
- `repo:<org>/<repo>:ref:refs/heads/<release-branch>`

The ECR publisher role must **not** have EKS, SSM, S3, or any unrelated AWS permissions.

### Manual dispatch branch restriction

`workflow_dispatch` on Release is restricted to the configured release branch (`release.branch`
in `config.yaml`). Dispatching from any other branch fails immediately before any AWS
credentials are requested. This prevents feature-branch images from being published to ECR.

### Mandatory controls (schema-enforced)

These controls are validated by `platform-config.schema.json` using `const: true`.
They cannot be disabled in `config.yaml` and cannot be bypassed by workflow logic:

| Control | Tool | Behaviour |
|---|---|---|
| Secret scanning | Gitleaks v8.30.1, pinned image digest | Full Git history scan; any finding blocks Release |
| Container scanning | Trivy | Scans the pushed digest; findings fail Release |
SBOM generation is intentionally optional. Disabling SBOM storage does not disable the
mandatory secret scan or immutable-image vulnerability scan.

**One canonical SBOM when enabled:** Only `anchore/sbom-action` generates the SBOM. Its
implicit artifact and release-asset uploads are disabled; the workflow uploads the one named
artifact with the configured retention period.

### Protect configuration files

Use branch protection and CODEOWNERS to prevent unauthorized changes to:
- `.github/workflows/`
- `.github/actions/`
- `ci-cd-platform/.platform/config.yaml`
- `ci-cd-platform/.platform/platform-config.schema.json`
- `ci-cd-platform/scripts/`

---

## Deploy

### Credential isolation

Deploy uses a **separate** IAM role from Release:

```
GitHub OIDC → EKS deployer IAM role → EKS cluster + target namespace only
```

The deployer role OIDC trust depends on the selected GitHub integration:

- Direct/free-default job: `repo:<org>/<repo>:ref:refs/heads/main`
- GitHub Environment job: `repo:<org>/<repo>:environment:<environment-name>`

Keep the two trust modes explicit. Enabling the Environment integration changes the OIDC
subject, so the IAM trust policy must be updated at the same time.

### Source trust verification

Before any deployment helper executes with AWS credentials, the pipeline verifies the
source commit belongs to the history of the configured release branch (derived from
`release.branch` in `config.yaml`). A manually supplied `source_sha` that is not an ancestor of the release
branch is rejected.

### Digest integrity

The deployed image is always pinned by its immutable ECR digest (`repository@sha256:...`).

- **Automatic deployment:** The digest comes from the `deployment-metadata` artifact
  uploaded by the triggering Release run. The metadata's `source_sha` is verified against
  the triggering workflow's `head_sha`.
- **Manual deployment with explicit digest:** The pipeline cross-verifies the supplied
  `image_digest` matches the digest of the image tagged with `source_sha` in ECR, preventing
  deployment of an image built from a different commit.
- **Manual deployment without digest:** The pipeline resolves the digest by looking up
  `imageTag=<source_sha>` in ECR.
- **Post-deployment verification:** Every matching Deployment's application container image
  is verified to equal `repository@sha256:digest` after `helm upgrade` completes. If
  `helm.container_name` is set, only that named container is checked; otherwise the first
  container is used as a fallback.

The Helm digest post-renderer (`helm_digest_post_renderer.py`) replaces the image field
in every `apps/v1/Deployment` before Helm applies it to the cluster. Values-file tags
cannot override the immutable digest.

### No static credentials

- No static AWS credentials or access keys in the repository.
- No static kubeconfig in the repository.
- `aws eks update-kubeconfig` generates an ephemeral kubeconfig scoped to the runner.
- Failure diagnostics intentionally omit container logs to reduce the risk of exposing
  application secrets or environment variables.

### GitHub Environment gates

GitHub Environments are disabled by default for private-repository portability. When the
client's plan supports the needed environment features, set
`integrations.github.deployment_environments.enabled: true`, configure the environments,
and add required reviewers where that protection rule is available. The protected job then
enters the matching Environment before AWS credentials are requested.

### Supply-chain hardening

GitHub Actions use readable exact release tags, and Dependabot is configured to open weekly
update pull requests. Exact tags are easier to maintain but are mutable references, so clients
with stricter supply-chain policies should replace them with verified full commit SHAs. The
Gitleaks container remains pinned to its immutable `linux/amd64` manifest digest.

---

## Secrets and Sensitive Values

| Category | Location | Notes |
|---|---|---|
| AWS OIDC role ARNs | `config.yaml` | Not sensitive — role ARNs are not secrets |
| Application secrets | GitHub Environment secrets or external vault | Never in `config.yaml`, `set_values`, or build args |
| Kubeconfig | Generated ephemerally at runtime | Never stored in repo |
| Container registry credentials | Generated ephemerally via ECR login action | Never stored in repo |
| Build arguments (`build_args`) | `config.yaml` | Non-sensitive only; secrets must not be passed as build args |
| Helm `set_values` | `config.yaml` | Non-sensitive only; secrets must not appear here |
