"""Stage 6: critic-optimizer loop.

run evals -> classify failures (judge) -> apply ONE targeted patch for the dominant failure
category (never a full regeneration) -> re-run. Max 3 iterations. Only *dev* failures are
visible to the optimizer; *holdout* cases are scored but never used to choose fixes, so the
report can show whether a fix generalised or just overfit. A patch that lowers the dev score
is reverted and logged as such.
"""
from __future__ import annotations

import copy
import time
from collections import Counter
from dataclasses import dataclass

from pydantic import BaseModel, Field

from .llm import USAGE, BudgetExceeded, json_call, render
from .models import AgentDefinition, EvalCase
from .runtime import Agent
from .judge import Verdict, judge_case, summarize


class Cats(BaseModel):
    categories: list[str] = Field(default_factory=list)


@dataclass
class Fix:
    id: str
    category: str
    description: str


PRIORITY = ["hallucination", "retrieval_miss", "wrong_refusal", "prompt_defect"]


def run_evals(defn: AgentDefinition, cases: list[EvalCase], docs, log=print) -> tuple[list[Verdict], list[dict]]:
    agent = Agent(defn, docs)
    verdicts, transcripts = [], []
    for c in cases:
        resp = agent.ask(c.question)
        v = judge_case(c, resp, defn.output_contract.citations_required)
        verdicts.append(v)
        transcripts.append({"case": c.id, "question": c.question, **resp.to_dict(),
                            "verdict": v.__dict__})
        mark = "PASS" if v.passed else f"FAIL[{v.category}]"
        log(f"    {c.id} {c.type:<12} {c.split:<7} {mark}")
    return verdicts, transcripts


# ------------------------------------------------------------------ patches
def _apply(defn: AgentDefinition, category: str, failing: list[Verdict], cases: dict[str, EvalCase],
           tried: set[str]) -> tuple[AgentDefinition, Fix] | None:
    d = copy.deepcopy(defn)
    r = d.retrieval
    s = d.prompt_sections
    ids = [v.case_id for v in failing]

    if category == "retrieval_miss":
        gated = sum(1 for v in failing if v.checks.get("gate"))
        ladder = []
        if gated >= max(1, len(failing) // 2) and r.min_relevance > 0.08:
            ladder.append(("lower_gate", lambda: setattr(r, "min_relevance", round(r.min_relevance - 0.08, 2)),
                           f"abstain gate fired on {gated} miss(es): min_relevance {r.min_relevance} -> {round(r.min_relevance - 0.08, 2)}"))
        ladder += [
            ("top_k_up", lambda: setattr(r, "top_k", r.top_k + 2), f"top_k {r.top_k} -> {r.top_k + 2}"),
            ("query_rewrite", lambda: setattr(r, "query_rewrite", True), "enable LLM keyword query expansion before retrieval"),
            ("smaller_chunks", lambda: (setattr(r, "chunk_size", max(60, int(r.chunk_size * 0.7))),
                                        setattr(r, "overlap", max(15, int(r.overlap * 0.7)))),
             f"chunk_size {r.chunk_size} -> {max(60, int(r.chunk_size * 0.7))} (more precise chunks)"),
            ("top_k_up2", lambda: setattr(r, "top_k", r.top_k + 2), f"top_k {r.top_k} -> {r.top_k + 2}"),
        ]
    elif category == "hallucination":
        oos = [cases[v.case_id].question for v in failing if cases[v.case_id].should_refuse]
        num = [v for v in failing if v.checks.get("unsupported_numbers")]
        ladder = []
        if oos:
            def add_topics():
                cats = json_call(render("optimizer_generalize", questions="\n".join(f"- {q}" for q in oos),
                                        scope=d.description), Cats, fallback=lambda: Cats(), stage="optimizer")
                extra = [c for c in cats.categories if isinstance(c, str)][:3] or ["questions not answered by the documents"]
                s["refusal"] += "\n- Also decline: " + "; ".join(extra) + "."
                add_topics.desc = f"added generalised decline categories to REFUSAL section: {extra}"
            add_topics.desc = ""
            ladder.append(("refusal_topics", add_topics, None))
            ladder.append(("raise_gate", lambda: setattr(r, "min_relevance", round(r.min_relevance + 0.07, 2)),
                           f"min_relevance {r.min_relevance} -> {round(r.min_relevance + 0.07, 2)} (abstain more)"))
        if num or not oos:
            ladder.append(("strict_figures", lambda: s.__setitem__("grounding", s["grounding"] +
                           "\n- Every number, amount, date or code in your answer must be copied exactly from an excerpt. "
                           "If a figure is not in the excerpts, do not state one."),
                           "GROUNDING: added rule that every figure must be copied verbatim from an excerpt"))
        ladder.append(("check_step", lambda: s.__setitem__("grounding", s["grounding"] +
                       "\n- Before answering, check: is each claim supported by a cited excerpt? If not, remove it or refuse."),
                       "GROUNDING: added self-check step before answering"))
    elif category == "wrong_refusal":
        ladder = [
            ("soften_refusal", lambda: s.__setitem__("refusal", s["refusal"] +
             "\n- Informal, vague or misspelled questions are still in scope: if any excerpt answers them, ANSWER. "
             "Refuse only when no excerpt is relevant."),
             "REFUSAL: answer informal/partial matches when an excerpt is relevant"),
            ("lower_gate", lambda: setattr(r, "min_relevance", max(0.05, round(r.min_relevance - 0.08, 2))),
             f"min_relevance {r.min_relevance} -> {max(0.05, round(r.min_relevance - 0.08, 2))}"),
            ("top_k_up", lambda: setattr(r, "top_k", r.top_k + 2), f"top_k {r.top_k} -> {r.top_k + 2}"),
        ]
    else:  # prompt_defect
        no_cite = sum(1 for v in failing if v.checks.get("citation_ok") is False)
        ladder = []
        if no_cite:
            ladder.append(("cite_rule", lambda: s.__setitem__("format", s["format"] +
                           '\n- "citations" must never be empty when you answer: list the [id] of each excerpt used.'),
                           f"FORMAT: mandatory non-empty citations ({no_cite} case(s) lacked them)"))
        ladder += [
            ("complete_answer", lambda: s.__setitem__("format", s["format"] +
             "\n- Answer every part of the question. State the specific figures, limits, conditions and names from the "
             "excerpts rather than summarising them vaguely."),
             "FORMAT: require complete answers with specific figures"),
            ("longer_answers", lambda: setattr(d.output_contract, "max_words", d.output_contract.max_words + 60),
             f"max_words {d.output_contract.max_words} -> {d.output_contract.max_words + 60}"),
        ]

    for fid, fn, desc in ladder:
        key = f"{category}:{fid}"
        if key in tried:
            continue
        tried.add(key)
        fn()
        desc = desc or getattr(fn, "desc", fid)
        d.version += 1
        d.changelog.append(f"v{d.version}: [{category}] {desc} (targets {', '.join(ids)})")
        return d, Fix(key, category, desc)
    return None


def optimize(defn: AgentDefinition, cases: list[EvalCase], docs, max_iters: int = 3, log=print,
             time_budget_s: float = 0) -> dict:
    case_map = {c.id: c for c in cases}
    t0 = time.time()
    log("\n  iteration 0 (baseline)")
    verdicts, transcripts = run_evals(defn, cases, docs, log)
    best = {"defn": defn, "verdicts": verdicts, "summary": summarize(verdicts), "transcripts": transcripts}
    iterations = [{"iteration": 0, "version": defn.version, "change": "baseline", "summary": best["summary"],
                   "kept": True, "tokens_so_far": USAGE.prompt_tokens + USAGE.completion_tokens}]
    log(f"  baseline: {best['summary']['passed']}/{best['summary']['total']} "
        f"(dev {best['summary']['dev']}, holdout {best['summary']['holdout']}) failures={best['summary']['failures']}")
    tried: set[str] = set()
    not_paying = 0
    stop_reason = f"reached max iterations ({max_iters})"
    for it in range(1, max_iters + 1):
        dev_fail = [v for v in best["verdicts"] if not v.passed and v.split == "dev"]
        if not dev_fail:
            stop_reason = "all dev cases pass - nothing left to fix without peeking at holdout"
            break
        if time_budget_s and time.time() - t0 > time_budget_s:
            stop_reason = f"time budget {time_budget_s}s exhausted"
            break
        counts = Counter(v.category for v in dev_fail)
        order = sorted(counts, key=lambda c: (-counts[c], PRIORITY.index(c)))
        patched = None
        for cat in order:
            patched = _apply(best["defn"], cat, [v for v in dev_fail if v.category == cat], case_map, tried)
            if patched:
                break
        if not patched:
            stop_reason = "no untried targeted fix left for the remaining failure categories"
            break
        new_defn, fix = patched
        targeted = [v.case_id for v in dev_fail if v.category == fix.category]
        log(f"\n  iteration {it}: fix {fix.id} -> {fix.description}")
        try:
            verdicts, transcripts = run_evals(new_defn, cases, docs, log)
        except BudgetExceeded as e:
            stop_reason = f"budget exhausted: {e}"
            break
        summ = summarize(verdicts)
        before = best["summary"]
        fixed = [cid for cid in targeted if next(v for v in verdicts if v.case_id == cid).passed]
        broke = [v.case_id for v in verdicts if not v.passed and
                 next(b for b in best["verdicts"] if b.case_id == v.case_id).passed]
        # A patch must pay for itself ON DEV ALONE: held-out results must never influence a decision,
        # or the held-out score stops being an honest estimate. Ties are reverted (keep the simpler definition).
        d_new, d_old = summ["dev"] or 0, before["dev"] or 0
        kept = d_new > d_old
        entry = {"iteration": it, "version": new_defn.version, "fix": fix.id, "category": fix.category,
                 "change": fix.description, "why": f"{counts[fix.category]} dev failure(s) classified {fix.category}",
                 "targeted_cases": targeted, "fixed_cases": fixed, "newly_broken_cases": broke,
                 "score_before": {"overall": before["overall"], "dev": before["dev"], "holdout": before["holdout"]},
                 "score_after": {"overall": summ["overall"], "dev": summ["dev"], "holdout": summ["holdout"]},
                 "summary": summ, "kept": kept,
                 "tokens_so_far": USAGE.prompt_tokens + USAGE.completion_tokens}
        iterations.append(entry)
        log(f"  -> {summ['passed']}/{summ['total']} (dev {before['dev']} -> {summ['dev']}, holdout "
            f"{before['holdout']} -> {summ['holdout']}); fixed {fixed}; broke {broke}; "
            f"{'KEPT' if kept else 'REVERTED'}")
        if kept:
            best = {"defn": new_defn, "verdicts": verdicts, "summary": summ, "transcripts": transcripts}
            not_paying = 0
        else:
            best["defn"].changelog.append(f"(reverted attempt: [{fix.category}] {fix.description})")
            not_paying += 1
            if not_paying >= 2 and it < max_iters:
                stop_reason = "early stop: two consecutive patches did not improve the score"
                break
    return {"best": best, "iterations": iterations, "stop_reason": stop_reason}
