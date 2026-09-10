# Sovereign On-Premise Agentic AI Workbench

**Smart India Hackathon 2026 · Problem statement 26117 · Team AIDUO**
Client: Mangalore Refinery and Petrochemicals Limited (MRPL)

An agentic AI workbench for confidential industrial work that runs entirely
inside the plant. Open-weight models, local inference, no external API calls —
not as a configuration option, but as a property the system can demonstrate on
demand.

---

## The problem, and why "just use an LLM API" fails

A refinery's inspection reports, P&IDs, turnaround budgets and incident records
are exactly the documents that cannot leave the site. That rules out every hosted
model. What is left has to work offline, and it has to be trusted with numbers
that decide whether a pressure vessel stays in service.

That second constraint shapes more of this system than the first. A confident,
fluent, wrong figure about a vessel is worse than no answer at all, so the
system is built to cite what it says and to decline when the corpus cannot
support an answer.

## What it does

- **Ingests what a plant actually has** — scanned PDFs, photocopies, handwriting,
  P&ID sheets, Excel and CSV. OCR with preprocessing chosen by measurement, and
  vision-model escalation for pages the OCR engine cannot read.
- **Answers with citations** — hybrid dense + sparse retrieval, every claim
  traceable to a page and a bounding box in a real document.
- **Declines rather than invents** — an unanswerable question gets a refusal that
  names the documents it looked in.
- **Routes across models** — a manifest maps logical lanes (reasoning, vision,
  embedding, long-context) to physical models. Swapping a model is an edit to
  `config/models.yaml`, not a code change.
- **Runs code in a sandbox** — Docker with `--network=none`, read-only rootfs,
  dropped capabilities, no swap.
- **Generates Word, Excel and PowerPoint** — with provenance written *inside* the
  file, not beside it.
- **Gates documents behind a second person** — the requester cannot approve their
  own artifact.
- **Keeps a hash-chained audit log** — append-only, verifiable on demand.

## Measured, not asserted

Every number below comes from `make eval`, which runs against the real models and
the real corpus. Thresholds are asserted by the suites, so a regression fails the
run rather than being noticed later.

| | | |
|---|---|---|
| **Router** | lane accuracy | **1.00** |
| | vision capability rate | **1.00** — an image never reaches a model that cannot see |
| | fast-path p95 | **0.06 ms** (92% of traffic) |
| | classifier share | 0.08 — the escape hatch is bounded by frequency, not latency |
| **Retrieval** | recall@10 | **1.00** |
| | recall@5 | 0.90 |
| | MRR | 0.75 |
| | p95 latency | 27 ms |
| **OCR** | character error rate | **6.4%** on degraded scans |
| | ignoring word boundaries | **1.5%** — recognition is near-perfect; the gap is segmentation |
| **Grounding** | hallucinated citations | **0.00** |
| | answerable accuracy | **1.00** |
| | refusal rate on unanswerable | 0.80 – 1.00 |
| | failed runs | 0.00 |

The grounding refusal rate is given as a range because it is: one case — asking for MRPL's current plant manager, which no engineering document records — sits close enough to the model's threshold that it refuses on some runs and hedges on others. Quoting the better run would be the more flattering number and the less useful one.

The corpus these run against is synthetic and generated from known text, then
degraded through scan profiles — which is what makes exact OCR ground truth
possible at all.

Two measurements worth singling out, because they were surprises:

- **Adaptive thresholding made OCR nine times worse.** Raw 16.6% CER, CLAHE 7.6%,
  CLAHE + threshold 67.5%. Binarisation throws away the greyscale the recogniser's
  models depend on. The defaults record this in a comment so nobody re-enables it.
- **Tiling a P&ID is doing real work.** Whole-sheet recall 0%, two tiles 22%, six
  tiles 67% — precision 100% throughout.

## Running it

Needs Docker, Python 3.12, Node 20+, and LM Studio or Ollama with the models in
`config/models.yaml`.

```bash
make bootstrap     # deps, infra, migrations, demo users, seed corpus
make dev           # API on :8000, web on :3000
```

Then, in another terminal:

```bash
make demo          # scripted end-to-end walkthrough
```

`make demo` proves the claims rather than narrating them: it reads the process's
own outbound counter for the egress check, logs in as two different people to
show the clearance filter shortening one of their result lists, and tries to
approve an artifact as its own requester before approving it as someone else. It
stops on the first step it cannot complete.

| | |
|---|---|
| `make eval` | every eval suite, with thresholds and baseline deltas |
| `make test` | 226 tests against the deterministic mock provider — no models needed |
| `make check` | everything CI runs |
| `make airgap` | a single offline installation bundle |

## Air-gapped for real

`make airgap` produces one archive that installs on a machine with no route out:
container images, every Python wheel for the target platform, the OCR models
RapidOCR would otherwise fetch on first use, the built frontend, and the source.

It deliberately does **not** carry the LLM weights. They are the one component an
operator most likely already has, and baking 30 GB in would make the bundle
unusable over the media that actually gets carried into a plant. The installer
lists exactly which weights are needed and checks the platform before it starts.

The honest test of an air-gapped system is not a firewall rule. It is whether the
thing can be *installed* without one — because anything the bundle forgets becomes
a download request on a network that does not permit downloads, which is how
air-gapped systems quietly grow an exception.

## Layout

```
apps/api          FastAPI backend — agent loop, retrieval, ingestion, RBAC, audit
apps/web          Next.js frontend — chat, trace timeline, document viewer
config/           models, RBAC, routing, ingestion and tool policy — the seams
data/seed         synthetic MRPL corpus generator and OCR ground truth
evals/            four suites, their datasets, and the harness that runs them
services/sandbox  the no-network code execution image
scripts/          setup, seeding, the demo, and the air-gap bundle
```

`config/` is where the "sovereign" and "model-agnostic" claims live. Application
code never names a physical model; it asks for a lane.

## Security posture

- Clearance and department filters are applied **inside** the retrieval query —
  in the SQL and in the vector search — not by hiding rows in the frontend.
- Unknown clearance ranks *lowest* and unknown classification ranks *highest*, so
  a label nobody anticipated fails closed in both directions.
- Failed logins and the lockout counter are written on independent connections, so
  they survive the rollback of the transaction that rejected the attempt.
- The sandbox's isolation is verified against a control: the same image reaches
  1.1.1.1 without `--network=none` and fails with it.
- Approval is separation-of-duties enforced in the gate, with expiry so a run
  always terminates.

## License

Built for SIH 2026. Not currently licensed for redistribution.
