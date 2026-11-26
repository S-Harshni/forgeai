"""Stage 4: eval generator. Builds the test set FROM the documents, then distrusts it:
every key fact must be verifiably present in its source excerpt, trivially leaky and
duplicate questions are dropped, and 'out-of-scope' questions that the documents
actually answer are discarded as mislabeled."""
from __future__ import annotations

import random
import re

from pydantic import BaseModel, Field

from .ingest import Chunk, Document, chunk_documents
from .llm import JUDGE_MODEL, json_call, render
from .models import EvalCase, Requirements
from .retrieval import Index
from .textutil import fact_present, jaccard, looks_like_refusal, numbers, tokens

TARGET = {"answerable": 10, "multi_doc": 3, "ambiguous": 4, "out_of_scope": 6}
MIN_CASES = 22


class QA(BaseModel):
    question: str
    key_facts: list[str] = Field(default_factory=list)


class MultiQA(BaseModel):
    question: str
    fact_from_a: str = ""
    fact_from_b: str = ""


class OneQ(BaseModel):
    question: str


class Qs(BaseModel):
    questions: list[str] = Field(default_factory=list)


def _informative(c: Chunk) -> float:
    words = len(c.text.split())
    if words < 12:
        return 0
    return min(words, 120) / 120 + 0.5 * min(len(numbers(c.text)), 3)


def _pick_chunks(chunks: list[Chunk], n: int, rng: random.Random) -> list[Chunk]:
    """Round-robin across documents, most informative first, so every doc is tested."""
    by_doc: dict[str, list[Chunk]] = {}
    for c in chunks:
        if _informative(c) > 0:
            by_doc.setdefault(c.doc, []).append(c)
    for doc in by_doc:
        lst = by_doc[doc]
        rng.shuffle(lst)
        lst.sort(key=lambda c: -_informative(c) + rng.random() * 0.6)
    picked, docs = [], list(by_doc)
    while len(picked) < n and any(by_doc.values()):
        for d in docs:
            if by_doc[d] and len(picked) < n:
                picked.append(by_doc[d].pop(0))
    return picked


def _validate_facts(facts: list[str], source_text: str, question: str) -> list[str]:
    good = []
    for f in facts:
        f = str(f).strip().strip('"')
        if len(f.split()) < 1 or len(f) > 160:
            continue
        if fact_present(f, source_text) < 0.8:       # model invented / paraphrased the "quote"
            continue
        if fact_present(f, question) >= 0.9:          # answer leaked into the question
            continue
        good.append(f)
    return good[:3]


class RT(BaseModel):
    answer: object = ""


_CORPUS: dict = {"idx": None}


def _distractors(question: str, sources: list[Chunk], k: int = 3) -> list[Chunk]:
    idx = _CORPUS["idx"]
    if idx is None:
        return []
    src_ids = {c.id for c in sources}
    return [h.chunk for h in idx.search(question, k=k + len(sources)) if h.chunk.id not in src_ids][:k]


def _roundtrip(question: str, facts: list[str], excerpts: list[Chunk], stats: dict | None = None) -> list[str]:
    """Independent model (different family) answers the question from the source excerpt(s) shuffled
    among the most similar excerpts from the rest of the corpus.

    Returns the key facts it reproduced. Catches (a) facts that exist in the excerpt but don't answer
    the question, (b) paraphrases that changed the meaning, and (c) questions whose answer is not
    unique in the corpus (e.g. "price of the fuel injector nozzle" when two nozzles exist): if the
    answer uses a figure that only a competing excerpt contains, the case is ambiguous and rejected."""
    distract = _distractors(question, excerpts)
    # Deterministic ambiguity test: a rival excerpt that matches the question's words at least as well
    # as the source, but lacks the expected figures, means the question doesn't pin down one answer.
    fact_nums = set().union(set(), *(numbers(f) for f in facts))
    if fact_nums:
        qtok = set(tokens(question))
        src_cov = max(len(qtok & set(tokens(c.text))) for c in excerpts)
        for d in distract:
            if src_cov and len(qtok & set(tokens(d.text))) >= src_cov and not fact_nums <= numbers(d.text):
                if stats is not None:
                    stats["dropped_not_unique_in_corpus"] = stats.get("dropped_not_unique_in_corpus", 0) + 1
                return []
    shown = excerpts + distract
    random.Random(len(question)).shuffle(shown)  # no position cue for the source
    ex = "\n\n".join(f"[{c.doc}] {c.text}" for c in shown)
    a = json_call(render("evalgen_roundtrip", excerpts=ex, question=question), RT, model=JUDGE_MODEL,
                  fallback=lambda: RT(answer=""), stage="evalgen_roundtrip", temperature=0.0, retries=1).answer
    a = a if isinstance(a, str) else " ".join(map(str, a.values() if isinstance(a, dict) else a))
    if not a.strip() or "not answerable" in a.lower():
        return []
    src_nums = set().union(*(numbers(c.text) for c in excerpts)) | numbers(question)
    rival_nums = set().union(set(), *(numbers(c.text) for c in distract)) - src_nums
    if numbers(a) & rival_nums:
        if stats is not None:
            stats["dropped_not_unique_in_corpus"] = stats.get("dropped_not_unique_in_corpus", 0) + 1
        return []
    ok = []
    for f in facts:
        fn = numbers(f)
        if (fn and fn <= numbers(a)) or (not fn and fact_present(f, a) >= 0.5):
            ok.append(f)
    return ok


def _roundtrip_ok(question, facts, excerpts, stats=None) -> list[str]:
    ok = _roundtrip(question, facts, excerpts, stats)
    return ok if len(ok) * 2 >= len(facts) and ok else []


def _answerable_from_corpus(question: str) -> bool:
    """For 'out-of-scope' candidates: does an independent model find a grounded answer in the corpus?"""
    hits = _distractors(question, [], k=3)
    if not hits:
        return False
    ex = "\n\n".join(f"[{c.doc}] {c.text}" for c in hits)
    a = json_call(render("evalgen_roundtrip", excerpts=ex, question=question), RT, model=JUDGE_MODEL,
                  fallback=lambda: RT(answer="NOT ANSWERABLE"), stage="evalgen_roundtrip", temperature=0.0,
                  retries=1).answer
    a = a if isinstance(a, str) else " ".join(map(str, a.values() if isinstance(a, dict) else a))
    if not a.strip() or "not answerable" in a.lower() or looks_like_refusal(a):
        return False
    return fact_present(a, ex) >= 0.6  # the answer is mostly made of the excerpts' own words


SHORT_OK = {"a", "an", "am", "as", "at", "be", "by", "do", "go", "he", "if", "in", "is", "it", "me", "my", "no",
            "of", "on", "or", "so", "to", "up", "us", "we", "hr", "it", "id", "pc", "ok", "pm", "am", "vs", "eg"}


def _garbled(q: str) -> bool:
    """Informal rewrites are fine; word salad is not a fair test of anything."""
    words = re.findall(r"[A-Za-z]+", q)
    if len(words) < 3:
        return True
    frags = sum(1 for w in words if len(w) <= 2 and w.lower() not in SHORT_OK)
    return frags / len(words) > 0.2


def _clean_q(q: str) -> str:
    return q.strip().strip(".").strip().strip('"').strip()


def _dup(q: str, existing: list[EvalCase]) -> bool:
    return any(jaccard(q, e.question) > 0.6 for e in existing)


def generate_evals(req: Requirements, docs: list[Document], log=print, seed: int = 13) -> tuple[list[EvalCase], dict]:
    rng = random.Random(seed)
    words = sum(len(d.text.split()) for d in docs if d.kind == "text") + 12 * sum(len(d.rows) for d in docs if d.kind == "table")
    # Small corpora need smaller excerpts, or every question collapses onto the same few passages.
    chunks = chunk_documents(docs, 70 if words < 1500 else 150, 0)
    _CORPUS["idx"] = Index(chunks, mode="bm25")
    cases: list[EvalCase] = []
    stats = {"generated": 0, "dropped_unverifiable_facts": 0, "dropped_duplicate": 0,
             "dropped_oos_actually_answerable": 0, "dropped_leaky_or_empty": 0,
             "dropped_roundtrip_failed": 0}

    # ---- answerable --------------------------------------------------------
    picked = _pick_chunks(chunks, TARGET["answerable"] + 6, rng)
    # Small corpora have few excerpts: make up to 3 passes, asking for a *different* question each time.
    for n_try, c in enumerate((picked * 3)[: (TARGET["answerable"] + 6) * 2]):
        if sum(x.type == "answerable" for x in cases) >= TARGET["answerable"]:
            break
        stats["generated"] += 1
        avoid = "; ".join(x.question for x in cases if c.id in x.generated_from) or "(none)"
        qa = json_call(render("evalgen_answerable", audience=req.audience, doc=c.doc, excerpt=c.text,
                              avoid=avoid),
                       QA, fallback=lambda: QA(question=""), stage="evalgen",
                       temperature=0.4 + 0.2 * (n_try // max(1, len(picked))), seed=100 + n_try)
        if len(qa.question) < 8:
            stats["dropped_leaky_or_empty"] += 1
            continue
        facts = _validate_facts(qa.key_facts, c.text, qa.question)
        if not facts:
            stats["dropped_unverifiable_facts"] += 1
            continue
        if _dup(qa.question, cases):
            stats["dropped_duplicate"] += 1
            continue
        confirmed = _roundtrip_ok(qa.question, facts, [c], stats)
        if not confirmed:
            stats["dropped_roundtrip_failed"] += 1
            continue
        stats["facts_trimmed_by_roundtrip"] = stats.get("facts_trimmed_by_roundtrip", 0) + len(facts) - len(confirmed)
        facts = confirmed
        cases.append(EvalCase(id="", type="answerable", question=_clean_q(qa.question), should_refuse=False,
                              expected_sources=[c.doc], key_facts=facts, generated_from=[c.id],
                              prompt="evalgen_answerable"))

    # ---- multi-doc: pair an excerpt with its most related excerpt in ANOTHER document ----
    doc_names = sorted({c.doc for c in chunks})
    if len(doc_names) >= 2:
        pair_idx = Index(chunks, mode="bm25")
        anchors = _pick_chunks(chunks, 30, rng)
        rng.shuffle(anchors)
        attempts = 0
        seen_pairs = set()
        while sum(x.type == "multi_doc" for x in cases) < TARGET["multi_doc"] and attempts < 10 and anchors:
            ca = anchors[attempts % len(anchors)]
            attempts += 1
            related = [h.chunk for h in pair_idx.search(ca.text[:400], k=12)
                       if h.chunk.doc != ca.doc and _informative(h.chunk) > 0]
            if not related:
                continue
            cb = related[0] if (ca.id, related[0].id) not in seen_pairs else related[min(1, len(related) - 1)]
            seen_pairs.add((ca.id, cb.id))
            stats["generated"] += 1
            m = json_call(render("evalgen_multidoc", audience=req.audience, doc_a=ca.doc, doc_b=cb.doc,
                                 excerpt_a=ca.text, excerpt_b=cb.text),
                          MultiQA, fallback=lambda: MultiQA(question=""), stage="evalgen", temperature=0.4,
                          seed=200 + attempts)
            fa = _validate_facts([m.fact_from_a], ca.text, m.question)
            fb = _validate_facts([m.fact_from_b], cb.text, m.question)
            if len(m.question) < 8 or not fa or not fb:
                stats["dropped_unverifiable_facts"] += 1
                continue
            if _dup(m.question, cases):
                stats["dropped_duplicate"] += 1
                continue
            confirmed = _roundtrip(m.question, fa + fb, [ca, cb], stats)
            if len(confirmed) < 2:  # a multi-doc case must keep a fact from each document
                stats["dropped_roundtrip_failed"] += 1
                continue
            cases.append(EvalCase(id="", type="multi_doc", question=_clean_q(m.question), should_refuse=False,
                                  expected_sources=[ca.doc, cb.doc], key_facts=fa + fb,
                                  generated_from=[ca.id, cb.id], prompt="evalgen_multidoc"))
        if not any(x.type == "multi_doc" for x in cases):
            log("  WARNING: no multi-document case survived validation")

    # ---- ambiguous (paraphrases of verified answerable cases) --------------
    base = [c for c in cases if c.type == "answerable"]
    rng.shuffle(base)
    for b in base[: TARGET["ambiguous"] + 4]:
        if sum(x.type == "ambiguous" for x in cases) >= TARGET["ambiguous"]:
            break
        stats["generated"] += 1
        q = json_call(render("evalgen_ambiguous", question=b.question), OneQ,
                      fallback=lambda: OneQ(question=""), stage="evalgen", temperature=0.6,
                      seed=300 + len(cases))
        if len(q.question) < 8 or jaccard(q.question, b.question) > 0.85 or _dup(q.question, [x for x in cases if x is not b]):
            stats["dropped_duplicate"] += 1
            continue
        if _garbled(q.question):
            stats["dropped_garbled"] = stats.get("dropped_garbled", 0) + 1
            continue
        if any(fact_present(f, q.question) >= 0.9 for f in b.key_facts):
            stats["dropped_leaky_or_empty"] += 1
            continue
        src = [c for c in chunks if c.id in b.generated_from]
        if not _roundtrip_ok(q.question, b.key_facts, src, stats):  # paraphrase must keep the same answer
            stats["dropped_roundtrip_failed"] += 1
            continue
        cases.append(EvalCase(id="", type="ambiguous", question=_clean_q(q.question), should_refuse=False,
                              expected_sources=b.expected_sources, key_facts=b.key_facts,
                              generated_from=b.generated_from, prompt="evalgen_ambiguous"))

    # ---- out-of-scope ------------------------------------------------------
    idx = Index(chunks, mode="bm25")
    stats["generated"] += 1
    oos = json_call(render("evalgen_oos", scope=req.scope, audience=req.audience,
                           refuse="; ".join(req.must_refuse_topics),
                           unsupported="; ".join(req.unsupported_topics) or "(none flagged)",
                           n=TARGET["out_of_scope"] + 3),
                    Qs, fallback=lambda: Qs(), stage="evalgen", temperature=0.5)
    oos_q = ["Can you recommend a good recipe for chocolate cake?"]  # deterministic off-domain anchor
    oos_q += [q for q in oos.questions if isinstance(q, str) and len(q) > 8]
    for q in oos_q:
        if sum(x.type == "out_of_scope" for x in cases) >= TARGET["out_of_scope"]:
            break
        top = idx.search(q, k=1)
        if (top and top[0].relevance >= 0.8) or _answerable_from_corpus(q):  # docs answer it -> mislabeled
            stats["dropped_oos_actually_answerable"] += 1
            continue
        if _dup(q, cases):
            stats["dropped_duplicate"] += 1
            continue
        cases.append(EvalCase(id="", type="out_of_scope", question=_clean_q(q) + ("?" if q.strip().endswith("?") else ""), should_refuse=True,
                              prompt="evalgen_oos"))

    # ---- top-up: guarantee >= MIN_CASES when the corpus allows it ---------
    extra = 0
    pool = _pick_chunks(chunks, 40, rng)
    while len(cases) < MIN_CASES and extra < 40 and pool:
        c = pool[extra % len(pool)]
        extra += 1
        stats["generated"] += 1
        avoid = "; ".join(x.question for x in cases if c.id in x.generated_from) or "(none)"
        qa = json_call(render("evalgen_answerable", audience=req.audience, doc=c.doc, excerpt=c.text, avoid=avoid),
                       QA, fallback=lambda: QA(question=""), stage="evalgen", temperature=0.8, seed=500 + extra)
        facts = _validate_facts(qa.key_facts, c.text, qa.question) if len(qa.question) >= 8 else []
        if not facts or _dup(qa.question, cases):
            stats["dropped_duplicate" if facts else "dropped_unverifiable_facts"] += 1
            continue
        confirmed = _roundtrip_ok(qa.question, facts, [c], stats)
        if not confirmed:
            stats["dropped_roundtrip_failed"] += 1
            continue
        cases.insert(sum(x.type == "answerable" for x in cases),
                     EvalCase(id="", type="answerable", question=_clean_q(qa.question), should_refuse=False,
                              expected_sources=[c.doc], key_facts=confirmed, generated_from=[c.id],
                              prompt="evalgen_answerable"))

    # ---- ids + dev/holdout split (every 3rd case per type is held out) -----
    counters: dict[str, int] = {}
    for i, c in enumerate(cases, 1):
        c.id = f"E{i:02d}"
        n = counters.get(c.type, 0)
        counters[c.type] = n + 1
        c.split = "holdout" if n % 3 == 2 else "dev"
    stats["final"] = len(cases)
    stats["by_type"] = counters
    log(f"  evals: {len(cases)} cases {counters}; dropped: "
        f"{ {k: v for k, v in stats.items() if k.startswith('dropped') and v} }")
    if len(cases) < 20:
        log("  WARNING: fewer than 20 validated eval cases - documents may be too small; reported as-is.")
    return cases, stats
