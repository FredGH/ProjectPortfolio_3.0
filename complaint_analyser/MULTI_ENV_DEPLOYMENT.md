# Multi-Environment Deployment Guide

---

## Writing once, deploying anywhere

The application layer (Docker Compose + environment variables) is inherently
cloud-agnostic. Only three seams need to be abstracted to make the stack
deployable to AWS, GCP, or Azure with no application code changes.

### Seam 1 — Infrastructure provisioning: Terraform with provider modules

One set of shared variables, three provider implementations:

```
complaint_analyser/
└── infrastructure/
    ├── variables.tf        # shared: vm_size, region, ssh_public_key
    ├── outputs.tf          # shared: deploy_host, deploy_user
    ├── bootstrap.sh        # idempotent Docker + repo setup (runs on any Ubuntu VM)
    ├── aws/
    │   └── main.tf         # Graviton EC2 + SG + Elastic IP
    ├── gcp/
    │   └── main.tf         # T2A Compute Engine + firewall + static IP
    └── azure/
        └── main.tf         # Dpsv6 VM + NSG + public IP
```

`cd infrastructure/aws && terraform apply` provisions an AWS instance.
Swap to `gcp/` or `azure/` for the other clouds. `outputs.tf` always emits
the same variables (`deploy_host`, `deploy_user`) regardless of provider.

### Seam 2 — Deploy job: cloud-agnostic secrets

The deploy CI job needs only `DEPLOY_HOST` and `DEPLOY_SSH_KEY`. Use GitHub
Actions environments keyed by `CLOUD_TARGET`:

```yaml
deploy:
  environment: ${{ vars.CLOUD_TARGET }}   # "aws" | "gcp" | "azure"
  steps:
    - uses: appleboy/ssh-action@v1
      with:
        host: ${{ secrets.DEPLOY_HOST }}   # populated from terraform output
        username: ${{ secrets.DEPLOY_USER }}
        key: ${{ secrets.DEPLOY_SSH_KEY }}
        script: |
          cd /opt/complaint_analyser
          git pull origin main
          docker compose pull && docker compose up -d --wait
```

Terraform outputs the instance IP → update `DEPLOY_HOST` in the target
environment's secrets. The workflow YAML never changes.

### Seam 3 — LLM provider: one env var + a factory function

A single factory in `agentic_triage/core/llm.py`:

```python
def get_llm() -> BaseChatModel:
    provider = os.environ.get("LLM_PROVIDER", "ollama")
    model = os.environ.get("LLM_MODEL", "llama3.1:8b")
    base_url = os.environ.get("LLM_BASE_URL", "http://ollama:11434")

    if provider == "ollama":
        return ChatOllama(model=model, base_url=base_url)
    if provider == "bedrock":
        return ChatBedrockConverse(model_id=model)
    if provider == "vertexai":
        return ChatVertexAI(model_name=model)
    if provider == "azureopenai":
        return AzureChatOpenAI(azure_deployment=model)
    raise ValueError(f"Unknown LLM_PROVIDER: {provider}")
```

Every graph node that needs an LLM calls `get_llm()`. Swapping clouds is
then a single `.env` change:

```bash
# Oracle / AWS (self-hosted Ollama)
LLM_PROVIDER=ollama
LLM_MODEL=llama3.1:8b
LLM_BASE_URL=http://ollama:11434

# GCP
LLM_PROVIDER=vertexai
LLM_MODEL=meta/llama-3.1-8b-instruct-maas

# Azure
LLM_PROVIDER=azureopenai
LLM_MODEL=llama-3-1-8b
```

### The full swap surface: `.env`

```bash
# Infrastructure endpoints — filled from Terraform output or managed console
POSTGRES_HOST=localhost          # → RDS / Cloud SQL / Azure DB FQDN
REDIS_URL=redis://redis:6379     # → ElastiCache / Memorystore / Azure Cache URL
QDRANT_HOST=qdrant               # → Qdrant Cloud URL if using managed

# LLM — the only application branch point
LLM_PROVIDER=ollama
LLM_MODEL=llama3.1:8b
LLM_BASE_URL=http://ollama:11434

# Secrets
POSTGRES_PASSWORD=...
GHCR_TOKEN=...
```

### What actually changes per cloud

| Layer | Changes |
|---|---|
| Application code | Nothing |
| CI jobs (lint / test / build / evaluate) | Nothing |
| Docker Compose | Nothing |
| GHCR images | Nothing for ARM64 targets; add `linux/amd64` to build matrix for x86 VMs |
| Terraform | `cd infrastructure/<provider> && terraform apply` |
| GitHub Actions secrets | `DEPLOY_HOST`, `DEPLOY_SSH_KEY` updated from Terraform output |
| `.env` on server | `LLM_PROVIDER` + any managed service endpoints |

> **Decision point before Phase 2:** add `linux/amd64` to the CI build matrix
> now to cover GCP/Azure x86 VMs, or keep ARM64-only. Adding it doubles build
> time but means all three clouds are covered from day one.

---

## Cloud-by-cloud options

### AWS

**Best fit: single EC2 Graviton instance (lift-and-shift)**

CI already builds `linux/arm64` images. Graviton is ARM64 — zero image changes.

| What | How |
|---|---|
| VM | `t4g.2xlarge` (8 vCPU, 32 GB) ~$200/mo, or **spot** ~$50–70/mo |
| Docker Compose | Deploy exactly as-is — identical to the Oracle setup |
| Postgres / Redis | Optionally replace with RDS + ElastiCache (~$30/mo combined) for managed backups |
| Ollama | Stays on the VM — Graviton handles Q4 inference well |
| CI/CD deploy | Replace Oracle SSH step with `DEPLOY_HOST` secret; no other changes |
| Tunnel | ALB + ACM cert, or keep Cloudflare Tunnel |

**Verdict:** closest to Oracle, zero image rebuilds, spot instances keep costs
low. Best choice for lift-and-shift.

---

### GCP

**Best fit: Compute Engine T2A (ARM) or replace Ollama with Vertex AI**

| What | How |
|---|---|
| VM | `t2a-standard-8` (8 vCPU, 32 GB) ~$220/mo — ARM64, images work as-is |
| Alternative | `n2-standard-8` (x86) — larger spot market, but requires adding `linux/amd64` to CI build matrix |
| Ollama swap | Replace with **Vertex AI** (Llama 3.1 8B via Model Garden) — removes 8 GB RAM requirement, cuts VM to 16 GB |
| Postgres / Redis | Cloud SQL + Memorystore — well-integrated, ~$40/mo combined |
| Tunnel | Cloud Load Balancer + managed SSL, or keep Cloudflare |

If dropping Ollama for Vertex AI, `langchain-ollama` → `langchain-google-vertexai`
is the only code change (isolated to `get_llm()` above).

**Verdict:** best if you want a managed LLM and are already in the Google
ecosystem.

---

### Azure

**Best fit: single VM with managed Postgres + Redis, keep Ollama**

| What | How |
|---|---|
| VM | `Standard_D8ps_v6` (ARM, 8 vCPU, 32 GB) ~$250/mo — ARM64 images work as-is |
| Alternative | `Standard_D8s_v5` (x86) — larger spot market, requires `linux/amd64` in CI build matrix |
| Ollama swap | Replace with **Azure OpenAI** (Llama 3.1 available via MAAP) |
| Postgres / Redis | Azure Database for PostgreSQL + Azure Cache for Redis |
| Tunnel | Azure Application Gateway, or keep Cloudflare |

Azure ARM VMs (`Dpsv6` series) are newer and have a thinner spot market than
Graviton — factor this in for cost-sensitive deployments.

**Verdict:** good choice if your organisation is Azure-first; slightly more
friction than AWS for this stack.

---

## Recommendation

| Goal | Choice |
|---|---|
| Cheapest self-hosted, minimum changes | **AWS Graviton spot** |
| Eliminate Ollama operational burden | **GCP + Vertex AI** |
| Organisation already on Azure | **Azure VM + Azure OpenAI** |
