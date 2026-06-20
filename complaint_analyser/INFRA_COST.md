# Infrastructure Cost Analysis

Cloud cost comparison for running the complaint_analyser stack on Oracle Cloud, AWS, and GCP.

---

## Infrastructure Requirements

Derived from the production deployment spec in [README.md](README.md):

| Resource | Requirement | Notes |
|---|---|---|
| **Compute** | 4 ARM cores, 24 GB RAM | Steady-state ~10 GB; BERTopic burst ~12–13 GB |
| **Storage** | 200 GB block storage | Qdrant + Postgres volumes |
| **LLM** | Local (Ollama) | No external API cost — `llama3.1:8b` Q4 runs on-VM |
| **Services** | Qdrant, Postgres, Redis, n8n, FastAPI, arq workers, spaCy | All self-hosted via Docker Compose |

---

## Oracle Cloud

| Tier | Compute | Storage | Monthly Total |
|---|---|---|---|
| **Always Free** | A1.Flex 4 OCPUs / 24 GB RAM | 200 GB (free) | **$0** |
| **Pay-as-you-go** | $0.01/OCPU-hr + $0.0015/GB-hr | $0.0255/GB-mo | **~$60/mo** |

Compute breakdown (pay-as-you-go):
- 4 OCPUs × $0.01 × 720 hrs = $28.80
- 24 GB × $0.0015 × 720 hrs = $25.92
- 200 GB block storage = $5.10
- **Total: ~$60/mo**

Oracle is the cheapest paid option and the only one with a $0 path. The project was designed around the Always Free A1.Flex allocation. The main obstacle is capacity in busy regions (UK South London); see [README.md](README.md) production deployment section.

---

## AWS

Closest ARM match: **t4g.xlarge** (4 vCPU / 16 GB — tight on RAM) or **t4g.2xlarge** (8 vCPU / 32 GB — comfortable).

| Instance | On-demand | 1-yr Reserved | EBS 200 GB gp3 | Monthly Total |
|---|---|---|---|---|
| t4g.xlarge (4 vCPU / 16 GB) | ~$97/mo | ~$62/mo | $16/mo | **~$113 / ~$78** |
| t4g.2xlarge (8 vCPU / 32 GB) | ~$193/mo | ~$123/mo | $16/mo | **~$209 / ~$139** |

> The t4g.xlarge may hit memory pressure when BERTopic runs (`~2 GB` burst on top of the ~10 GB steady-state). The t4g.2xlarge is the safe production choice.

Region: `eu-west-2` (London) pricing. EBS gp3 at $0.08/GB-month.

---

## GCP

Closest ARM match: **t2a-standard-4** (4 vCPU / 16 GB — tight). For the full 24 GB footprint, a custom e2 machine is more economical than a standard tier.

| Instance | On-demand | 1-yr CUD (~30% off) | Persistent Disk 200 GB | Monthly Total |
|---|---|---|---|---|
| t2a-standard-4 (4 vCPU / 16 GB) | ~$121/mo | ~$85/mo | $8/mo (HDD) | **~$129 / ~$93** |
| e2-custom (4 vCPU / 24 GB) | ~$110/mo | ~$77/mo | $8/mo | **~$118 / ~$85** |
| e2-standard-8 (8 vCPU / 32 GB) | ~$193/mo | ~$135/mo | $8/mo | **~$201 / ~$143** |

Custom e2 pricing breakdown:
- 4 vCPUs × $0.02126/hr × 720 hrs = $61.23
- 24 GB × $0.002849/hr × 720 hrs = $49.26
- pd-standard 200 GB = $8.00
- **Total: ~$118/mo on-demand**

Region: `europe-west2` (London). Persistent disk at $0.04/GB-month (HDD).

---

## Summary

| Cloud | Free Tier | Minimum Viable (paid) | Comfortable (paid) |
|---|---|---|---|
| **Oracle** | **$0/mo** (capacity limited in London) | ~$60/mo | ~$60/mo |
| **GCP** | None | ~$85–93/mo (1-yr CUD) | ~$143/mo (1-yr CUD) |
| **AWS** | None | ~$78/mo (1-yr RI) | ~$139/mo (1-yr RI) |
| **AWS** | None | ~$113/mo (on-demand) | ~$209/mo (on-demand) |
| **GCP** | None | ~$118/mo (on-demand) | ~$201/mo (on-demand) |

### Key Observations

- **Oracle Always Free is the target deployment** — the stack was sized to fit within the 4 OCPU / 24 GB / 200 GB free allocation.
- **If Oracle capacity is unavailable**, GCP with a 1-year Committed Use Discount (~$85/mo) is the next cheapest option, followed by AWS with a 1-year Reserved Instance (~$78/mo for the RAM-constrained t4g.xlarge).
- **On-demand pricing** is broadly similar between AWS and GCP for equivalent specs (~$113–$130/mo for the minimum viable instance).
- **The LLM running locally (Ollama)** eliminates what would otherwise be the dominant cost driver. Replacing Ollama with the Anthropic Claude API at production volume would add ~$50–200+/mo depending on complaint throughput.

---

*Prices are approximate as of mid-2025 and subject to change. Always verify current rates on each provider's pricing calculator before provisioning.*
