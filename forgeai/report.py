"""Stage 7: honest reporting - scorecard + report that says what still fails and why."""
from __future__ import annotations

from collections import Counter

from .models import AgentDefinition, EvalCase, Requirements


def pct(x):
    return "n/a" if x is None else f"{x * 100:.0f}%"


def scorecard_md(iterations: list[dict]) -> str:
    lines = ["# Scorecard", "",
             "| Iter | Change | Kept | Overall | Dev | Holdout | Answerable | Multi-doc | Ambiguous | Out-of-scope | "
             "Retrieval miss | Prompt defect | Hallucination | Wrong refusal |",
             "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for it in iterations:
        s = it["summary"]
        bt = s["by_type"]

        def t(k):
            return f"{bt[k]['pass']}/{bt[k]['total']}" if k in bt else "-"
        f = s["failures"]
        lines.append(f"| {it['iteration']} | {it.get('change', '')[:70]} | {'yes' if it.get('kept', True) else 'reverted'} | "
                     f"{s['passed']}/{s['total']} ({pct(s['overall'])}) | {pct(s['dev'])} | {pct(s['holdout'])} | "
                     f"{t('answerable')} | {t('multi_doc')} | {t('ambiguous')} | {t('out_of_scope')} | "
                     f"{f['retrieval_miss']} | {f['prompt_defect']} | {f['hallucination']} | {f['wrong_refusal']} |")
    return "\n".join(lines) + "\n"


def report_md(req: Requirements, defn: AgentDefinition, cases: list[EvalCase], result: dict,
              evalstats: dict, calibration: dict, usage: dict, elapsed: float) -> str:
    best = result["best"]
    s = best["summary"]
    its = result["iterations"]
    verdicts = best["verdicts"]
    qmap = {c.id: c for c in cases}
    out = [f"# ForgeAI report - {defn.name}", ""]
    out += ["## Bottom line", "",
            f"- **Final score: {s['passed']}/{s['total']} ({pct(s['overall'])})** - dev {pct(s['dev'])}, "
            f"**holdout {pct(s['holdout'])}** (holdout cases were never shown to the optimizer).",
            f"- Baseline was {its[0]['summary']['passed']}/{its[0]['summary']['total']} "
            f"({pct(its[0]['summary']['overall'])}); {len(its) - 1} optimisation iteration(s); stop reason: "
            f"{result['stop_reason']}.",
            f"- Judge calibration: {pct(calibration.get('agreement'))} agreement on {calibration.get('n', 0)} "
            f"synthetic known-label checks ({calibration.get('false_positives', 0)} false positives, "
            f"{calibration.get('false_negatives', 0)} false negatives; by kind {calibration.get('by_kind', {})}; "
            f"LLM judge consulted on {calibration.get('llm_judge_invocations', 0)}).",
            f"- Cost: {usage['calls']} LLM calls, {usage['total_tokens']:,} tokens, {elapsed / 60:.1f} min wall clock.", ""]
    gap = (s["dev"] or 0) - (s["holdout"] or 0)
    if s["holdout"] is not None and gap > 0.15:
        out.append(f"> **Overfitting warning:** dev beats holdout by {gap * 100:.0f} points - some fixes may be "
                   f"tuned to the dev questions rather than the domain.\n")
    if s["overall"] == 1.0:
        out.append("> A perfect score on an auto-generated eval set is more likely a weak eval set than a perfect "
                   "agent. Treat with suspicion; see eval-generation stats below.\n")

    out += ["## Groundability (requirements stage)", "",
            "| Topic | Status | Relevance | Evidence |", "|---|---|---|---|"]
    for v in req.groundability:
        out.append(f"| {v.topic} | **{v.status}** | {v.relevance:.2f} | {(v.evidence[:90] + '...') if v.evidence else v.reason[:90]} |")
    if req.unsupported_topics:
        out.append(f"\n**Flagged gaps:** {', '.join(req.unsupported_topics)} - the documents cannot support these, so "
                   f"they were moved to must-refuse instead of letting the agent improvise answers.")
    if req.assumptions:
        out += ["", "**Assumptions made without the requester (unattended mode):**"] + [f"- {a}" for a in req.assumptions]

    out += ["", "## Tool selection", ""] + [f"- `{k}`: {v}" for k, v in defn.tool_rationale.items()]

    out += ["", "## What still fails and why", ""]
    fails = [v for v in verdicts if not v.passed]
    if not fails:
        out.append("No failing cases in the final run (see caveat above).")
    else:
        cats = Counter(v.category for v in fails)
        out.append("Failure mix: " + ", ".join(f"{k} x{n}" for k, n in cats.most_common()) + "\n")
        out += ["| Case | Type | Split | Category | Question | Diagnosis | Eval case suspect? |",
                "|---|---|---|---|---|---|---|"]
        suspects = 0
        for v in fails:
            q = qmap[v.case_id].question.replace("|", "/")
            rel = v.checks.get("best_relevance", 0)
            sus = ""
            if v.type == "out_of_scope" and rel >= 0.5:
                sus = f"possibly answerable (doc relevance {rel:.2f}) - label may be wrong"
            elif v.type in ("ambiguous", "answerable", "multi_doc") and rel < 0.1:
                sus = "question shares almost no terms with any document - may be garbled"
            suspects += bool(sus)
            out.append(f"| {v.case_id} | {v.type} | {v.split} | {v.category} | {q[:90]} | {v.reason[:140]} | {sus or '-'} |")
        if suspects:
            out.append(f"\n{suspects} failure(s) look like eval-set defects rather than agent defects. They are still "
                       f"counted as failures in the score above; the score is not adjusted in our favour.")
        out += ["", "**Likely root causes:**"]
        if cats.get("retrieval_miss"):
            out.append("- *Retrieval misses*: lexical/embedding search did not surface the passage - typically "
                       "paraphrased or vague questions whose wording does not overlap the document text.")
        if cats.get("hallucination"):
            out.append("- *Hallucinations*: the small model answered from general knowledge or stated figures absent "
                       "from the excerpts; the grounding check caught them (they would have shipped otherwise).")
        if cats.get("wrong_refusal"):
            out.append("- *Wrong refusals*: the passage was retrieved but the model declined - over-cautious refusal "
                       "rules trade recall for safety.")
        if cats.get("prompt_defect"):
            out.append("- *Prompt defects*: relevant text was in context but the answer omitted key facts or broke "
                       "the output contract (no citations) - a 3B model's instruction-following limit.")

    out += ["", "## Optimisation log", ""]
    for it in its[1:]:
        out.append(f"- **Iter {it['iteration']}** `{it['fix']}` - {it['change']}  \n  why: {it['why']}; targeted "
                   f"{it['targeted_cases']}; fixed {it['fixed_cases'] or 'none'}; newly broken "
                   f"{it['newly_broken_cases'] or 'none'}; overall {pct(it['score_before']['overall'])} -> "
                   f"{pct(it['score_after']['overall'])} (holdout {pct(it['score_before']['holdout'])} -> "
                   f"{pct(it['score_after']['holdout'])}); **{'kept' if it['kept'] else 'reverted'}**")
    if len(its) == 1:
        out.append("- no iterations ran")

    out += ["", "## Eval set provenance", "",
            f"- {len(cases)} cases, generated by the prompts in `prompts/evalgen_*.txt` (none hand-written).",
            f"- Generation attempts: {evalstats.get('generated')}; dropped as unverifiable (key fact not literally in "
            f"source): {evalstats.get('dropped_unverifiable_facts')}; duplicates: {evalstats.get('dropped_duplicate')}; "
            f"leaky/empty: {evalstats.get('dropped_leaky_or_empty')}; 'out-of-scope' questions the docs actually answer: "
            f"{evalstats.get('dropped_oos_actually_answerable')}; failed round-trip check (an independent model, "
            f"reading only the source excerpt, could not reproduce the expected facts - i.e. a mislabeled case): "
            f"{evalstats.get('dropped_roundtrip_failed')}; not unique in the corpus (another passage fits the question "
            f"equally well with different figures): {evalstats.get('dropped_not_unique_in_corpus', 0)}.",
            "- Known limitation: questions are generated from single excerpts, so they skew towards literal, "
            "well-formed phrasing; real users will be messier than this set.",
            "", "## How the judge is defended", "",
            "- Deterministic first: refusal detection, key-fact matching (exact numbers), citation validity, and "
            "an unsupported-number check against retrieved text.",
            "- LLM judge (different model family from the generator) only rules on single facts that code could not "
            "match - pointwise, so no position bias; one fact at a time and truncated, so no verbosity reward.",
            "- Calibrated on known-label synthetic answers, including padded wrong answers (results above).",
            "", "## Cost by stage", "", "| Stage | Calls | Tokens | Seconds |", "|---|---|---|---|"]
    for k, v in usage["by_stage"].items():
        out.append(f"| {k} | {v['calls']} | {v['tokens']:,} | {v['seconds']} |")
    return "\n".join(out) + "\n"
