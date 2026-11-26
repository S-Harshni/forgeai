#!/usr/bin/env python3
"""ForgeAI - the agent that builds agents.

    python forge.py --brief brief.txt --docs ./docs --out runs/my_agent            # interactive intake
    python forge.py --brief brief.txt --docs ./docs --out runs/my_agent --auto     # fully unattended
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

from forgeai import llm
from forgeai.evalgen import generate_evals
from forgeai.generator import generate_agent
from forgeai.ingest import load_folder
from forgeai.intake import groundability_guard, human_checkpoint, interview
from forgeai.judge import calibrate
from forgeai.llm import USAGE
from forgeai.models import EvalCase, Requirements
from forgeai.optimizer import optimize
from forgeai.report import report_md, scorecard_md


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--brief", required=True, help="path to a text file with the brief, or the brief itself")
    ap.add_argument("--docs", required=True, help="folder of documents (pdf/txt/md/csv/docx/json)")
    ap.add_argument("--out", required=True, help="output folder for all artifacts")
    ap.add_argument("--auto", action="store_true", help="unattended: ForgeAI answers its own questions")
    ap.add_argument("--max-iters", type=int, default=3)
    ap.add_argument("--time-budget", type=float, default=0, help="seconds; stop optimising when exceeded")
    ap.add_argument("--reuse-evals", action="store_true", help="reuse evals.json in --out (regression mode)")
    args = ap.parse_args()

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    log_f = open(out / "run.log", "w")

    def log(msg=""):
        print(msg, flush=True)
        log_f.write(str(msg) + "\n")
        log_f.flush()

    t0 = time.time()
    brief_path = Path(args.brief)
    brief = brief_path.read_text().strip() if brief_path.exists() else args.brief.strip()
    have = llm.available_models()
    if llm.PROVIDER == "openai":
        if not llm.OPENAI_API_KEY:
            sys.exit("FORGE_PROVIDER is an API provider but no key is set: export GROQ_API_KEY=... (or OPENAI_API_KEY).")
        if not have:
            sys.exit(f"Could not list models at {llm.OPENAI_BASE_URL} - check the API key.")
        missing = [m for m in (llm.GEN_MODEL, llm.JUDGE_MODEL) if m not in have]
        if missing:
            sys.exit(f"Models not offered by {llm.OPENAI_BASE_URL}: {missing}. Set FORGE_GEN_MODEL / FORGE_JUDGE_MODEL.")
    else:
        if not have:
            sys.exit(f"Ollama is not reachable at {llm.OLLAMA_URL}. Start it with `ollama serve`.")
        missing = [m for m in (llm.GEN_MODEL, llm.JUDGE_MODEL) if m not in have]
        if missing:
            sys.exit(f"Missing Ollama models: {missing}. Run: " + " && ".join(f"ollama pull {m}" for m in missing))
    log(f"ForgeAI | provider={llm.PROVIDER} generator={llm.GEN_MODEL} judge={llm.JUDGE_MODEL} | mode={'auto' if args.auto else 'interactive'}")
    log(f"BRIEF: {brief}\n")
    (out / "brief.txt").write_text(brief + "\n")

    log("[1/7] Loading documents")
    docs = load_folder(args.docs)
    for d in docs:
        log(f"  - {d.name} ({d.kind}, {len(d.rows) if d.kind == 'table' else len(d.text.split())} "
            f"{'rows' if d.kind == 'table' else 'words'})")

    log("\n[2/7] Intake interview")
    interactive = not args.auto and sys.stdin.isatty()
    req = interview(brief, docs, interactive, log)

    log("\n[3/7] Groundability guard")
    req = groundability_guard(req, docs, log)
    if interactive:
        req = human_checkpoint(req, out / "requirements.json", log)
    (out / "requirements.json").write_text(req.model_dump_json(indent=2))

    log("\n[4/7] Generating agent definition")
    defn = generate_agent(req, brief, docs, args.docs, log)
    (out / "agent_v0.json").write_text(defn.model_dump_json(indent=2))

    log("\n[5/7] Generating eval set")
    prev_verdicts = {}
    if args.reuse_evals and (out / "final_transcripts.json").exists():
        for t in json.loads((out / "final_transcripts.json").read_text()):
            prev_verdicts[t["case"]] = t["verdict"]["passed"]
    if args.reuse_evals and (out / "evals.json").exists():
        cases = [EvalCase.model_validate(c) for c in json.loads((out / "evals.json").read_text())["cases"]]
        evalstats = {"generated": "reused", "final": len(cases)}
        log(f"  regression mode: reusing {len(cases)} existing cases")
    else:
        cases, evalstats = generate_evals(req, docs, log)
        (out / "evals.json").write_text(json.dumps(
            {"stats": evalstats, "cases": [c.model_dump() for c in cases]}, indent=2))

    log("\n[6/7] Judge calibration + critic-optimizer loop")
    calib = calibrate(cases)
    log(f"  judge calibration: {calib['agreement'] * 100:.0f}% agreement on {calib['n']} known-label checks")
    result = optimize(defn, cases, docs, max_iters=args.max_iters, log=log, time_budget_s=args.time_budget)
    best = result["best"]

    log("\n[7/7] Writing artifacts")
    final = best["defn"]
    (out / "agent.json").write_text(final.model_dump_json(indent=2))
    (out / "iterations.json").write_text(json.dumps(result["iterations"], indent=2, default=str))
    (out / "final_transcripts.json").write_text(json.dumps(best["transcripts"], indent=2, default=str))
    (out / "judge_calibration.json").write_text(json.dumps(calib, indent=2))
    (out / "scorecard.md").write_text(scorecard_md(result["iterations"]))
    if prev_verdicts:
        now = {v.case_id: v for v in best["verdicts"]}
        broke = [cid for cid, ok in prev_verdicts.items() if ok and cid in now and not now[cid].passed]
        healed = [cid for cid, ok in prev_verdicts.items() if not ok and cid in now and now[cid].passed]
        qmap = {c.id: c.question for c in cases}
        lines = ["# Regression report (old eval set vs regenerated agent)", "",
                 f"- previously passing, now FAILING: {len(broke)}", f"- previously failing, now passing: {len(healed)}", ""]
        lines += [f"- **{cid}** broke: {qmap[cid]} -> {now[cid].category}: {now[cid].reason}" for cid in broke]
        (out / "regression.md").write_text("\n".join(lines) + "\n")
        log(f"  regression: {len(broke)} case(s) broke, {len(healed)} healed -> {out}/regression.md")
    elapsed = time.time() - t0
    usage = USAGE.to_dict()
    (out / "usage.json").write_text(json.dumps(usage, indent=2))
    (out / "report.md").write_text(report_md(req, final, cases, result, evalstats, calib, usage, elapsed))
    s = best["summary"]
    holdout = "n/a" if s["holdout"] is None else f"{s['holdout'] * 100:.0f}%"
    log(f"\nDONE in {elapsed / 60:.1f} min. Final {s['passed']}/{s['total']} ({s['overall'] * 100:.0f}%), "
        f"holdout {holdout}.")
    log(f"Artifacts in {out}/  ->  chat with it:  python chat.py {out}/agent.json")


if __name__ == "__main__":
    main()
