"""Stage 1: intake interviewer + Stage 2: groundability guard + optional human checkpoint."""
from __future__ import annotations

import json
import re
import os
import subprocess
from pathlib import Path

from pydantic import BaseModel, Field

from .ingest import Document, chunk_documents, inventory
from .llm import JUDGE_MODEL, json_call, render
from .models import Requirements, TopicVerdict, _strlist
from .retrieval import Index
from .textutil import fact_present, in_vocab, jaccard, stem, tokens

MAX_QUESTIONS = 5


class Draft(BaseModel):
    agent_name: str = "Assistant"
    scope: str = ""
    audience: str = ""
    tone: str = "professional, concise"
    must_answer_topics: list[str] = Field(default_factory=list)
    must_refuse_topics: list[str] = Field(default_factory=list)
    requested_topics: list[str] = Field(default_factory=list)
    questions: list[str] = Field(default_factory=list)


class AutoAnswer(BaseModel):
    answer: str
    is_assumption: bool = True


class GuardCheck(BaseModel):
    covered: str = "no"
    evidence: str = ""
    reason: str = ""


DEFAULT_REFUSE = ["legal or medical advice", "personal opinions or speculation",
                  "questions unrelated to the documents"]


def _fallback_draft(brief: str, docs: list[Document]) -> Draft:
    topics = []
    for d in docs:
        if d.kind == "table":
            topics.append(f"{Path(d.name).stem.replace('_', ' ')} lookups")
        else:
            topics.append(Path(d.name).stem.replace("_", " ").replace("-", " "))
    return Draft(agent_name="Assistant", scope=brief.strip().split(".")[0],
                 audience="staff named in the brief", must_answer_topics=topics[:8],
                 must_refuse_topics=DEFAULT_REFUSE, questions=[])


MODEL_NAMES = ("qwen", "llama", "gemma", "mistral", "gpt", "claude", "phi")


def _agent_name(name: str) -> str:
    name = (name or "").strip(" .\"'")
    if not name or any(m in name.lower() for m in MODEL_NAMES) or len(name) > 60:
        return "Assistant"  # small models sometimes name the agent after themselves
    return name


def interview(brief: str, docs: list[Document], interactive: bool, log=print) -> Requirements:
    inv = inventory(docs)
    draft = json_call(render("intake_draft", brief=brief, inventory=inv), Draft,
                      fallback=lambda: _fallback_draft(brief, docs), stage="intake")
    questions = [q for q in _strlist(draft.questions) if len(q) > 8][:MAX_QUESTIONS]
    qa, assumptions = [], []
    log(f"  intake: {len(questions)} clarifying question(s)")
    for i, q in enumerate(questions, 1):
        ans, src = "", "auto"
        if interactive:
            try:
                ans = input(f"\n  Q{i}/{len(questions)}: {q}\n  > (Enter = let ForgeAI decide) ").strip()
                src = "user" if ans else "auto"
            except EOFError:
                interactive = False
        if not ans:
            a = json_call(render("intake_autoanswer", brief=brief, inventory=inv, question=q), AutoAnswer,
                          fallback=lambda: AutoAnswer(answer="No answer available; use conservative default.",
                                                      is_assumption=True), stage="intake")
            ans = a.answer.strip()
            if len(ans.strip(" .")) < 3:  # small models sometimes reply "..."
                ans, a.is_assumption = "No preference stated in the brief; using the conservative default.", True
            if a.is_assumption:
                assumptions.append(f"{q} -> {ans}")
        qa.append({"question": q, "answer": ans, "source": src})
        log(f"    Q{i}: {q}\n        A ({src}): {ans}")

    draft_json = draft.model_dump(exclude={"questions", "requested_topics"})
    qa_text = "\n".join(f"- {x['question']} -> {x['answer']}" for x in qa) or "(none)"
    final = json_call(render("intake_final", brief=brief, inventory=inv,
                             draft=json.dumps(draft_json, indent=1), qa=qa_text),
                      Draft, fallback=lambda: draft, stage="intake")
    refuse = _strlist(final.must_refuse_topics) or DEFAULT_REFUSE
    if not any("unrelated" in r.lower() or "off-topic" in r.lower() for r in refuse):
        refuse.append("questions unrelated to the documents")
    must = _strlist(final.must_answer_topics) or _strlist(draft.must_answer_topics)
    # Topics the brief explicitly requested must survive to the guard even if the model
    # "helpfully" dropped them because the documents lack them - that gap is what the guard is for.
    for t in _strlist(draft.requested_topics):
        if not any(jaccard(t, m) >= 0.5 for m in must):
            must.append(t)
    return Requirements(
        agent_name=_agent_name(final.agent_name or draft.agent_name),
        scope=final.scope or draft.scope or brief[:200],
        audience=final.audience or draft.audience or "unspecified",
        tone=final.tone or draft.tone,
        must_answer_topics=must[:10],
        must_refuse_topics=refuse[:7],
        clarifications=qa,
        assumptions=assumptions,
    )


# --------------------------------------------------------------------------- guard
def _verify_quote(quote: str, excerpts: str) -> bool:
    """Don't trust the guard model's 'yes' unless its evidence quote really is in the excerpts."""
    if len(quote.split()) < 4:
        return False
    return fact_present(quote, excerpts) >= 0.85


ACTION_WORDS = {stem(w) for w in """look lookup up compare comparison calculate calculation compute estimate show tell
find check list explain answer provide handle question questions query queries info information detail details
general help support guidance policy policies process procedure assistant manage management""".split()}


def groundability_guard(req: Requirements, docs: list[Document], log=print) -> Requirements:
    idx = Index(chunk_documents(docs, 160, 30))
    vocab = set(idx.idf)
    verdicts: list[TopicVerdict] = []
    for topic in req.must_answer_topics:
        raw_words = [w for w in re.findall(r"[A-Za-z][A-Za-z\-]+", topic)]
        missing = [w.lower() for w in raw_words
                   if (st := tokens(w)) and st[0] not in ACTION_WORDS and not in_vocab(st[0], vocab)]
        hits = idx.search(topic, k=4)
        best = max((h.relevance for h in hits), default=0.0)
        excerpts = "\n\n".join(f"[{h.chunk.id}] {h.chunk.text[:700]}" for h in hits)
        chk = json_call(render("guard_check", topic=topic, excerpts=excerpts), GuardCheck,
                        fallback=lambda: GuardCheck(covered="unknown"), stage="guard", temperature=0.0,
                        model=JUDGE_MODEL)  # measured: llama3.2:3b is the more reliable coverage checker
        # The quote must exist in the excerpts AND actually be about the topic.
        verified = _verify_quote(chk.evidence, excerpts) and bool(set(tokens(topic)) & set(tokens(chk.evidence)))
        cov = chk.covered.lower().strip()
        # Combine the lexical signal with the (quote-verified) LLM signal.
        # Strongest gap signal: the corpus never uses a content word of the topic at all
        # (e.g. "rating", "tax"). Small judges happily "support" such topics with an off-topic quote.
        if missing and not verified:
            status = "unsupported"
            chk.reason = f"documents never mention: {', '.join(missing)}. " + chk.reason
        elif best < 0.2 and cov != "yes":
            status = "unsupported"
        elif cov == "yes" and (verified or best >= 0.5):
            status = "supported"
        elif cov == "no" and best < 0.25:
            # A bare "no" from a 3B judge is not enough on its own when the corpus does use the
            # topic's words and retrieval finds moderately relevant text: call it partial instead.
            status = "unsupported"
        elif best >= 0.6:
            status = "supported" if verified or cov in ("yes", "partial", "unknown") else "partial"
        else:
            status = "partial"
        src = next((h.chunk.doc for h in hits if chk.evidence and chk.evidence[:40] in h.chunk.text),
                   hits[0].chunk.doc if hits else "")
        reason = chk.reason + ("" if verified or not chk.evidence else
                               " (the checker's supporting quote was not found in the documents, so it was ignored)")
        verdicts.append(TopicVerdict(topic=topic, status=status, relevance=round(best, 3),
                                     evidence=chk.evidence[:300] if verified else "",
                                     evidence_source=src if verified else "", reason=reason.strip()))
        flag = {"supported": "OK  ", "partial": "PART", "unsupported": "GAP!"}[status]
        log(f"    [{flag}] {topic}  (relevance {best:.2f}, llm={cov}, quote_verified={verified})")
    req.groundability = verdicts
    req.unsupported_topics = [v.topic for v in verdicts if v.status == "unsupported"]
    if req.unsupported_topics:
        log(f"  GROUNDABILITY WARNING: documents cannot support: {req.unsupported_topics}. "
            f"They are moved to must-refuse so the agent will not hallucinate them.")
        req.must_answer_topics = [t for t in req.must_answer_topics if t not in req.unsupported_topics]
        for t in req.unsupported_topics:
            req.must_refuse_topics.append(f"{t} (not covered by documents)")
    return req


def human_checkpoint(req: Requirements, path: Path, log=print) -> Requirements:
    path.write_text(req.model_dump_json(indent=2))
    log(f"\n  Requirements written to {path}")
    while True:
        try:
            c = input("  Approve requirements? [y]es / [e]dit in $EDITOR / [q]uit: ").strip().lower() or "y"
        except EOFError:
            return req
        if c == "y":
            req.approved_by = "user"
            return req
        if c == "q":
            raise SystemExit("aborted by requester at requirements checkpoint")
        if c == "e":
            subprocess.call([os.environ.get("EDITOR", "nano"), str(path)])
            try:
                req = Requirements.model_validate_json(path.read_text())
                req.approved_by = "user (edited)"
                log("  reloaded edited requirements")
            except Exception as e:
                log(f"  edited file is invalid ({e}); please fix it")
