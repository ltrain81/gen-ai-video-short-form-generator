# AWS Shorts V2 — AI Video Editor · Product & Technical Spec

> Source of truth for V2 decisions. Derived from the Q&A in `v2-research-and-questions.md` (2026-11-17).
> V1 (Step Functions + Lambda) is preserved at git tag `v1-legacy`.

## 1. Design principle — "Easy by default, as deep as you want"

| Level | User action | Surface |
|---|---|---|
| L0 | Pick assets + one-line synopsis → finished video | Chat |
| L1 | Change length / aspect / tone / caption style / music; pick variant A/B/C | Chips & sliders next to preview |
| L2 | Select a clip/range and say "shorter", "add B-roll here" | Selection auto-attached to chat context |
| L3 | Trim / split / reorder / delete, caption text, replace clip | Simple timeline (always visible) |
| L4 | Multi-track, keyframes, transitions, speed curves, color, chroma key, audio mix | Pro editor (toggle) |
| L5 | Edit timeline JSON, custom recipes / skills / agents, OTIO/FCPXML/EDL import/export, API/MCP | Power users |

Consequences:
1. One timeline model expresses L0–L5 from day one; UI exposes levels progressively.
2. Chat = UI = API: every edit is a timeline **operation (patch)**. Anything done by chat is undoable in UI and vice-versa.
3. Every AI decision is inspectable ("why this clip": source timestamps, search scores) and overridable. User-edited properties are **pinned**; the agent must not overwrite pinned values.
4. Advanced features are hidden, never blocked. Remember the user's last level.
5. Defaults are quality: captions, reframing, loudness normalization, platform safe-zones applied at L0.

Scope is **not limited to short-form**: length and aspect ratio are recipe parameters. No maximum length.

## 2. Decisions

### Platform
| Item | Decision |
|---|---|
| Test sample | `SPOTV2.mp4` (owner-provided)  |
| Repo | Fork `ltrain81/gen-ai-video-short-form-generator`, branch `v2`, PR to aws-samples later |
| Region | `us-east-1` (parameterized) |
| Account | `760810925078` = dev; direct deploy allowed |
| IaC | AWS CDK (TypeScript) |
| Deployment modes | `-c mode=edit` (edit only) / `-c mode=full` (edit + generation). Same codebase |
| Product name | "AWS Shorts V2 — AI Video Editor" |
| UI | React + Vite + TypeScript + **Cloudscape Design System**, hosted on CloudFront + private S3; ko/en i18n. UI renders reviewed with the product owner step by step |
| Observability | AgentCore Observability (CloudWatch GenAI) + X-Ray traces |
| Guardrails / cost caps | Not required. **Exception:** generation from real-person faces is blocked; generated assets carry `ai_generated` + C2PA metadata |

### Agents
| Item | Decision |
|---|---|
| Orchestrator (primary) | **Bedrock Managed Agents (with OpenAI)** — Codex harness, model `openai.gpt-5.6-luna` (env-configurable) |
| Orchestrator (fallback) | **Strands Agents** on AgentCore Runtime, selected by config. Both share the same Gateway MCP tools |
| Subagents | Editing helpers = tools in the orchestrator; image/video generation = separate BMA sessions (Strands: separate Runtime via A2A) |
| Tools | AgentCore Gateway (MCP `2025-03-26`) → Lambda targets |
| Skills | Built-in recipes baked into the Runtime image; custom recipes stored in DynamoDB and exposed via tools; users can register custom skills/agents (extensibility) |
| User identity → tools | Tenant isolation via **per-tenant BMA session role**, plus a per-session user context. Phase 3 opens with a spike to verify what caller identity reaches Gateway tools (BMA guidance: no credentials in instructions). Strands path uses the Cognito JWT directly |
| Session | One BMA session per project; on expiry create a new one and inject timeline + summary context |
| Browser ↔ agent events | AppSync Events (Cognito auth); also broadcasts timeline patches |
| HITL | Turn-end question with structured payload (choices, clip cards, cost approval, range selection) rendered as UI components |
| Autonomy | Slider Auto ↔ Ask; default Auto, confirm before major changes |
| Memory | AgentCore Memory: per-user/group preferences (caption style, length, tone) |
| Long jobs | Analysis, generation, final render are async jobs behind tools; agent polls; continues if the browser closes |

### Media understanding & indexing
| Item | Decision |
|---|---|
| Embedding | Marengo Embed **3.0** (`twelvelabs.marengo-embed-3-0-v1:0`, 512-d), `StartAsyncInvoke`; dynamic (shot-based) segmentation; `visual`, `audio`, `transcription` stored **separately** |
| Query embedding | Marengo `InvokeModel` via `us.` inference profile (text / image / text_image) |
| Understanding | Pegasus **1.5** (`us.twelvelabs.pegasus-1-5-v1:0`): `general` summaries + `time_based_metadata` segments |
| Default analysis at ingest | Summary, chapters, timestamped events, highlight candidates. Others on demand |
| Description embeddings | Pegasus text outputs also embedded (`modality=description`) — hybrid search |
| STT | Amazon Transcribe (word timestamps), language auto-detect ko/en+ |
| Images | Marengo embedding + multimodal caption/tags by orchestrator model |
| PPT / PDF | Split into pages (image per page) at ingest; everything else done dynamically by the agent |
| Other assets | Audio, logos, fonts |
| Vector store | S3 Vectors, **index per tenant**, ACL post-filter via DynamoDB |
| Limits | None from the user side. Inputs over model limits (Pegasus 2 h / 2 GB, Marengo 4 h / 6 GB) are automatically chunked; offsets re-mapped |
| Proxy | MediaConvert 1080p (and low-res) H.264 proxies + thumbnails/sprites + waveform |
| Bulk backfill | SQS queues with parallel workers and concurrency control; progress dashboard |
| Dedupe | Content hash (SHA-256) → reuse analysis/embeddings across uploads; ACL stays per asset |
| Agent adaptivity | The agent decides which analyses are needed per asset/project and asks the user when unclear |
| People | Manual tags only; no face recognition |
| Sources | Local upload (multipart, resumable) + S3 import |
| Delete | Soft delete 30 days; projects show "missing asset"; renders kept |

### Identity & permissions
| Item | Decision |
|---|---|
| Hierarchy | Tenant (org) > Group > User; tenants fully isolated |
| AuthN | Cognito user pool; OIDC/SAML federation supported |
| Roles | **Owner / Editor / User** (User = can search and use in edits, cannot download original). No view-only role |
| Share units | Collections (inherit) + individual assets |
| Visibility | No permission → invisible |
| Requests | Request → owner approves; expiring shares; public links only for rendered outputs |
| Revocation | Renders kept; timeline clips from revoked assets locked |
| Admin | Quotas + audit log (who used which asset) in MVP; cost dashboard later |
| BYO keys | Per-user/tenant third-party keys (e.g. ElevenLabs) via AgentCore Identity API-key credential providers |

### Editing & output
| Item | Decision |
|---|---|
| Layout | Chat centered → on first message chat slides right, live preview left, simple timeline below preview, pro editor toggle |
| Preview | In-browser WebGL/WebCodecs compositor reading the timeline JSON; server render (FFmpeg on Fargate) must match (golden-frame tests) |
| Concurrency | Timeline locked while the agent edits; user edits are sent to the agent as diffs |
| Collaboration | No simultaneous multi-user editing (project sharing + comments later) |
| Versions | Snapshot every agent turn; restore from version list |
| Variants / batch | A/B/C variants; "N shorts from this long video" as background batch |
| Chat attachments | Project-scoped asset, with "save to archive" option |
| Output | MP4 H.264/AAC, any aspect (9:16 default, 1:1, 16:9, 4:5), burned-in captions + SRT/VTT, 30/60 fps; OTIO/FCPXML/EDL export |
| Metadata | Auto title/description/hashtags/thumbnail, editable |
| Brand kit | Per group presets (logo, fonts, colors, intro/outro, caption styles), editable |
| Distribution | Download only |

### Generation (mode=full)
| Capability | Default model (license) | Hosting |
|---|---|---|
| Text→image | Z-Image-Turbo (Apache-2.0) | SageMaker Async Inference (scale to zero) |
| Image edit / character consistency | Qwen-Image-Edit-2509 (Apache-2.0) + LoRA training (SageMaker training job) | SageMaker Async |
| Text/Image→video | Wan 2.2 A14B T2V / I2V (Apache-2.0) | SageMaker Async (g6e/p5; unlimited quota) |
| TTS | Fun-CosyVoice3-0.5B (Apache-2.0); optional ElevenLabs etc. via BYO key | SageMaker Async |
| Music | ACE-Step 1.5 (MIT) + user uploads + bundled CC0 library | SageMaker Async |

Models are configured in `config/models.yaml`; license-restricted models (e.g. LTX-2.5, MiniMax-H3, Qwen-Image-2.1) can be enabled by the deployer.
Generation flow: AI proposes archive candidates → user picks in UI; if none fit, user asks for generation → show estimated cost/time → generate → register as asset (`ai_generated=true`) → usable like any other asset.

## 3. Build order
1. CDK foundation: Cognito, S3, DynamoDB, S3 Vectors, upload → event ingest (Marengo + Pegasus + Transcribe), chunking, dedupe
2. Archive UI: upload, list, search (text/image), sharing & permissions
3. Orchestrator (BMA + Strands fallback), Gateway tools, chat UI, HITL — starting with the identity spike
4. Timeline model + browser preview + server render (FFmpeg on Fargate)
5. Editing UI (L3, then L4)
6. Recipes: highlight, recap, supercut, talking-head + B-roll, Top N, slideshow
7. Generation mode (image/video/TTS/music subagents + SageMaker)
8. Batch, metadata, brand kit, OTIO export, custom skills/agents
