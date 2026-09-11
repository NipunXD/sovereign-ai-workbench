# Demo bank — Sovereign AI Workbench

Everything below is checked against the real system. Numbers in the
**Expected** column are what the corpus actually contains, so you can tell a
good run from a bad one without trusting the model.

Three variants of every capability (**A / B / C**). A is the safest for a live
audience; C is usually the most impressive and the most likely to wander.

---

## 0 · Before anyone is watching

```bash
make infra            # postgres, qdrant, redis
make dev              # api + web
python scripts/demo_corpus.py    # writes data/demo/ (4 files)
```

Then, in this order:

1. **Warm the models.** Sign in as `senior`, ask anything, throw the answer
   away. A cold load costs ~7s per model and the first question of a demo is
   the worst place to pay it.
2. **Run the proof script** in a second terminal tab and leave the output up:
   ```bash
   make demo
   ```
   It logs in as four different people, gets refused twice on purpose, and
   verifies the audit hash chain. If a judge says "prove it", this is the
   answer. Takes ~5 minutes — never run it live.
3. **Curate the sidebar.** Run beats 2, 4, 5 and 7 below once each, then rename
   the saved sessions so the list reads like a menu. Every one of them replays
   in two seconds with its full trace, evidence map and documents intact.

**Timing reality:** a live run is 1.5–2.5 minutes. Budget **two** live runs in
a 10-minute slot and replay everything else.

---

## 1 · Grounded lookup — "every claim traces to a page"

| | Prompt | Expected |
|---|---|---|
| **A** ✅ | What is the depressurisation rate limit for V-1201, and what hold time does the SOP require? | 2 bar/min; 4 hours at 4 barg, 120 °C. Cites SOP-4412. ~100% grounded |
| **B** | What does the 2029 inspection report say about CML-04 on V-1201? | 9.20 mm against a t-min of 8.00 mm. Cites INSP-2029 (OCR ~95%) |
| **C** | Which isolation valves are shown for V-1201 on the P&ID? | HV-1205, XV-1207, PSV-1207. Cites the drawing — OCR'd at 92.8%, so expect an OCR warning chip |

**What to point at, in order:** Trace tab (routing → plan → 8 passages) →
hover `[1]` so the map lights up → click `[1]` → the page with the yellow box →
the grounding ring in the footer.

> "The bounding box came from the ingester, not the model. The model cannot
> move it."

---

## 2 · Calculation — "the arithmetic is checked, not generated"

| | Prompt | Expected |
|---|---|---|
| **A** ✅ | Using the 2023 and 2029 CML-04 readings for V-1201, compute the corrosion rate in mm/year and the remaining life against the 8.0 mm minimum. | 12.50 → 9.20 over 6 years = **0.55 mm/yr**; margin 1.20 mm → **~2.2 years** |
| **B** | P-101A vibration rose from 2.8 to 6.4 mm/s during 2029. At that rate, when does it cross the 7.1 mm/s alarm limit? *(needs the CSV ingested)* | ~0.33 mm/s per month → crosses **~February 2030** |
| **C** | Compare the loss rate at CML-04 and CML-06 on V-1202 and tell me which location governs the re-inspection interval. *(needs INSP-2030-V1202)* | CML-06: 2.45 mm since 2028, margin 0.85 mm — **CML-06 governs** |

Watch the **tool call** appear in the trace. Say: *"That ran in a Docker
container with no network. The model wrote the code; it did not get to choose
where it ran."*

---

## 3 · Refusal — "it declines instead of inventing"

| | Prompt | Expected |
|---|---|---|
| **A** ✅ | What was the purge duration used during the 2019 turnaround? | Amber **"declined — not in the corpus"** |
| **B** ✅ | What is the design pressure of vessel V-9999? | Declines; names the equipment it has no record of |
| **C** ⚠️ | Who is the current plant manager of MRPL? | **Known weak case.** Our own eval (g06) records it answering this one. Only use it if you *want* to show the eval catching a failure |

> "In a refinery a confidently wrong number is worse than no number. Our eval
> measures this: 80% refusal rate, 0% hallucinated citations."

---

## 4 · Document generation + the two-person rule

| | Prompt | Expected |
|---|---|---|
| **A** ✅ | Produce a Word report of the V-1201 thickness survey with every reading in a table. | .docx, inline `[n]` superscripts, numbered Sources page, waits for approval |
| **B** | Create an Excel workbook of the CML readings for V-1201 with the corrosion rate per location. | .xlsx, one row per CML |
| **C** | Make a PowerPoint for the turnaround review covering the V-1201 findings and the 2029 budget. | .pptx — pulls from the **restricted** memo, so run it as `senior`, not `viewer` |

**The sequence that lands:**
1. Run stops at the approval banner — *"the agent stopped itself"*
2. Second window as `approver` → Approvals → approve
3. First window resumes on its own, document appears
4. As the **requester**, show *"You raised this request, so it needs a second person"*
5. Expand **Provenance** → who, when, which models, digest, every source with page and OCR confidence
6. Open the .docx — *"that provenance block is written inside the file, so it travels with the document"*

---

## 5 · Access control — the cheapest 40 seconds in the demo

No model time at all. Do all three.

| | Action | Expected |
|---|---|---|
| **A** ✅ | Documents page as `senior`, then as `viewer` | Different counts, visibly different classification bar |
| **B** ✅ | Retrieval page, search `V-1201` as both | The restricted memo's passages are simply not candidates for `viewer` |
| **C** | Ask *"Summarise the CDU turnaround budget for 2029"* as each | `senior` answers (INR 142 crore); `viewer` cannot see the source and says so |

> "This is not the UI hiding rows. The filter is inside the retrieval query.
> You cannot prompt-inject your way past a WHERE clause."

---

## 6 · Ingest — "the knowledge base is yours to extend"

Four files in `data/demo/`, each exercising a different path. **All four are
verified end-to-end through the real ingester.**

| File | Path exercised | Ingest time |
|---|---|---|
| `INSP-2030-V1202.pdf` | native text | seconds |
| `WP-2030-0912-scanned.pdf` | OCR → **vision escalation** | ~1 min (a VLM call) |
| `P-101A-VIBRATION-2029.csv` | spreadsheet | seconds |
| `NOTE-2030-V1202.txt` | plain text | seconds |

### A ✅ — the refuse-then-answer arc (the best ingest beat)

1. **Before uploading**, ask: *"What is the minimum allowable thickness for
   V-1202?"* → **declines**, nothing in the corpus covers V-1202.
2. Upload `INSP-2030-V1202.pdf` (classification **confidential**, type
   **inspection**, department **inspection**).
3. Ask the same question again → **7.50 mm**, cited, page 1.

> "That is the product. Not a model that was trained on your plant — a model
> that reads your plant's documents, and reads the one you handed it thirty
> seconds ago."

### B — vision escalation (the only place FR-10 is visible)

Upload `WP-2030-0912-scanned.pdf`. Watch the ingest stages.

- OCR lands at **69% mean confidence**, under the **72%** gate in
  `config/ingest.yaml`
- The trace says *"mean confidence 69% below 72%"* → the page is re-read by
  `qwen3-vl:8b` on Ollama
- In the document viewer, toggle **regions**: OCR blocks and VLM blocks are
  drawn in different colours
- Any citation from it carries an **OCR nn% — verify** chip

⚠️ **Be honest about this one.** The vision model recovers the page but makes
mistakes on it — in testing it read `V-1202` as `V-1822` and `3 barg` as
`2 barg`. That is *why* the confidence warning and the "open the page" button
exist. Frame it that way and it is a strength; claim the recovery is perfect
and a judge who reads the page will catch you. Show the **trace and the
regions**, not the retrieved text.

### C — the conflict (most impressive, least predictable)

Upload **both** `INSP-2030-V1202.pdf` and `NOTE-2030-V1202.txt`, then ask:

> *"What re-inspection interval is recommended for V-1202, and is there any
> disagreement about it?"*

The report says **12 months**; the field note says **4 months** and shows its
arithmetic. A good answer cites both and says they disagree.

> "Two documents, one contradiction. It didn't average them and it didn't pick
> one — it showed you both and told you they disagree. That is what an
> engineer needs."

Also worth showing: the CSV has no page to photograph, so the viewer falls
back to the **extracted table** — *"there is no scan, so we show you exactly
what was indexed."*

---

## 7 · Three full rehearsal passes

**Pass 1 — cold (60 min).** Everything from a stopped stack. `make infra`,
`make dev`, generate demo files, run every A variant, run `make demo`. Goal:
nothing is missing. Write down anything that took longer than you expected.

**Pass 2 — timed (20 min).** Stopwatch on the table, full 10-minute run twice.
Goal: find the beat that always overruns and cut it. Decide your two live
questions and never change them again.

**Pass 3 — hostile (30 min).** Someone plays a sceptical judge and interrupts:
*"how do I know it's not calling an API?"*, *"what if the model is wrong?"*,
*"what doesn't work?"*. Goal: the answers are reflexes, not improvisation.

---

## The numbers (latest eval run)

| Suite | Result |
|---|---|
| Grounding | 0% hallucinated citations · 94% mean grounded ratio · 80% refusal rate · 8 cases |
| Retrieval | recall@10 **1.00** · nDCG@10 **0.81** · p95 **32 ms** · 20 cases |
| OCR | CER **6.4%** (1.5% ignoring spaces) at 400 DPI · down from 16.3% |
| Routing | lane accuracy **1.00** · fast-path p95 **0.06 ms** · 24 cases |

**364 tests · machine-enforced layer architecture · hash-chained audit · single offline install bundle**

> "We didn't measure these to put them on a slide. The OCR number moved from
> 16.3% to 6.4% *because* we measured it."

---

## If it goes wrong

| Symptom | Do this |
|---|---|
| Run hangs past ~3 min | Stop it, say *"that's a cold model load on a laptop"*, click a saved session instead |
| Model unloaded / 500 | The runner retries once by itself. If it fails again, switch to a saved session |
| Upload appears stuck | Native files are seconds; the scanned permit takes ~1 min for the vision call. Say so while it runs |
| Approval doesn't resume | Refresh the requester's tab — the run is stored, not lost. That *is* the feature |
| Judge asks something not in the corpus | Perfect. Type it in. It will decline, and that is beat 3 |

---
---

# 8 · The flows, click by click

What to click, what appears, what to say. Durations assume the models are
already warm.

## Roles, as actually seeded

| User | Clearance | Can | Cannot |
|---|---|---|---|
| `senior` | restricted | chat, generate documents, run code, ingest, classify | **approve** |
| `engineer` | confidential | chat, generate, ingest | run code, approve |
| `approver` | confidential | chat, **approve** | **generate** |
| `viewer` | internal | chat, read documents | everything else |
| `auditor` | internal | **read the audit log**, read documents | **chat** — no workbench at all |

The separation is real: `senior` cannot approve their own work, and `approver`
cannot produce the thing they approve.

---

## Flow 0 — Two windows (do this before you start)

The approval beat needs two people signed in at once.

1. Window 1, normal: sign in as `senior`. Put it on the projector.
2. Window 2, **private/incognito** (sessions are per-browser-profile): sign in
   as `approver`. Keep it behind, or on a second monitor.

Do not try to do this by signing out and back in — you lose the run on screen,
and the whole point is that the requester watches it resume.

---

## Flow 1 — Sign-in page · 20s

**Where:** `localhost:3000/login`, before you log in.

1. Leave it on screen while people settle.
2. Point at the six-stage strip: *"Understand, plan, retrieve, tools, validate,
   deliver. That is the actual pipeline — you'll watch it run in a minute."*
3. Point at the five role cards and the coloured clearance edge: *"Five roles,
   and they deliberately cannot do each other's jobs."*
4. Click **Enter as Senior Inspection Engineer**.

---

## Flow 2 — Sovereignty · 30s

**Where:** header, top right.

1. Click the green **AIR-GAPPED** badge.
2. A panel drops down listing every host the deployment can reach, plus
   **External AI services: none**.

> "That isn't a claim in a README. It reads the running process's egress
> configuration. If an external service appeared in that list this badge turns
> red. Unplug the network and nothing on this screen changes."

3. Click the **memory chip** next to it (`3.5/14 GB`) to show which models are
   resident. Close both.

---

## Flow 3 — A grounded run · 2.5 min · ⭐ the core flow

**Where:** Workbench (first icon in the rail).

1. Click the **grounded lookup** suggestion card, or type variant 1A.
2. Press Enter. **Now stop watching the answer and drive the right-hand panel.**

**While it runs**, narrate the Trace tab top to bottom as each line appears:

| Appears | Say |
|---|---|
| `Routed to reasoning` + model chip | "A classifier picked the model. Not hardcoded." |
| `Planned 3 steps` | "That plan is the agent's, not ours." |
| `Retrieved 8 passages`, `hybrid` chips | "Hybrid — dense plus sparse, fused. `V-1201` is a token BM25 catches and embeddings blur." |
| Answer streaming | *(say nothing, let them read)* |

**When it finishes** it switches to **Evidence** by itself.

3. **Hover** a `[1]` chip in the answer → the matching node lights up on the map.
4. **Hover** a node on the map → every chip citing it lights up in the answer.
   > "Two views of the same fact. You can move between them with your eyes."
5. Look at the footer: **grounding ring**, passages read, tool calls, duration.
   > "That ring is computed by a validator that re-checks every marker against
   > retrieved evidence and deletes references pointing at nothing."

---

## Flow 4 — Down to the page · 40s (continues Flow 3)

1. **Click** the `[1]` chip. The panel switches to **Source**, showing the full
   indexed passage with the cited span highlighted.
2. Click **show the surrounding text** — the chunks either side appear.
   > "The sentence before the one a model quoted is often the one that changes
   > its meaning."
3. Click **Open page** (or the **page** toggle at the top).
4. The scanned page appears with a **yellow box** around the cited region.
   > "That box came from the ingester, not the model. The model cannot move it."
5. Click **regions** in the viewer header — every extracted block is outlined,
   colour-coded by source (native / OCR / VLM).

---

## Flow 5 — The refusal · 1.5 min

**Where:** same chat.

1. Type variant 3A or 3B.
2. Say while it runs: *"This is the beat I'd judge us on. In a refinery a
   confidently wrong number is worse than no number."*
3. Footer shows the amber **declined — not in the corpus** chip.
4. Quote the eval: **80% refusal rate, 0% hallucinated citations.**

---

## Flow 6 — Access control · 40s · no model time, do all three

**Where:** Documents, then Retrieval. Sign out, in as `viewer`, repeat.

| | As `senior` | As `viewer` |
|---|---|---|
| Documents page | **10 documents** — 5 internal, 4 confidential, 1 restricted | **5 documents** — all internal |
| Classification bar | four segments | one segment |
| Retrieval → `turnaround budget 2029` | filter chip reads `classification<=restricted; departments=inspection,process`; top hits are the **Internal Memo** | filter chip reads `classification<=internal; departments=operations`; the memo **does not appear at all** |

1. Documents page as `senior` → point at the corpus strip and classification bar.
2. Rail → **Retrieval** → search `turnaround budget 2029` → point at the
   **access filter chip** and the memo in the results.
3. Sign out → in as `viewer` → same two pages, same query.

> "The memo isn't ranked lower — it was never a candidate. The filter is inside
> the retrieval query. You cannot prompt-inject your way past a WHERE clause."

4. While you are `viewer`: point at the **rail** — Ingest, Approvals and Audit
   are simply not there. *"A destination you cannot use is not shown and then
   refused. It is not shown."*

---

## Flow 7 — Document + the two-person rule · 2 min · needs Flow 0

**Window 1, as `senior`:**

1. Ask variant 4A (or open the saved session that already did).
2. The run stops on the **approval banner**: *waiting for an approver*.
   > "The agent stopped itself. A document that leaves the plant needs a second
   > person."
3. Rail → **Approvals**. Your own request is there, and it says:
   *"You raised this request, so it needs a second person."*
   > "We state the rule rather than hiding the buttons."

**Window 2, as `approver`:**

4. Rail → **Approvals** → the request is in the queue with the question that
   raised it.
5. Type a note in the comment box → click **Approve**.

**Back to Window 1:**

6. The banner changes to *approved by M. Devadiga* and **the run resumes on its
   own**. The document card appears.
7. Click **Provenance** on the card → who requested, who approved and when,
   which models, the digest, every source with page numbers and OCR confidence.
8. Click **Download**, open the .docx: inline `[1]` superscripts in the body, a
   numbered **Sources** page, and the provenance block.
   > "That block is written inside the file, so it travels with the document
   > when someone emails it."

**If you only have one window:** approve first, then open the saved session —
it replays with the approval already granted. Weaker, but it works.

---

## Flow 8 — Ingest: refuse, then answer · 2 min · ⭐ best ingest beat

**Where:** Workbench, then Ingest, then back.

1. Ask: *"What is the minimum allowable thickness for V-1202?"* → **declines**.
2. Rail → **Ingest**.
3. Set **Classification** `confidential`, **Type** `inspection`,
   **Departments** `inspection`.
4. Drop `data/demo/INSP-2030-V1202.pdf`. Watch the stage pipeline:
   sniff → extract → OCR → vision → chunk → embed → index. Seconds.
5. Rail → **Workbench** → **New** → ask the same question again.
6. **7.50 mm**, cited, page 1.

> "Not a model trained on your plant — a model that reads your plant's
> documents, including the one you handed it thirty seconds ago."

---

## Flow 9 — Vision escalation · 90s · handle with care

1. Ingest → drop `data/demo/WP-2030-0912-scanned.pdf`
   (classification `internal`, type `inspection`).
2. Watch the **vision** stage. It reports *"re-read 1 page with vision"*.
   OCR came in at **69%**, under the **72%** gate in `config/ingest.yaml`.
3. Documents → open it → toggle **regions**: OCR blocks and VLM blocks in
   different colours.

> "OCR knew it had done a bad job. The page went to a vision model running on
> Ollama, on this machine."

⚠️ **Show the trace and the regions — not the recovered text.** The VLM reads
tags wrong on damage this heavy (`V-1202` → `V-1822` in testing). If someone
reads the page, that is the moment to point at the **OCR nn% — verify** chip:
*"which is exactly why we surface the confidence and put the original page one
click away."*

---

## Flow 10 — Conflicting evidence · 2 min · most impressive

1. Ingest both `INSP-2030-V1202.pdf` and `NOTE-2030-V1202.txt`.
2. Ask: *"What re-inspection interval is recommended for V-1202, and is there
   any disagreement about it?"*
3. The report says **12 months**; the note says **4 months** and shows its
   arithmetic. Both should be cited.
4. Open the Evidence map — two documents, both feeding the same question.

> "Two documents, one contradiction. It didn't average them and it didn't pick
> one. That is what an engineer needs."

---

## Flow 11 — Audit · 45s

**Where:** sign in as `auditor`.

1. Point at the **rail first**: there is no Workbench icon. *"Audit reads. It
   cannot act. The people who act are not the people who review."*
2. Rail → **Audit log** → click **Verify chain**.
   > "Every record commits to the hash of the one before it. Altering any
   > historical row invalidates every hash after it, and this reports the exact
   > sequence number where it breaks."
3. Point at **Refused actions — last 24 hours**: *"Denials first. That's the
   question an auditor actually opens this page to answer."*
4. Optional: click **Export evidence** — the log downloads as JSONL.

**The flourish:** sign back in as `senior`, go to `/audit` directly. It says
*"The audit log is not yours to read… this refusal was itself recorded."*

---

## Flow 12 — Models and portability · 45s

**Where:** rail → **System** (as `senior` or `admin`).

1. **Model manifest** — nine models, logical names (`reasoning.primary`,
   `vision.primary`, `embed.primary`) mapped to backends and physical models.
   > "Swap qwen for llama by editing a YAML file. No application code changes."
2. **Backends** — LM Studio and Ollama both live and healthy.
   > "Two genuinely different runtimes on every multimodal request, so the
   > provider abstraction is proven continuously, not asserted once."
3. **Memory budget** — 14 GB, what's resident.
   > "A cold load costs seconds, so routing prefers a resident model that's
   > good enough over a marginally better cold one."
4. **Routing lanes** — the fallback order per lane.

---

## Flow 13 — Saved sessions · 20s · your safety net

**Where:** the Sessions sidebar on the Workbench.

1. Click any earlier session. It replays instantly — answer, trace, evidence
   map, citations, documents, approval state.
2. Point out the URL changed to `/chat/<id>`, and the message count in the list.

> "Every run is kept, and only for the person who had it. Another user asking
> for this conversation by id gets 'not found' — not 'forbidden', because
> 'forbidden' would confirm it exists."

**This is what you fall back to if a live run misbehaves.** Nothing about a
replayed session is faked: it is the recorded event stream of a real run,
replayed through the same code the live stream uses.

---

## Putting it together

**The 10-minute run:** 1 → 2 → 3 → 4 → 6 → 5 → 7 → 11 → close on the numbers.

**If you get 15 minutes:** add 8 and 10 after 7.

**Keep in your pocket for Q&A:** 9 (vision), 12 (portability), 13 (sessions),
and `make demo` running in a terminal tab.

**Cut first if you're short:** 4 (fold it into 3), 12, 9.
