# AWS & GitHub Infrastructure Setup Guide (Dev Environment)

This document provides a **comprehensive, step-by-step** guide using the **AWS Management Console** to provision every AWS service, IAM role, EKS cluster, and GitHub configuration required to run the CI → Release → Deploy pipeline for the `dev` environment.

---

## Table of Contents

- [Prerequisites](#prerequisites)
- [Architecture Overview](#architecture-overview)
- [Step 1: GitHub OIDC Identity Provider in AWS](#step-1-github-oidc-identity-provider-in-aws)
- [Step 2: Amazon ECR Repository](#step-2-amazon-ecr-repository)
- [Step 3: ECR Publisher IAM Role (Release Workflow)](#step-3-ecr-publisher-iam-role-release-workflow)
- [Step 4: Amazon EKS Cluster](#step-4-amazon-eks-cluster)
- [Step 5: EKS Deployer IAM Role (Deploy Workflow)](#step-5-eks-deployer-iam-role-deploy-workflow)
- [Step 6: Kubernetes RBAC for the Deployer Role](#step-6-kubernetes-rbac-for-the-deployer-role)
- [Step 7: Update config.yaml with Real ARNs](#step-7-update-configyaml-with-real-arns)
- [Step 8: GitHub Repository Settings](#step-8-github-repository-settings)
- [GitHub Secrets & Variables Summary](#github-secrets--variables-summary)
- [config.yaml Values Summary](#configyaml-values-summary)
- [Verification Checklist](#verification-checklist)
- [Troubleshooting](#troubleshooting)

---

## Prerequisites

| Prerequisite | Details |
|---|---|
| AWS Console access | Admin or power-user access to account `003417131700` |
| `kubectl` | Installed locally (v1.30+) — needed only for Step 6 (RBAC) |
| `helm` | Installed locally (v3.15+) — needed for chart validation |
| GitHub repository | `sridharan-a-mindstix/Accelerator-CICD-Testing` with Actions enabled |
| AWS region | `us-east-2` (Ohio) for all services |

---

## Architecture Overview

```
┌─────────────────────────────────────────────────────────────────────┐
│  GitHub Actions                                                     │
│                                                                     │
│  ┌────────────┐   ┌──────────────┐   ┌──────────────────────────┐  │
│  │  CI        │   │  Release     │   │  Deploy                  │  │
│  │  (PR only) │   │  (push main) │   │  (auto after Release)    │  │
│  │            │   │              │   │                          │  │
│  │  No AWS    │   │  OIDC →      │   │  OIDC →                  │  │
│  │  creds     │   │  ECR         │   │  EKS                     │  │
│  │  needed    │   │  Publisher   │   │  Deployer                │  │
│  └────────────┘   │  Role       │   │  Role                    │  │
│                   └──────┬───────┘   └──────────┬───────────────┘  │
│                          │                      │                   │
└──────────────────────────┼──────────────────────┼───────────────────┘
                           │                      │
                   GitHub OIDC                GitHub OIDC
                           │                      │
                           ▼                      ▼
┌──────────────────────────────────────────────────────────────────────┐
│  AWS Account: 003417131700  (us-east-2)                              │
│                                                                      │
│  ┌──────────────────┐   ┌──────────────────┐   ┌─────────────────┐  │
│  │  IAM OIDC        │   │  Amazon ECR      │   │  Amazon EKS     │  │
│  │  Provider        │   │  Repository      │   │  Cluster        │  │
│  │  (GitHub)        │   │  client-a/       │   │  client-a-      │  │
│  │                  │   │  sample-app      │   │  shared          │  │
│  └──────────────────┘   └──────────────────┘   └─────────────────┘  │
│                                                                      │
│  ┌──────────────────────────────────────────────────────────────┐    │
│  │  IAM Roles                                                   │    │
│  │                                                               │    │
│  │  1. github-actions-ecr-publisher     (Release → ECR push)    │    │
│  │  2. github-actions-eks-deployer-dev  (Deploy → EKS/Helm)     │    │
│  └──────────────────────────────────────────────────────────────┘    │
└──────────────────────────────────────────────────────────────────────┘
```

---

## Step 1: GitHub OIDC Identity Provider in AWS

GitHub Actions authenticates to AWS using OpenID Connect (OIDC) — no static access keys are stored anywhere. You need to register GitHub as a trusted identity provider in your AWS account.

### Console Steps

1. Open the **IAM Console**: https://console.aws.amazon.com/iam/
2. In the left sidebar, click **Identity providers**.
3. Click **Add provider**.
4. Configure:

   | Field | Value |
   |---|---|
   | Provider type | **OpenID Connect** |
   | Provider URL | `https://token.actions.githubusercontent.com` |
   | Audience | `sts.amazonaws.com` |

5. Click **Get thumbprint** — AWS will auto-resolve GitHub's certificate thumbprint.
6. Click **Add provider**.

### Verify

After creation, you should see an entry with:
- **Provider**: `token.actions.githubusercontent.com`
- **Type**: OpenID Connect
- **ARN**: `arn:aws:iam::003417131700:oidc-provider/token.actions.githubusercontent.com`

> **Note:** If the provider already exists (it's account-wide and shared across repos), skip this step.

---

## Step 2: Amazon ECR Repository

The Release workflow pushes container images to this ECR repository.

### Console Steps

1. Open the **ECR Console**: https://console.aws.amazon.com/ecr/
2. Make sure the region selector (top-right) is set to **US East (Ohio) us-east-2**.
3. Click **Create repository**.
4. Configure:

   | Field | Value |
   |---|---|
   | Visibility | **Private** |
   | Repository name | `client-a/sample-app` | accelerator/demo-app
   | Tag immutability | **Enabled** ✅ |
   | Scan on push | **Enabled** ✅ |
   | Encryption | **AES-256** (default) |

5. Click **Create repository**.

> **Important:** Enabling **Tag immutability** ensures that once an image is tagged with a commit SHA, it cannot be overwritten. This is critical for the pipeline's digest-integrity model.

### Set Lifecycle Policy (Optional, Recommended)

To automatically clean up old images and control costs:

1. Click on the repository name `client-a/sample-app`.
2. In the left sidebar, click **Lifecycle Policy**.
3. Click **Create rule**.
4. Configure:

   | Field | Value |
   |---|---|
   | Rule priority | `1` |
   | Rule description | `Keep last 50 images` |
   | Image status | **Any** |
   | Match criteria | **Image count more than** → `50` |
   | Action | **Expire** |

5. Click **Save**.

---

## Step 3: ECR Publisher IAM Role (Release Workflow)

This role is assumed by the **Release** workflow (via OIDC) to build and push container images to ECR.

### Console Steps

1. Open the **IAM Console**: https://console.aws.amazon.com/iam/
2. In the left sidebar, click **Roles**.
3. Click **Create role**.
4. Select **Web identity** as the trusted entity type.
5. Configure the web identity:

   | Field | Value |
   |---|---|
   | Identity provider | `token.actions.githubusercontent.com` |
   | Audience | `sts.amazonaws.com` |

6. Click **Next**.
7. On the "Add permissions" page, click **Next** (we will create an inline policy after the role is created).
8. Configure:

   | Field | Value |
   |---|---|
   | Role name | `github-actions-ecr-publisher` |
   | Description | `GitHub Actions: Release workflow ECR publisher for Accelerator-CICD-Testing` |

9. Click **Create role**.

### Restrict the Trust Policy to Your Repository

By default, the trust policy allows **any** GitHub repo to assume this role. You must restrict it:

1. Click on the newly created role `github-actions-ecr-publisher`.
2. Click the **Trust relationships** tab.
3. Click **Edit trust policy**.
4. Replace the entire policy with:

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Principal": {
        "Federated": "arn:aws:iam::003417131700:oidc-provider/token.actions.githubusercontent.com"
      },
      "Action": "sts:AssumeRoleWithWebIdentity",
      "Condition": {
        "StringEquals": {
          "token.actions.githubusercontent.com:aud": "sts.amazonaws.com"
        },
        "StringLike": {
          "token.actions.githubusercontent.com:sub": "repo:sridharan-a-mindstix/Accelerator-CICD-Testing:ref:refs/heads/main"
        }
      }
    }
  ]
}
```

5. Click **Update policy**.

> **Security:** The `sub` condition restricts this role to only be assumed from the `main` branch of your specific repository. Feature branches cannot publish images.

### Add the ECR Permissions Policy

1. While still on the role page, click the **Permissions** tab.
2. Click **Add permissions** → **Create inline policy**.
3. Click the **JSON** tab.
4. Paste the following policy:

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "ECRAuth",
      "Effect": "Allow",
      "Action": "ecr:GetAuthorizationToken",
      "Resource": "*"
    },
    {
      "Sid": "ECRPush",
      "Effect": "Allow",
      "Action": [
        "ecr:BatchCheckLayerAvailability",
        "ecr:InitiateLayerUpload",
        "ecr:UploadLayerPart",
        "ecr:CompleteLayerUpload",
        "ecr:PutImage",
        "ecr:DescribeImages",
        "ecr:BatchGetImage",
        "ecr:GetDownloadUrlForLayer"
      ],
      "Resource": "arn:aws:ecr:us-east-2:003417131700:repository/client-a/sample-app"
    }
  ]
}
```

5. Click **Next**.
6. Policy name: `ECRPublishPolicy`
7. Click **Create policy**.

### Note the Role ARN

On the role summary page, copy the **ARN**. It will look like:

```
arn:aws:iam::003417131700:role/github-actions-ecr-publisher
```
arn:aws:iam::003417131700:role/sre-accelerator-github-actions-ecr-publisher

> Save this — you will need it for `config.yaml` in [Step 7](#step-7-update-configyaml-with-real-arns).
arn:aws:iam::003417131700:role/github-actions-ecr-publisher
---

## Step 4: Amazon EKS Cluster

### Console Steps

1. Open the **EKS Console**: https://console.aws.amazon.com/eks/
2. Make sure the region selector is set to **US East (Ohio) us-east-2**.
3. Click **Add cluster** → **Create**.

#### Step 4a: Configure Cluster

| Field | Value |
|---|---|
| Name | `client-a-shared` | 'accelerator-demo-cluster'
| Kubernetes version | `1.30` (or latest stable) |
| Cluster service role | Select an existing EKS cluster role, or create one (see below) |

**If you need to create an EKS cluster role:** 
arn:aws:iam::003417131700:role/AcceleratorEKSAutoRole
arn:aws:iam::003417131700:role/AcceleratorEksNodeAutoRole

1. Open a new tab → **IAM Console** → **Roles** → **Create role**.
2. Trusted entity: **AWS service** → Use case: **EKS** → **EKS - Cluster**.
3. The `AmazonEKSClusterPolicy` will be auto-attached.
4. Role name: `eksClusterRole`. acceleratorEksClusterRole
5. Click **Create role**.
6. Go back to the EKS cluster creation and select `eksClusterRole`.

#### Step 4b: Specify Networking

| Field | Value |
|---|---|
| VPC | Select your default VPC or a dedicated VPC |
| Subnets | Select at least **2 subnets** in different availability zones |
| Security groups | Use the default or create a dedicated security group |
| Cluster endpoint access | **Public and private** (for testing) |

Click **Next**.

#### Step 4c: Configure Observability (Optional)

Leave defaults or enable CloudWatch logging as needed. Click **Next**.

#### Step 4d: Select Add-ons

Keep the defaults (CoreDNS, kube-proxy, Amazon VPC CNI). Click **Next**.

#### Step 4e: Review and Create

Review all settings and click **Create**.

> **Note:** Cluster creation takes approximately **10–15 minutes**. Wait for the status to become **Active**.

### Add a Node Group

After the cluster is **Active**:

1. Click on the cluster name `client-a-shared`.
2. Go to the **Compute** tab.
3. Click **Add node group**.
4. Configure:

   | Field | Value |
   |---|---|
   | Name | `dev-nodes` |
   | Node IAM role | Select an existing node role, or create one (see below) |

   **If you need to create a node role:**

   1. Open a new tab → **IAM Console** → **Roles** → **Create role**.
   2. Trusted entity: **AWS service** → Use case: **EC2**.
   3. Attach these policies:
      - `AmazonEKSWorkerNodePolicy`
      - `AmazonEKS_CNI_Policy`
      - `AmazonEC2ContainerRegistryReadOnly`
   4. Role name: `eksNodeRole`.
   5. Click **Create role**.
   6. Go back and select `eksNodeRole`.

5. Click **Next**.
6. Configure compute:

   | Field | Value |
   |---|---|
   | AMI type | **Amazon Linux 2023 (AL2023)** |
   | Capacity type | **On-Demand** |
   | Instance type | `t3.medium` |
   | Disk size | `20 GiB` |

7. Click **Next**.
8. Configure scaling:

   | Field | Value |
   |---|---|
   | Minimum size | `1` |
   | Maximum size | `3` |
   | Desired size | `2` |

9. Select your subnets (same as the cluster). Click **Next**.
10. Review and click **Create**.

> **Note:** Node group creation takes approximately **5–10 minutes**.

### Connect kubectl to the Cluster

After both the cluster and node group are **Active**, configure your local kubectl:

```bash
aws eks update-kubeconfig --name client-a-shared --region us-east-2
```

Verify:

```bash
kubectl cluster-info
kubectl get nodes
```

You should see your nodes in `Ready` state.

---

## Step 5: EKS Deployer IAM Role (Deploy Workflow)

This role is assumed by the **Deploy** workflow (via OIDC) to deploy to EKS via Helm.

### Console Steps

1. Open the **IAM Console**: https://console.aws.amazon.com/iam/
2. In the left sidebar, click **Roles**.
3. Click **Create role**.
4. Select **Web identity** as the trusted entity type.
5. Configure:

   | Field | Value |
   |---|---|
   | Identity provider | `token.actions.githubusercontent.com` |
   | Audience | `sts.amazonaws.com` |

6. Click **Next**.
7. Skip adding permissions (click **Next**).
8. Configure:

   | Field | Value |
   |---|---|
   | Role name | `github-actions-eks-deployer-dev` |
   | Description | `GitHub Actions: Deploy workflow EKS deployer for dev environment` |

9. Click **Create role**.

### Restrict the Trust Policy

1. Click on the role `github-actions-eks-deployer-dev`.
2. Click the **Trust relationships** tab.
3. Click **Edit trust policy**.
4. Replace the entire policy with:

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Principal": {
        "Federated": "arn:aws:iam::003417131700:oidc-provider/token.actions.githubusercontent.com"
      },
      "Action": "sts:AssumeRoleWithWebIdentity",
      "Condition": {
        "StringEquals": {
          "token.actions.githubusercontent.com:aud": "sts.amazonaws.com"
        },
        "StringLike": {
          "token.actions.githubusercontent.com:sub": "repo:sridharan-a-mindstix/Accelerator-CICD-Testing:ref:refs/heads/main"
        }
      }
    }
  ]
}
```

5. Click **Update policy**.

> **Note:** Since GitHub Environments are disabled (`integrations.github.deployment_environments.enabled: false`), the subject is scoped to `ref:refs/heads/main`. If you later enable GitHub Environments, change the subject to `environment:dev`.

### Add the EKS/ECR Permissions Policy

1. Click the **Permissions** tab.
2. Click **Add permissions** → **Create inline policy**.
3. Click the **JSON** tab.
4. Paste:

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "EKSDescribe",
      "Effect": "Allow",
      "Action": [
        "eks:DescribeCluster"
      ],
      "Resource": "arn:aws:eks:us-east-2:003417131700:cluster/client-a-shared"
    },
    {
      "Sid": "ECRRead",
      "Effect": "Allow",
      "Action": [
        "ecr:GetAuthorizationToken"
      ],
      "Resource": "*"
    },
    {
      "Sid": "ECRDescribeImages",
      "Effect": "Allow",
      "Action": [
        "ecr:DescribeImages",
        "ecr:BatchGetImage",
        "ecr:GetDownloadUrlForLayer"
      ],
      "Resource": "arn:aws:ecr:us-east-2:003417131700:repository/client-a/sample-app"
    }
  ]
}
```

5. Click **Next**.
6. Policy name: `EKSDeployPolicy`
7. Click **Create policy**.

### Note the Role ARN

Copy the **ARN** from the role summary:

```
arn:aws:iam::003417131700:role/github-actions-eks-deployer-dev
```

> Save this — you will need it for `config.yaml` in [Step 7](#step-7-update-configyaml-with-real-arns).
arn:aws:iam::003417131700:role/sre-accelerator-github-actions-eks-deployer-dev
---

## Step 6: Kubernetes RBAC for the Deployer Role

The EKS deployer IAM role must be mapped to a Kubernetes identity with permissions to manage resources in the `sample-app-dev` namespace.

### Option A: Using EKS Access Entries (Recommended — EKS Console)

1. Open the **EKS Console**: https://console.aws.amazon.com/eks/
2. Click on the cluster `client-a-shared`.
3. Go to the **Access** tab.
4. Click **Create access entry**.
5. Configure:

   | Field | Value |
   |---|---|
   | IAM principal ARN | `arn:aws:iam::003417131700:role/github-actions-eks-deployer-dev` |
   | Type | **Standard** |

6. Click **Next**.
7. Click **Add access policy**:

   | Field | Value |
   |---|---|
   | Policy name | `AmazonEKSEditPolicy` |
   | Access scope | **Namespace** |
   | Namespaces | `sample-app-dev` |

8. Click **Add policy**, then **Create**.

### Create the Namespace

This step requires `kubectl` (configured in Step 4):

```bash
kubectl create namespace sample-app-dev
```

### Option B: Fine-Grained RBAC (Alternative)

If you prefer more granular control over what the deployer can do, apply this RBAC config after creating the namespace:

```bash
cat <<'EOF' | kubectl apply -f -
---
apiVersion: rbac.authorization.k8s.io/v1
kind: Role
metadata:
  name: helm-deployer
  namespace: sample-app-dev
rules:
  - apiGroups: ["", "apps", "batch", "networking.k8s.io"]
    resources:
      - configmaps
      - secrets
      - services
      - deployments
      - replicasets
      - pods
      - jobs
      - ingresses
      - serviceaccounts
    verbs: ["get", "list", "watch", "create", "update", "patch", "delete"]
  - apiGroups: [""]
    resources:
      - events
    verbs: ["get", "list", "watch"]
---
apiVersion: rbac.authorization.k8s.io/v1
kind: RoleBinding
metadata:
  name: github-actions-deployer
  namespace: sample-app-dev
subjects:
  - kind: User
    name: github-actions-deployer-dev
    apiGroup: rbac.authorization.k8s.io
roleRef:
  kind: Role
  name: helm-deployer
  apiGroup: rbac.authorization.k8s.io
EOF
```

### Verify RBAC

```bash
kubectl auth can-i create deployments.apps \
  --namespace sample-app-dev \
  --as github-actions-deployer-dev
```

Should return `yes`.

---

## Step 7: Update config.yaml with Real ARNs

After creating the IAM roles, update `ci-cd-platform/.platform/config.yaml` with the actual role ARNs.

Replace the placeholder `123456789012` ARNs with the real values from Steps 3 and 5:

```yaml
# release.aws section (line ~89-92)
release:
  aws:
    account_id: "003417131700"
    region: us-east-2
    oidc_role_arn: arn:aws:iam::003417131700:role/github-actions-ecr-publisher

# deploy.environments.dev section (line ~155-157)
deploy:
  environments:
    dev:
      aws:
        region: us-east-2
        oidc_role_arn: arn:aws:iam::003417131700:role/github-actions-eks-deployer-dev
      eks:
        cluster_name: client-a-shared
```

### Validate the Configuration

```bash
python3 ci-cd-platform/scripts/platform_config.py validate
```

This must exit with code 0 and print the resolved JSON output.

---

## Step 8: GitHub Repository Settings

### 8.1 Enable GitHub Actions

1. Go to your repository: https://github.com/sridharan-a-mindstix/Accelerator-CICD-Testing
2. Click **Settings** (top menu bar).
3. In the left sidebar, click **Actions** → **General**.
4. Under "Actions permissions":
   - Select **Allow all actions and reusable workflows** (or restrict to specific actions if required).
5. Under "Workflow permissions":
   - Select **Read repository contents and packages permissions**.
   - ✅ Check **Allow GitHub Actions to create and approve pull requests** (needed for Dependabot).
6. Click **Save**.

### 8.2 Branch Protection Rules (Recommended)

1. Go to **Settings** → **Branches**.
2. Click **Add branch protection rule** (or **Add classic branch protection rule**).
3. Branch name pattern: `main`
4. Enable these settings:
   - ✅ **Require a pull request before merging**
   - ✅ **Require status checks to pass before merging**
     - Search and add these required checks:
       - `Validate configuration`
       - `Runtime quality checks`
       - `Source security checks`
   - ✅ **Require conversation resolution before merging** (optional)
   - ✅ **Do not allow bypassing the above settings** (optional)
5. Click **Create** or **Save changes**.

### 8.3 CODEOWNERS (Recommended)

Create a file `.github/CODEOWNERS` in your repository to protect pipeline files:

```
# CI/CD Platform files — require platform team review
/.github/workflows/    @sridharan-a-mindstix
/.github/actions/      @sridharan-a-mindstix
/ci-cd-platform/       @sridharan-a-mindstix
```

---

## GitHub Secrets & Variables Summary

> **This pipeline requires NO GitHub Secrets or repository variables for AWS authentication.** All AWS credentials are obtained dynamically via GitHub OIDC federation. The role ARNs are stored in `config.yaml` (they are not secrets).

| What | Where | Is it a Secret? | Notes |
|---|---|---|---|
| AWS OIDC Role ARNs | `config.yaml` | ❌ No | Role ARNs are not sensitive; they cannot be used without OIDC |
| AWS Access Keys | **Not used** | N/A | OIDC replaces static credentials entirely |
| ECR credentials | Generated at runtime | N/A | `aws-actions/amazon-ecr-login` generates ephemeral tokens |
| Kubeconfig | Generated at runtime | N/A | `aws eks update-kubeconfig` creates an ephemeral config |
| `GITHUB_TOKEN` | Auto-provided by Actions | N/A | Used for artifact upload/download, automatically scoped |

---

## config.yaml Values Summary

All values that must be set correctly in `ci-cd-platform/.platform/config.yaml`:

| Config Key | Value | Description |
|---|---|---|
| `application.name` | `demo-app` | Application identifier |
| `application.runtime` | `python` | Selects Python 3.12 runtime and quality commands |
| `release.branch` | `main` | Must match `on.push.branches` in `release.yaml` |
| `release.container.registry` | `ecr` | Container registry type |
| `release.container.repository` | `client-a/sample-app` | ECR repository path |
| `release.container.dockerfile` | `Dockerfile` | Path to Dockerfile |
| `release.aws.account_id` | `003417131700` | AWS account ID |
| `release.aws.region` | `us-east-2` | AWS region for ECR |
| `release.aws.oidc_role_arn` | `arn:aws:iam::003417131700:role/github-actions-ecr-publisher` | ECR publisher role ARN |
| `deploy.automatic_environment` | `dev` | Auto-deploy target after Release |
| `deploy.environments.dev.aws.region` | `us-east-2` | AWS region for EKS |
| `deploy.environments.dev.aws.oidc_role_arn` | `arn:aws:iam::003417131700:role/github-actions-eks-deployer-dev` | EKS deployer role ARN |
| `deploy.environments.dev.eks.cluster_name` | `client-a-shared` | EKS cluster name |
| `deploy.environments.dev.helm.namespace` | `sample-app-dev` | Kubernetes namespace |

---

## Verification Checklist

Run through this checklist after completing all steps:

| # | Check | How to Verify | Expected |
|---|---|---|---|
| 1 | OIDC provider exists | IAM Console → Identity providers | `token.actions.githubusercontent.com` listed |
| 2 | ECR repo exists | ECR Console → Repositories | `client-a/sample-app` exists in `us-east-2` |
| 3 | ECR publisher role exists | IAM Console → Roles → `github-actions-ecr-publisher` | Trust policy scoped to your repo's `main` branch |
| 4 | EKS cluster is running | EKS Console → Clusters → `client-a-shared` | Status: **Active** |
| 5 | Node group is ready | EKS Console → `client-a-shared` → Compute | Node group status: **Active**, nodes in Ready state |
| 6 | EKS deployer role exists | IAM Console → Roles → `github-actions-eks-deployer-dev` | Trust policy scoped to your repo's `main` branch |
| 7 | RBAC is configured | `kubectl auth can-i create deployments --namespace sample-app-dev` | `yes` |
| 8 | Platform config validates | `python3 ci-cd-platform/scripts/platform_config.py validate` | Exit code 0 |
| 9 | GitHub Actions enabled | GitHub → Settings → Actions → General | Actions are permitted |
| 10 | Helm chart lints | `helm lint helm-chart -f deployment-config/values/dev.yaml` | 0 charts failed |
| 11 | CI passes on PR | Open a test PR | CI workflow completes successfully |

---

## Troubleshooting

### "Not authorized to perform sts:AssumeRoleWithWebIdentity"

- **Check the OIDC provider** exists in IAM Console → Identity providers.
- **Check the trust policy** on the role — the `sub` condition must exactly match:
  ```
  repo:sridharan-a-mindstix/Accelerator-CICD-Testing:ref:refs/heads/main
  ```
- **Check the audience** is `sts.amazonaws.com` in both the OIDC provider and the trust policy.

### "No immutable ECR image was found for source SHA"

- The **Release** workflow must push the image to ECR successfully before Deploy can run.
- Verify the ECR repository name in `config.yaml` matches the actual ECR repository.
- Check the ECR publisher role has `ecr:PutImage` permission scoped to the correct repository ARN.

### "Cannot create resource deployments in namespace sample-app-dev"

- The EKS deployer IAM role is not mapped to a Kubernetes identity.
- Check the **EKS Console → Access tab** for the access entry.
- Ensure the `AmazonEKSEditPolicy` is scoped to namespace `sample-app-dev`.
- Verify the namespace `sample-app-dev` exists: `kubectl get namespace sample-app-dev`.

### "Helm chart directory not found: helm-chart"

- Ensure the `helm-chart/` directory exists at the repository root with `Chart.yaml`.
- Verify `deploy.environments.dev.helm.chart` in `config.yaml` is set to `helm-chart`.

### "Deployment source must belong to the trusted 'main' branch history"

- The Deploy workflow verifies the source commit is an ancestor of `main`.
- Ensure you're deploying from the `main` branch, not a feature branch.

### EKS cluster creation fails

- Ensure your VPC has at least **2 subnets** in different availability zones.
- Check that your AWS account has the `AWSServiceRoleForAmazonEKS` service-linked role (auto-created on first cluster).
- Verify you have sufficient EC2 instance limits for `t3.medium` in `us-east-2`.
