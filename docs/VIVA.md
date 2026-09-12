# Viva brief — Sovereign AI Workbench

SIH 2026 · PS 26117 · Team AIDUO · Client MRPL

Short spoken answers. Everything here is true of the running system.

---

## 1 · The 30-second answer

> "An air-gapped AI workbench for a refinery. An engineer asks a question in
> plain English; an agent plans the work, searches the plant's own documents,
> runs calculations in a sandbox, and returns an answer where every claim is
> linked to the exact page it came from. Nothing leaves the plant — every model
> runs locally on open weights. Documents it generates wait for a second person
> to approve, and every action is written to a hash-chained audit log."

**If they want one sentence:** *"Local, citable, governed AI for confidential
industrial work."*

---

## 2 · The problem

Refineries hold SOPs, inspection reports, P&IDs, maintenance logs and
correspondence that are confidential and often scanned. Engineers need answers
from them, but:

- Cloud AI is unusable — the data cannot leave the network
- Keyword search returns documents, not answers
- A wrong number in a refinery is a safety issue, so an AI that guesses is worse
  than no AI

**So the product must be:** local, grounded (every claim cited), honest (says
when it doesn't know), and governed (RBAC + approval + audit).

---

## 3 · Tech stack — what and *why*

| Layer | Tech | Why this one |
|---|---|---|
| Frontend | Next.js 15, React 19, TypeScript, Tailwind | App Router for streaming UI; typed end-to-end |
| State | Zustand + TanStack Query | Zustand for the live run, Query for server cache |
| Backend | Python, FastAPI | Async-native, which matters when one request streams for 2 minutes |
| ORM | SQLModel + SQLAlchemy async + Alembic | Pydantic models and DB models are the same class |
| Database | PostgreSQL | 26 tables — users, runs, messages, approvals, audit |
| Vector DB | Qdrant | Payload filtering, so clearance is applied *inside* the search |
| Models | Qwen3 family, open weights | No licence lock-in; runs on the plant's own GPU |
| Serving | LM Studio (MLX) + Ollama (GGUF) | Two different runtimes prove the provider abstraction |
| OCR | RapidOCR | Runs locally, no cloud OCR API |
| Vision | Qwen3-VL 8B | Re-reads pages OCR couldn't |
| Documents | PyMuPDF, python-docx, openpyxl, python-pptx | Extraction and artifact generation |
| Sandbox | Docker, `network_mode=none` | Kernel-level isolation for generated code |
| Transport | Server-Sent Events | One-way stream; simpler and more robust than WebSocket |
| Quality | pytest (364 tests), ruff, mypy, import-linter, ESLint | Architecture is machine-enforced |

**Why not LangChain/LangGraph?** We wrote the orchestrator ourselves — one less
dependency in an offline bundle, and we needed explicit control over two things
a framework hides: cancellation (a closed browser must not orphan a run) and a
human-approval pause that survives the requester logging out.

---

## 4 · Architecture — nine layers

```
UI (Next.js) → API (FastAPI) → Agent orchestrator → Tools
                                      ↓
        Model router → LM Studio / Ollama (local open weights)
                                      ↓
   Qdrant (vectors) · PostgreSQL (state + audit) · Filesystem (blobs)
                                      ↓
              Security: RBAC, clearance, approval, audit
```

The dependency direction is **enforced by import-linter** in CI:

```
api > agent > tools > ingest > (rag | artifacts | sandbox | router)
    > providers > security > db > core
```

Arrows only point down. A violation fails the build.

---

## 5 · What happens when you ask a question (asked almost every time)

1. **Auth** — JWT access token; the request carries the user's roles and clearance
2. **Route** — a classifier picks the logical model (`reasoning.primary`), with a
   fast path that skips the classifier for obvious cases
3. **Plan** — the model writes a step list, each step with an intent
4. **Retrieve** — hybrid search: dense (embeddings) + sparse (BM25), fused by
   Reciprocal Rank Fusion, **filtered by clearance inside the query**
5. **Act** — tools if needed: `calc.engineering`, `code.run_python` (sandbox),
   `artifact.docx/xlsx/pptx`
6. **Approve** — document and code steps pause for a second person
7. **Synthesize** — the model answers using only the retrieved passages, marking
   each claim with the source id
8. **Validate** — markers are resolved to numbered citations; any pointing at
   nothing are deleted and reported; grounding ratio computed
9. **Deliver** — answer streams over SSE with citations, trace, and any files
10. **Record** — run, tool calls, approvals and refusals go to the audit log

---

## 6 · Features, one line each

| # | Feature | How |
|---|---|---|
| 1 | Chat workspace | SSE stream with a live trace of what the agent is doing |
| 2 | Saved sessions | Every run's event stream stored; replayed through the same reducer. Owner-only — others get 404, never 403 |
| 3 | Multi-format ingest | PDF (native + scanned), images, DOCX/PPTX/XLSX, CSV, text, email |
| 4 | OCR + quality gate | RapidOCR at 400 DPI with CLAHE and deskew; confidence measured per block |
| 5 | Vision escalation | Below 72% confidence or under 30 words, the page is re-read by a local VLM |
| 6 | Chunking | 256-token children indexed, 900-token parents returned; table rows never split |
| 7 | Hybrid retrieval | Dense + sparse, RRF-fused, top 8; low-confidence OCR chunks penalised not hidden |
| 8 | Citations | Every claim carries `[n]`; click opens the passage, then the page with the region outlined |
| 9 | Evidence map | Force-directed graph of what was read; hovering a citation lights the same passage |
| 10 | Refusal | Structural detector — says what the corpus does not contain rather than guessing |
| 11 | Model routing | 9 models in a YAML manifest with logical names; swap models without touching code |
| 12 | Memory budget | 14 GB residency manager; prefers a resident adequate model over a cold better one |
| 13 | Sandboxed code | Docker container, no network interface, read-only root, runs as `nobody` |
| 14 | Artifacts | DOCX/XLSX/PPTX with inline citations and a provenance block written inside the file |
| 15 | Two-person approval | The requester cannot approve their own request; execution is exactly-once |
| 16 | RBAC + clearance | 4 classification levels; the filter lives inside the retrieval query |
| 17 | Audit | Hash-chained log; each record commits to the digest of the one before it |
| 18 | Sovereignty check | The running system publishes every host it can reach |

---

## 7 · The five hard problems (what makes it more than a demo)

**1. Clearance inside the query, not the UI.**
The access filter is a Qdrant payload filter. A restricted passage is never a
candidate for a user without clearance — you cannot prompt-inject past it.
Same query, two users: `classification<=restricted; departments=inspection,process`
vs `classification<=internal; departments=operations`.

**2. Hallucinated citations are deleted, not displayed.**
The model emits markers; the resolver maps them to retrieved evidence. A marker
pointing at nothing is removed and reported to the validator. Measured
hallucinated-citation rate: **0%**.

**3. Refusal detection is structural, not a phrase list.**
Fixed phrases missed real refusals and our eval reported a 20% refusal rate when
the truth was 100%. We replaced it with a grammar — a negated availability verb
attached to a document subject, in the opening sentences, with a guard so a
caveat after a cited answer is not mistaken for a refusal.

**4. Exactly-once deferred approval.**
When an approver signs off minutes later, execution resumes as the *requester*
(so their permissions apply, not the approver's), claimed with a conditional
UPDATE so two approvals cannot run the tool twice.

**5. Runs survive a closed browser.**
Finalisation runs in a detached task under anyio cancellation semantics, so a
dropped connection records a terminal state instead of leaving a run "running"
forever. A reconnect replays the stored trace.

---

## 8 · Numbers to quote

| Suite | Result |
|---|---|
| Grounding | **0%** hallucinated citations · 94% mean grounded ratio · 80% refusal rate on unanswerable |
| Retrieval | recall@10 **1.00** · nDCG@10 **0.81** · MRR 0.75 · p95 latency **32 ms** |
| OCR | character error **6.4%** (1.5% ignoring spaces) at 400 DPI — down from **16.3%** |
| Routing | lane accuracy **1.00** · fast-path p95 **0.06 ms** |

**Scale:** ~32,000 lines · 364 tests · 26 tables · 13 enforced modules · 9 models

> "The OCR number moved from 16.3% to 6.4% *because* we measured it. We built
> the eval harness before we tuned anything."

---

## 9 · Likely viva questions

**Q. How is this different from ChatGPT?**
Two things it structurally cannot do: run inside your network on your GPU with
an egress list you can audit, and stop itself to wait for a second person before
producing a document.

**Q. What is RAG?**
Retrieval-Augmented Generation. Instead of relying on what the model memorised,
we search our own documents, put the relevant passages into the prompt, and
require the answer to come from them. That's what makes citations possible.

**Q. What's the difference between an agent and a chatbot?**
A chatbot answers in one step. An agent plans multiple steps, chooses tools,
checks its own output, and can pause for a human. Ours does plan → retrieve →
act → validate → deliver.

**Q. Why open-weight models?**
Licence freedom, no per-token cost, and — the real reason — they run on our own
hardware, which is the entire premise of an air-gapped product.

**Q. How do you stop hallucination?**
Three layers. Retrieval limits what it can talk about; the validator deletes
citations that point at nothing and computes a grounding ratio; and a refusal
detector means "not in the corpus" is a valid answer. Measured at 0%
hallucinated citations.

**Q. What is hybrid search?**
Dense search (embeddings) catches meaning; sparse search (BM25) catches exact
tokens. `V-1201` is an exact token embeddings blur together. We run both and
fuse with Reciprocal Rank Fusion.

**Q. What is RRF?**
Reciprocal Rank Fusion — each result scores `1/(k + rank)` in each list and the
scores are summed. It merges two rankings without needing their scores to be on
the same scale.

**Q. How do you chunk?**
256-token children are what we index; the 900-token parent is what we give the
model. Headings are respected and table rows are never split — half a table row
is a wrong number.

**Q. Why Qdrant and not FAISS/Pinecone?**
Pinecone is a cloud service, which is disqualifying. FAISS is a library with no
metadata filtering. Qdrant self-hosts and filters on payload, which is how
clearance is enforced inside the search.

**Q. How does authentication work?**
Short-lived JWT access tokens (HS256) plus a long-lived refresh token in an
httpOnly, SameSite=Strict cookie, stored only as a hash.

**Q. How is RBAC implemented?**
Roles map to permissions; users also carry a clearance level and departments.
Permissions gate endpoints; clearance is a filter inside the retrieval query.

**Q. Explain the audit hash chain.**
Each record stores the SHA-256 digest of the previous record. Altering any
historical row invalidates every hash after it, and the verify endpoint reports
the exact sequence number where the chain breaks.

**Q. How does the approval flow work?**
A tool marked as needing approval raises a request and the run pauses. The
requester cannot decide their own request. On approval the tool executes as the
requester, claimed by a conditional UPDATE so it can only run once.

**Q. What if the approver takes an hour?**
The run is stored, not held in memory. Approval later triggers deferred
execution and the artifact appears in the conversation when the requester
returns.

**Q. How do you handle scanned documents?**
Render at 400 DPI, deskew, CLAHE contrast, then RapidOCR. Each block gets a
confidence. Below 72% mean confidence — or under 30 words — the page is re-read
by a local vision model and the blocks are marked as VLM-sourced.

**Q. Why show OCR confidence to the user?**
Because a number read off a bad photocopy might be wrong. A citation from a 72%
page carries a warning and a one-click link to the original page.

**Q. How does code execution stay safe?**
Docker container with `network_mode=none` — no network interface at the kernel
level — read-only root filesystem, no capabilities, runs as `nobody`, with CPU,
memory and timeout limits. Plus a static check before it runs.

**Q. Why SSE and not WebSocket?**
The stream is one-way: server to client. SSE is plain HTTP, works through
proxies, reconnects natively, and needs no extra protocol. We add a 15-second
heartbeat so a silent stretch — like waiting on an approver — isn't mistaken for
a dead connection.

**Q. How do you test something non-deterministic?**
Two ways. Unit and integration tests run against a deterministic mock provider —
364 of them, no GPU needed. Model behaviour is measured separately by an eval
harness with thresholds, so a regression fails a number rather than a test.

**Q. What's your database schema?**
26 tables in five groups: identity (users, roles, permissions), content
(documents, pages, blocks, chunks), conversation (conversations, messages,
citations, agent_runs, steps), governance (approvals, artifacts, audit_events),
and operations (routing decisions, sandbox executions, eval runs).

**Q. How would you scale this?**
Qdrant shards; the API is stateless behind a load balancer; the model layer
moves from a laptop to a GPU server by editing one YAML file. The honest answer
is we've tested at 10 documents — the corpus is synthetic because real MRPL
documents are confidential, which is the point of the product.

**Q. What was the hardest bug?**
Runs vanishing when a browser closed. Under anyio, an `await` inside a `finally`
block during cancellation re-raises, so the finalisation never ran and the row
stayed "running" forever. Fixed with a detached task on an independent session —
and the first test we wrote passed against the bug, so we had to rewrite the
test before we could trust the fix.

**Q. What doesn't work / what's next?**
Vision escalation works but is slow — about a minute a page. Our refusal rate is
80%, not 100%; one case slipped through and our own eval flagged it. We've
tested at 10 documents, not 10,000. Next: a real VLM pass over P&IDs with tag
cross-validation, and CI.

---

## 10 · If asked "what did *you* build?"

Answer with a layer and a hard problem, not a list of files:

> "I owned [the agent orchestration / retrieval / ingestion / frontend]. The
> part I'd point at is [e.g. the citation resolver] — the difficulty was that
> the model emits its own ids and a marker pointing at nothing looks exactly
> like evidence, so we resolve every marker against retrieved evidence and
> delete the ones that don't resolve. That's what makes the 0% hallucinated
> citation number real."

Whatever you claim, be able to open that file and explain a decision in it.

---

## 11 · Three sentences to close on

1. Everything runs on the plant's own hardware on open weights — verifiable from
   the running system, not a claim in a README.
2. Every answer is traceable to a page, and when the documents don't cover the
   question it says so.
3. We measured it: 0% hallucinated citations, recall@10 of 1.00, and OCR error
   cut from 16.3% to 6.4% — and the numbers came from a harness we built before
   we started tuning.
