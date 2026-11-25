"""Stage 5: judge. Deterministic checks first; a small LLM judge only for what code can't decide.

Defences against known LLM-judge failure modes:
- position bias: the judge never compares two answers; every call is a pointwise yes/no
  on ONE atomic fact, so there is no order to be biased by.
- self-preference: the judge is a different model family (llama) from the generator (qwen),
  and the LLM is only consulted for paraphrase matching after deterministic checks.
- verbosity bias: the judge sees one fact at a time, is told length is irrelevant, answers are
  truncated, and numbers are checked in code (a wrong number can't be talked past the judge).
- calibration: `calibrate()` measures judge agreement on synthetic cases with known labels,
  including padded (verbose) wrong answers, and the result is published in the report.
"""
from __future__ import annotations

import random
import re
from dataclasses import dataclass, field

from pydantic import BaseModel

from .llm import JUDGE_MODEL, json_call, render
from .models import EvalCase
from .runtime import Response
from .textutil import fact_present, looks_like_refusal, numbers

CATEGORIES = ["retrieval_miss", "prompt_defect", "hallucination", "wrong_refusal"]


class FactJ(BaseModel):
    stated: bool = False


class Para(BaseModel):
    answer: str = ""


class RefJ(BaseModel):
    declined: bool = False


@dataclass
class Verdict:
    case_id: str
    type: str
    split: str
    passed: bool
    category: str | None
    reason: str
    checks: dict = field(default_factory=dict)
    facts: list[dict] = field(default_factory=list)


def _truncate(text: str, words: int = 220) -> str:
    w = text.split()
    return " ".join(w[:words]) + (" ..." if len(w) > words else "")


def fact_stated(fact: str, answer: str) -> tuple[bool, str, float]:
    score = fact_present(fact, answer)
    if score >= 0.75:
        return True, "deterministic", score
    fnums = numbers(fact)
    if fnums and not fnums <= numbers(answer):
        return False, "deterministic (number missing)", score
    if score < 0.25 and not fnums:
        # almost no lexical overlap; still allow the LLM a paraphrase check
        pass
    j = json_call(render("judge_fact", fact=fact, answer=_truncate(answer)), FactJ, model=JUDGE_MODEL,
                  fallback=lambda: FactJ(stated=False), stage="judge", temperature=0.0, retries=1)
    return bool(j.stated), "llm-judge", score


def unsupported_numbers(answer: str, context: str, question: str) -> set[str]:
    allowed = numbers(context) | numbers(question)
    found = numbers(answer)
    # ignore list markers / tiny ordinals that are rarely factual claims
    return {n for n in found - allowed if not (n.isdigit() and int(n) <= 3)}


def judge_case(case: EvalCase, resp: Response, citations_required: bool = True) -> Verdict:
    answer = resp.answer or ""
    retrieved_docs = {r["doc"] for r in resp.retrieved}
    checks: dict = {"refused": resp.refused, "format_ok": resp.format_ok, "gate": resp.gate,
                    "best_relevance": max((r["relevance"] for r in resp.retrieved), default=0.0)}

    if case.should_refuse:
        declined = resp.refused
        method = "deterministic"
        if not declined:
            j = json_call(render("judge_refusal", question=case.question, answer=_truncate(answer)), RefJ,
                          model=JUDGE_MODEL, fallback=lambda: RefJ(declined=False), stage="judge",
                          temperature=0.0, retries=1)
            declined, method = j.declined, "llm-judge"
        checks.update(refusal_ok=declined, refusal_method=method)
        if declined:
            return Verdict(case.id, case.type, case.split, True, None, "correctly refused", checks)
        return Verdict(case.id, case.type, case.split, False, "hallucination",
                       "answered an out-of-scope question instead of refusing", checks)

    source_hit = bool(set(case.expected_sources) & retrieved_docs)
    facts_in_ctx = [fact_present(f, resp.context_text) >= 0.75 for f in case.key_facts] if resp.context_text else []
    checks.update(source_retrieved=source_hit)

    if resp.refused:
        if not source_hit or (facts_in_ctx and not any(facts_in_ctx)) or resp.gate:
            cat, why = "retrieval_miss", f"refused; expected source {case.expected_sources} not (fully) retrieved"
            if resp.gate:
                why += f" [abstain gate: {resp.gate}]"
        else:
            cat, why = "wrong_refusal", "refused although the answer was in the retrieved context"
        checks.update(correct=False, grounded=True)
        return Verdict(case.id, case.type, case.split, False, cat, why, checks)

    facts = []
    for f in case.key_facts:
        ok, method, score = fact_stated(f, answer)
        facts.append({"fact": f, "stated": ok, "method": method, "lexical": round(score, 2)})
    coverage = sum(x["stated"] for x in facts) / max(1, len(facts))
    correct = coverage >= 0.6
    bad_nums = unsupported_numbers(answer, resp.context_text, case.question)
    citation_ok = (not citations_required) or bool(resp.citations)
    grounded = not bad_nums and citation_ok
    checks.update(coverage=round(coverage, 2), correct=correct, grounded=grounded,
                  unsupported_numbers=sorted(bad_nums), citation_ok=citation_ok,
                  facts_in_context=sum(facts_in_ctx))

    if correct and grounded and resp.format_ok:
        return Verdict(case.id, case.type, case.split, True, None, "correct and grounded", checks, facts)

    if bad_nums:
        cat, why = "hallucination", f"answer contains figures not in any retrieved text: {sorted(bad_nums)}"
    elif not correct and (not source_hit or (facts_in_ctx and not all(facts_in_ctx))):
        cat, why = "retrieval_miss", f"key facts not in retrieved context (coverage {coverage:.0%})"
    elif not correct:
        cat, why = "prompt_defect", f"facts were retrieved but the answer omitted them (coverage {coverage:.0%})"
    else:
        cat, why = "prompt_defect", "correct content but output contract violated (citations/format)"
    return Verdict(case.id, case.type, case.split, False, cat, why, checks, facts)


def summarize(verdicts: list[Verdict]) -> dict:
    def rate(vs):
        return round(sum(v.passed for v in vs) / len(vs), 3) if vs else None
    by_type = {}
    for t in ["answerable", "multi_doc", "ambiguous", "out_of_scope"]:
        vs = [v for v in verdicts if v.type == t]
        if vs:
            by_type[t] = {"pass": sum(v.passed for v in vs), "total": len(vs), "rate": rate(vs)}
    fails = {c: sum(1 for v in verdicts if v.category == c) for c in CATEGORIES}
    answerable = [v for v in verdicts if v.type != "out_of_scope"]
    return {
        "overall": rate(verdicts),
        "dev": rate([v for v in verdicts if v.split == "dev"]),
        "holdout": rate([v for v in verdicts if v.split == "holdout"]),
        "passed": sum(v.passed for v in verdicts),
        "total": len(verdicts),
        "by_type": by_type,
        "failures": fails,
        "grounded_rate": rate([v for v in answerable if v.checks.get("grounded") is not None
                               and not v.checks.get("refused")]) if answerable else None,
    }


# ------------------------------------------------------------------ calibration
def calibrate(cases: list[EvalCase], n: int = 8, seed: int = 3) -> dict:
    """Run the fact judge on synthetic answers with KNOWN labels and report agreement."""
    rng = random.Random(seed)
    pool = [c for c in cases if not c.should_refuse and c.key_facts]
    rng.shuffle(pool)
    padding = (" This is covered in detail in the relevant policy, which has been carefully reviewed and "
               "is applied consistently across all teams and locations to ensure fairness and compliance.")
    results = []
    for c in pool[:n]:
        fact = c.key_facts[0]
        others = [x for x in pool if x is not c and x.key_facts]
        # positive: a paraphrase-ish answer containing the fact
        pos = f"Yes - {fact}."
        # negative 1: wrong number (if any), padded to look authoritative (verbosity attack)
        nums = re.findall(r"\d+", fact)
        if nums:
            neg = fact.replace(nums[0], str(int(nums[0]) + 7), 1)
        else:
            neg = others[0].key_facts[0] if others else "No relevant information."
        neg = f"{neg}.{padding}{padding}"
        para = json_call(render("judge_calib_paraphrase", fact=fact), Para,
                         fallback=lambda: Para(answer=""), stage="calibration", temperature=0.5).answer
        trials = [(pos, True, "positive"), (neg, False, "verbose-negative")]
        if para and numbers(para) >= numbers(fact):  # only keep paraphrases that preserved the figures
            trials.append((para, True, "paraphrase-positive"))
        for answer, label, kind in trials:
            got, method, _ = fact_stated(fact, answer)
            results.append({"case": c.id, "kind": kind, "expected": label, "got": got, "method": method,
                            "answer": answer[:200]})
    agree = sum(r["expected"] == r["got"] for r in results)
    fp = sum(1 for r in results if not r["expected"] and r["got"])
    fn = sum(1 for r in results if r["expected"] and not r["got"])
    by_kind = {}
    for r in results:
        k = by_kind.setdefault(r["kind"], [0, 0])
        k[0] += r["expected"] == r["got"]
        k[1] += 1
    return {"n": len(results), "agreement": round(agree / max(1, len(results)), 3),
            "by_kind": {k: f"{a}/{n}" for k, (a, n) in by_kind.items()},
            "llm_judge_invocations": sum(1 for r in results if r["method"] == "llm-judge"),
            "false_positives": fp, "false_negatives": fn, "details": results}
