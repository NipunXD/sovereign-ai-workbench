# Future scope

What we would build next, why it matters at MRPL, and roughly what it costs.
Ordered by ratio of value to effort, not by ambition.

Items marked **seam exists** are already scaffolded — the table, the config or
the module is in place and nothing is wired to it. Those are the cheapest and
the most credible to promise.

---

## Tier 0 — finish what is already cut open (days)

| Item | Why | Seam |
|---|---|---|
| **CI pipeline** | `make check` passes locally; nothing runs it on push. A green badge is nearly free and it is the first thing a reviewer looks for | `.github/` exists, empty |
| **Persist run telemetry** | `routing_decisions`, `tool_invocations`, `sandbox_executions` are tables with zero rows — routing stats live in memory and are lost on restart. Persisting them turns the admin page into a real operations view | **seam exists** |
| **Eval results in the database** | Results are timestamped JSON files. In `eval_runs` they become a trend — "did last week's prompt change move grounding?" | **seam exists** |
| **Background worker** | Ingestion runs in the request. A 40-page scan blocks a connection for minutes; it should be a job with a progress stream | **seam exists** (`workers/` empty, layer contract already guards it) |
| **Generated API types** | `make types-gen` targets an empty package. Generating TS types from the OpenAPI schema removes a whole class of frontend/backend drift | **seam exists** |
| **End-to-end tests** | 364 tests cover the backend; nothing drives the browser. One Playwright test for ask → cite → open page would catch the bugs that hurt most in a demo | `tests/e2e/` exists, empty |
| **Three ADRs** | The reasoning lives in docstrings. Why no LangGraph, why ingest sits above rag, why a hand-rolled force layout — written down once | `docs/11-adr/` exists, empty |

---

## Tier 1 — answer quality (1–2 weeks each)

**Cross-encoder reranker.** Today retrieval is dense + sparse fused by RRF and
the top 8 go to the model. A small local reranker scoring query–passage pairs
would reorder those 8 properly. This is the single biggest accuracy lever left,
and our harness already measures nDCG@10, so the gain would be a number rather
than a feeling.

**Query rewriting.** "And the design pressure of that same vessel?" works today
because we pass conversation history. Rewriting the question into a standalone
query before retrieval would help more — abbreviations, implied equipment tags,
multi-part questions.

**Feedback loop.** A thumbs up/down on an answer, stored with its run id. Two
uses: bad answers become eval cases automatically, and a pattern of downvotes on
one document flags an ingestion problem. Nothing like this exists yet.

**Confidence calibration.** We show a grounding ratio. We do not yet know
whether 80% grounded actually means "right 80% of the time". Calibrating that
against a labelled set would let the UI say something honest about certainty.

**Adversarial evaluation.** Prompt injection through document content is the
obvious attack: a PDF containing "ignore previous instructions". We wrap
evidence in `<source>` tags and tell the model it is data, but we have not tried
to break it systematically. A red-team suite belongs in the eval harness.

---

## Tier 2 — depth that would make it indispensable at MRPL

**P&ID reading (the measured one).** We already benchmarked this: a vision model
reading a whole sheet finds 0% of tags; six 1024px tiles with 128px overlap
finds **67% at 100% precision**. The config is written, the ground truth file
exists. What is missing is wiring it into a user-facing answer — *"which valves
isolate V-1201?"* read off the drawing, with the tag cross-validated against the
equipment registry before it is trusted.

**Trend and remaining-life monitoring.** The corpus already contains thickness
readings across years. Turning that from a question you ask into a thing the
system watches — "CML-06 crosses t-min in five months at the current rate" — is
the difference between a search tool and an inspection assistant.

**Historian and CMMS integration.** The largest single upgrade. Live tags from a
process historian (OSIsoft PI or equivalent) and work orders from SAP PM, so a
question can join a document to a live reading: *"has P-101A vibration exceeded
the SOP-2210 alarm limit since the last overhaul?"* Read-only, inside the plant
network, and it fits the existing tool interface.

**Report templates.** Inspection reports follow a house format. A template
library — pick "UT survey report", the agent fills it from the corpus — turns a
five-minute prompt into one click, and makes output consistent across engineers.

**Multi-document comparison.** "What changed between the 2023 and 2029 surveys?"
is a different operation from retrieval. Aligning two structured documents and
reporting the deltas would need a dedicated tool.

**Conversational revision of artifacts.** Today a generated document is final.
"Add a column for corrosion rate and regenerate" should edit it, keeping the
provenance chain and requiring re-approval.

---

## Tier 3 — what a real deployment needs

| Item | Why |
|---|---|
| **SSO / Active Directory** | MRPL will not maintain a separate user list. LDAP or SAML, mapping AD groups to our roles and clearance |
| **Signed artifacts** | A provenance block inside the file proves origin only if it cannot be edited. Digital signature over the document digest |
| **Audit export to SIEM** | Security teams want events in their own system. The log is already append-only JSONL — it needs a syslog/webhook sink |
| **Retention and deletion** | A confidential document withdrawn from circulation must disappear from the index, the blobs and future answers — and the deletion must itself be audited |
| **Multi-plant tenancy** | MRPL is one refinery. Project or site scoping on every row, and retrieval filtered by it as clearance is today |
| **Horizontal scale** | API is already stateless; Qdrant shards; the model layer moves to a GPU server by editing one YAML file. The work is load testing, not architecture |
| **Observability** | OpenTelemetry traces and Prometheus metrics. Right now the answer to "why was that run slow?" is reading a log |
| **Backup and restore** | Postgres, Qdrant and the blob store must be recoverable together and consistently |

---

## Tier 4 — the ambitious ones

**Domain-adapted embeddings.** A general embedding model does not know that
CML-04 and "bottom shell location 4" are the same thing. A LoRA fine-tune on
plant vocabulary — equipment tags, abbreviations, unit conventions — would lift
retrieval on exactly the queries engineers actually type.

**Proactive agent.** Instead of waiting to be asked, watch newly ingested
documents and raise findings: a reading that breaks a trend, an SOP revision
that contradicts a work permit, an inspection that is overdue. Everything needed
is there — the trigger, the approval gate and the audit log.

**Offline mobile companion.** An inspector in the field with a tablet, no
network, querying a subset of the corpus synced before they walked out.

**Cross-plant knowledge, without sharing data.** If MRPL runs several units,
federated evaluation — share what a model learned, never the documents.

---

## What to say if asked in the viva (about 45 seconds)

> "Three directions. First, accuracy — a cross-encoder reranker after the hybrid
> fusion is the biggest lever left, and our harness already measures nDCG so the
> gain would be a number. Second, depth for the plant — we benchmarked P&ID tag
> extraction at 67% recall with 100% precision using tiled vision, and the next
> step is wiring that into an answer; beyond that, joining documents to live
> historian tags so you can ask whether a reading has breached an SOP limit.
> Third, deployment — SSO against Active Directory, signed artifacts, audit
> export to a SIEM. And we would finish the seams we deliberately left: a
> background worker for ingestion, CI, and persisting the routing telemetry
> that currently only lives in memory."

**If they ask what you would do *first*:** the reranker, because it is measurable
and improves every answer. **If they ask what is most valuable to MRPL:** the
historian integration, because it turns a document search into an engineering
assistant.
