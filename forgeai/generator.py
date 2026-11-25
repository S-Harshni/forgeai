"""Stage 3: agent generator - requirements + documents -> declarative AgentDefinition."""
from __future__ import annotations

import re
from pathlib import Path

from pydantic import BaseModel, Field

from .ingest import Document, inventory
from .llm import json_call, render
from .models import AgentDefinition, OutputContract, Requirements, RetrievalConfig
from .textutil import tokens
from .tools import REGISTRY, tool_evidence


class GenOut(BaseModel):
    role: str = ""
    scope: str = ""
    tools: list[str] = Field(default_factory=list)
    tool_reasons: dict[str, str] = Field(default_factory=dict)


GROUNDING = """- Answer ONLY from the CONTEXT excerpts supplied with each question (and tool results, if any).
- Never use outside knowledge, never guess numbers, dates, names or amounts.
- Cite the id of every excerpt you used in "citations".
- If the excerpts only partly answer the question, answer that part and say what is missing."""

REFUSAL = """- If the question is out of scope, or the CONTEXT does not contain the answer, set "refused": true and reply with the refusal message.
- Do not refuse questions that the CONTEXT does answer, even if they are phrased informally."""

FORMAT = """- Reply with a single JSON object: {"answer": str, "citations": [excerpt ids], "refused": bool}.
- Keep "answer" under {max_words} words. Plain sentences; include exact figures from the excerpts."""


def choose_retrieval(docs: list[Document]) -> RetrievalConfig:
    """Deterministic heuristics from corpus shape - an LLM adds nothing but noise here."""
    text_docs = [d for d in docs if d.kind == "text"]
    words = sum(len(d.text.split()) for d in text_docs)
    has_table = any(d.kind == "table" for d in docs)
    avg = words / max(1, len(text_docs))
    chunk = 120 if avg < 1200 else 180 if avg < 6000 else 240
    return RetrievalConfig(chunk_size=chunk, overlap=max(20, chunk // 5),
                           top_k=6 if has_table else 4, min_relevance=0.2, mode="hybrid")


def select_tools(proposed: list[str], reasons: dict, req: Requirements, brief: str,
                 docs: list[Document]) -> tuple[list[str], dict[str, str]]:
    """LLM proposal ∩ evidence gate. Every decision, including rejections, is recorded."""
    # Sentences of the brief that only ask for flagged (unsupported) topics are not evidence:
    # a tool must not be attached to serve a capability the agent is going to refuse.
    sentences = re.split(r"(?<=[.!?])\s+", brief)
    gaps = [set(tokens(t)) for t in req.unsupported_topics]
    kept = [s for s in sentences
            if not any(g and len(g & set(tokens(s))) / len(g) >= 0.5 for g in gaps)]
    evidence_text = " ".join([" ".join(kept), req.scope, " ".join(req.must_answer_topics)])
    chosen, rationale = [], {}
    proposed = [p for p in proposed if p in REGISTRY]
    for name in REGISTRY:
        ev = tool_evidence(name, evidence_text, docs)
        if name in proposed and ev:
            chosen.append(name)
            rationale[name] = f"ATTACHED - model: {reasons.get(name, 'n/a')}; evidence: {ev}"
        elif name in proposed:
            rationale[name] = "REJECTED - model proposed it but the brief gives no evidence it is needed"
        elif ev and ev.count(",") >= 1:
            # Two or more distinct trigger phrases in the brief is strong evidence even if the
            # model forgot to request the tool (e.g. "order placed today" + "dispatch date").
            chosen.append(name)
            rationale[name] = f"ATTACHED by rule - strong evidence ({ev}); model omitted it"
        elif ev and REGISTRY[name].needs_table:
            # Tabular data cannot be reliably reached through text retrieval alone.
            chosen.append(name)
            rationale[name] = f"ATTACHED by rule - {ev} (model omitted it)"
        elif ev:
            rationale[name] = f"not attached - weak evidence ({ev}) and model did not request it"
        else:
            rationale[name] = "not attached - no evidence in brief"
    return chosen, rationale


def _slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", s.lower()).strip("_")[:40] or "agent"


def generate_agent(req: Requirements, brief: str, docs: list[Document], docs_dir: str,
                   log=print) -> AgentDefinition:
    tools_desc = "\n".join(f"- {t.name}: {t.description}" for t in REGISTRY.values())
    out = json_call(render("generator_agent", requirements=req.model_dump_json(
                        include={"agent_name", "scope", "audience", "tone", "must_answer_topics",
                                 "must_refuse_topics", "unsupported_topics"}, indent=1),
                           inventory=inventory(docs, 300), tools=tools_desc),
                    GenOut, fallback=lambda: GenOut(), stage="generator")
    role = out.role.strip() or (f"You are {req.agent_name}, an assistant for {req.audience}. "
                                f"You help with: {req.scope}")
    scope = out.scope.strip() if isinstance(out.scope, str) else ""
    # The must-answer/refuse lists are authoritative; append them verbatim so the
    # model-written scope cannot silently drop a requirement.
    scope += ("\n\nMust answer:\n" + "\n".join(f"- {t}" for t in req.must_answer_topics) +
              "\n\nMust decline:\n" + "\n".join(f"- {t}" for t in req.must_refuse_topics))
    tools, rationale = select_tools(out.tools, out.tool_reasons, req, brief, docs)
    contract = OutputContract()
    sections = {
        "role": role,
        "scope": scope.strip(),
        "grounding": GROUNDING,
        "refusal": REFUSAL + f'\n- Refusal message: "{contract.refusal_message}"',
        "tone": f"Audience: {req.audience}. Tone: {req.tone}.",
        "format": FORMAT.replace("{max_words}", str(contract.max_words)),
    }
    if tools:
        sections["tools"] = ("Tool results appear in the CONTEXT as [tool:<name>] entries; you may use and cite them "
                             "like excerpts (cite as tool:<name>).")
    agent = AgentDefinition(
        name=req.agent_name or "Assistant",
        description=req.scope,
        docs_dir=str(Path(docs_dir).resolve()),
        prompt_sections=sections,
        retrieval=choose_retrieval(docs),
        tools=tools,
        tool_rationale=rationale,
        output_contract=contract,
        changelog=["v0: generated from requirements"],
    )
    log(f"  agent '{agent.name}': tools={tools or 'none'}; retrieval={agent.retrieval.model_dump()}")
    return agent
