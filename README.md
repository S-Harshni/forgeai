# ForgeAI: the agent that builds agents

ForgeAI takes a **one-paragraph brief** and a **folder of documents**. With no human involved, it produces a **working, tested RAG agent**:
- a system prompt
- a retrieval configuration
- the tools the agent needs
- an output contract
- an auto-generated eval set

It then runs a critic–optimizer loop on the agent and writes an **honest quality report**.

Everything runs locally on small open models through [Ollama](https://ollama.com):
- `qwen2.5:3b` generates.
- `llama3.2:3b` judges. It is a different model family, which counters self-preference.

## Quick start

```bash
ollama serve &                       # if not already running
ollama pull qwen2.5:3b && ollama pull llama3.2:3b
ollama pull nomic-embed-text         # optional: enables hybrid BM25 + embedding retrieval
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt

# Build an agent (unattended)
.venv/bin/python forge.py --brief demos/hr_policy/brief.txt --docs demos/hr_policy/docs --out runs/hr_policy --auto

# Chat with any produced agent
.venv/bin/python chat.py runs/hr_policy/agent.json            # interactive
.venv/bin/python chat.py runs/hr_policy/agent.json --debug    # show retrieval, tools, citations
```

**Review day:** run `sh review.sh "<brief text or brief.txt>" <docs_folder>`. It checks Ollama and the models, runs the deterministic tests, then the factory, and prints the report summary.

## Web app

```bash
.venv/bin/pip install fastapi uvicorn python-multipart
.venv/bin/python server.py        # open http://localhost:8765
```

| Page | What it shows |
|---|---|
| **Build an agent** | Write a brief, drop in documents, and watch the 7 stages run live with the factory log streaming beside them. |
| **Overview** | Final and held-out scores, judge agreement, and cost; the pipeline with what each stage concluded; which requested topics the documents can and cannot support; every tool decision and its evidence; what still fails. |
| **Exam** | Every generated test case, filterable by result, type and dev/held-out split. Click a case to see the agent's answer, the judge's verdict on each expected fact, and what retrieval surfaced. |
| **Repairs** | Score per iteration (dev, held-out, overall); each patch with why it was tried, what it fixed or broke, and whether it was kept or reverted; a diff of the final prompt against the first draft. |
| **Agent** | The shipped system prompt, retrieval settings, output contract, tools and version history. Download `agent.json`. |
| **Try it** | Chat with the produced agent. Answers show clickable citations (with the cited text), the tool-call trace, and abstentions. |
| **Report** | The full `report.md`. |

The frontend is plain HTML, CSS and JS in `web/` with no build step. The API is in `server.py`.

Leave out `--auto` to get the interactive mode. You then answer up to 5 clarifying questions (press Enter to let ForgeAI decide), and you approve or edit `requirements.json` before generation continues (the human checkpoint).

Other flags:
- `--brief` accepts a file path or the brief text itself.
- `--max-iters N`: maximum optimisation iterations (default 3).
- `--time-budget SEC`: stop optimising once this much time has passed.
- `FORGE_TOKEN_BUDGET=N`: hard token budget.
- `--reuse-evals`: regression mode. Re-runs the old eval set against a regenerated agent.

## Pipeline

```
brief + docs
  │
  ├─[1] Intake interviewer ──── ≤5 questions (user, or auto-answered and logged as assumptions)
  │                              → requirements.json: scope, audience, tone, must-answer, must-refuse
  ├─[2] Groundability guard ─── each must-answer topic: retrieval relevance + LLM check
  │                              whose evidence quote must really exist in the docs
  │                              → unsupported topics are FLAGGED and moved to must-refuse
  ├─[3] Agent generator ─────── declarative agent_v0.json: sectioned system prompt,
  │                              chunking/retrieval params, tools (LLM proposal ∩ evidence gate),
  │                              output contract
  ├─[4] Eval generator ──────── ≥20 cases from the docs: answerable, multi-doc, ambiguous,
  │                              out-of-scope. Generated, then validated; split into dev and holdout
  ├─[5] Judge ───────────────── deterministic checks first, then a different-model LLM judge
  │                              on single facts only. Calibrated on known-label cases
  ├─[6] Critic–optimizer ────── classify failures → ONE targeted patch → re-run (max 3);
  │                              patches that lower the score are reverted
  └─[7] Report ──────────────── agent.json, scorecard.md, report.md, iterations.json, transcripts
```

### Design choices that make the pipeline harder to fool

| Risk | Check |
|---|---|
| A small model emits broken JSON | Every structured call is validated with Pydantic, retried with the error fed back, then falls back to a safe default. Unattended runs don't crash. |
| The guard model says "yes, covered" without evidence | Its evidence sentence must be found in the excerpts (fuzzy match); otherwise the "yes" is ignored. It is combined with an absolute lexical relevance score. |
| An eval case has wrong expected facts | Every key fact must appear in its source excerpt, with numbers matching exactly, or the case is dropped. The same applies to questions that leak their answer and to duplicates. |
| An eval fact is real but doesn't answer its question, or a paraphrase changes the meaning | **Round-trip check:** a different model answers each generated question from the source excerpt alone. Only key facts it reproduces are kept; otherwise the case is dropped. |
| The guard backs a topic with a real but off-topic quote ("on-time rating" → a discount sentence) | **Vocabulary check:** if the corpus never uses a content word of the topic ("rating", "tax"), the topic is a gap unless a verified on-topic quote exists. |
| The agent's calculator call uses invented operands | Every number in a calculator expression must appear in the question, the retrieved excerpts or earlier tool results; otherwise the call is rejected before it runs. |
| Small models get date arithmetic wrong | `current_date(add_days=N)` does the arithmetic. Date questions always get today's date. |
| Failures caused by the eval set, not the agent | The report flags suspect cases (e.g. an "out-of-scope" question that is highly relevant to the docs) but still counts them as failures. |
| An exam question has more than one correct answer in the corpus ("price of *the* fuel injector nozzle" when there are two) | **Uniqueness check:** the source excerpt is shown among the most similar excerpts from the rest of the corpus. If a rival matches the question's words as well as the source but has different figures, or the independent answer uses a rival's figure, the case is rejected. |
| Typos and slang make retrieval miss ("ovtime", "stat day") | Out-of-vocabulary query words are mapped to the nearest corpus word (edit distance ≤ 2). Before abstaining, the agent rewrites the query once into document vocabulary. |
| Threshold rules are misapplied (approval limits) | The calculator also evaluates comparisons (`3960 <= 5000`), and the grounding rules tell the agent to state the value first, then quote the range that contains it. |
| An "out-of-scope" eval question is actually answerable | It is dropped if retrieval finds a highly relevant passage. |
| The agent hallucinates figures | Every number in an answer must appear in the retrieved text, a tool result or the question. |
| The agent cites something it was never shown | Citations are checked against the retrieved chunk ids. |
| An unneeded tool gets attached (weather on an HR bot) | The tool is attached only if the LLM proposes it **and** the brief contains evidence for it. Every decision, including rejections, is logged in `tool_rationale`. |
| The LLM judge has biases | **Position:** pointwise yes/no on one fact at a time; no pairwise comparisons. **Self-preference:** the judge is a different model family, and the LLM is consulted only after code. **Verbosity:** one fact at a time, answers truncated, numbers checked in code. The judge is calibrated against padded wrong answers. |
| The optimizer overfits the eval set | About a third of the cases form a **holdout set** the optimizer never sees. The report shows the dev vs holdout gap. |
| A "fix" makes things worse | A patch that lowers the dev score or the total is reverted, and logged as reverted. |
| A rewrite causes regressions | Fixes are targeted patches to one prompt section or one retrieval parameter, never a full regeneration. `fixed_cases` and `newly_broken_cases` are logged per iteration. |

### Failure categories and targeted fixes
| Category | How it is detected | Fix ladder (one per iteration) |
|---|---|---|
| retrieval_miss | key facts not in the retrieved context, or the expected source was not retrieved | lower the abstain gate (if that is what caused it) → `top_k`+2 → LLM query expansion → smaller chunks |
| hallucination | answered an out-of-scope question, or stated numbers not in the context | generalised decline categories in the REFUSAL section → raise the abstain gate → strict-figures rule → self-check rule |
| wrong_refusal | refused although the facts were retrieved | "answer informal questions if an excerpt is relevant" → lower the gate → `top_k`+2 |
| prompt_defect | facts were in context but omitted, or the output contract was broken | mandatory citations → complete-answer rule → raise `max_words` |

## Tool registry
`csv_lookup` · `calculator` (safe AST evaluator) · `current_date` · `weather` (open-meteo, no API key).
At runtime the agent may chain up to two tool calls, for example a lookup followed by a calculation. It can only call tools attached to its definition.

## Demo runs (all artifacts committed)
| Run | Documents | Deliberate gap in the brief → guard result | Tools chosen | Evals | Baseline → final | Held-out | Time |
|---|---|---|---|---|---|---|---|
| [`runs/hr_policy`](runs/hr_policy) | 3 HR policy PDFs | payslip tax deductions → **flagged** | none | 23 | 22 → 22/23 (96%) | 86% | 4 min |
| [`runs/ship_spares`](runs/ship_spares) | catalogue CSV, 2 supplier PDFs, policy Markdown | supplier on-time rating → **flagged** | csv_lookup, calculator, current_date (no weather) | 22 | 20 → 20/22 (91%) | 100% | 12 min |
| [`runs/selftest_it_helpdesk`](runs/selftest_it_helpdesk) - generalisation check on a brief written after the factory was built | DOCX + TXT | self-service password reset → not flagged (merged into "Passwords") | none | 22 | 20 → 22/22 (100%) | 100% | 9 min |

How to read these numbers:
- **HR** stops after the baseline because every dev case passed. Its one failure is a held-out case, and the factory never uses held-out cases to choose repairs.
- **IT** reaches 100% after two repairs that each fixed a dev case and broke nothing. Its own report warns that a perfect score on a small, self-generated exam is weak evidence: only 5 out-of-scope cases survived validation there.
- **Each `report.md`** lists the remaining failures and explains them.

Each run folder contains:
- `brief.txt`, `requirements.json`
- `agent_v0.json` (first draft) and `agent.json` (final)
- `evals.json`, `judge_calibration.json`
- `iterations.json` (the change log), `scorecard.md`, `report.md`
- `final_transcripts.json`, `run.log`, `usage.json`

The demo documents are LLM-synthesized; the prompts are in [`demos/SYNTHESIS_PROMPTS.md`](demos/SYNTHESIS_PROMPTS.md). Every prompt the factory uses, including the eval-generation prompts, is in [`prompts/`](prompts).

## Troubleshooting
If runs stall (long gaps between requests), restart Ollama with its log going to a file or to `/dev/null`, not to a pipe nobody reads: `ollama serve > /dev/null 2>&1`. The client retries with a 90-second per-request timeout (`FORGE_REQUEST_TIMEOUT`) and counts stalls in `usage.json`.

## Model choice (measured, not assumed)
The produced agent's model is part of its definition (`"model"` in `agent.json`; override with `FORGE_AGENT_MODEL`). We re-ran the same exams with both installed Qwen models:

| Exam | qwen2.5:3b | qwen2.5-coder:7b |
|---|---|---|
| Ship spares (23 cases) | 19/23 in 191 s | 20/23 in 514 s |
| HR policy (23 cases) | 20/23 in 81 s | 20/23 in 160 s |

The 7B model gained one case for 2–2.7× the latency, so the default stays 3B: a live, unattended run has to finish while the reviewers are watching.

## Known limitations
- 3B models have weak instruction following. Expect some prompt-defect failures to remain; the report says which ones.
- Eval questions come from single excerpts. They are more literal than real user questions, so real-world accuracy is likely lower than the scorecard.
- Without an embedding model, retrieval is BM25 only, and heavily paraphrased questions can miss. Pull `nomic-embed-text` to enable hybrid retrieval.
- Scanned PDFs (images without a text layer) are skipped with a warning. There is no OCR.

## Repository layout
```
forge.py            factory CLI          chat.py         generic chat runner (CLI)
server.py           web API + app        web/            frontend (no build step)
forgeai/llm.py      Ollama client, JSON validation/retry, budget
forgeai/ingest.py   pdf/txt/md/csv/docx/json loading + chunking
forgeai/retrieval.py BM25 + optional embeddings, absolute relevance score
forgeai/intake.py   interviewer, groundability guard, human checkpoint
forgeai/generator.py agent definition + tool selection
forgeai/tools.py    tool registry
forgeai/runtime.py  the agent runtime (shared by chat and evals)
forgeai/evalgen.py  eval generation + validation
forgeai/judge.py    deterministic + LLM judge, calibration
forgeai/optimizer.py critic-optimizer loop
forgeai/report.py   scorecard + report
prompts/            every prompt the factory uses
demos/              briefs + documents for the two demos
runs/               committed artifacts of the demo runs
```
