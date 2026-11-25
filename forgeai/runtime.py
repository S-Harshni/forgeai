"""Generic agent runtime: loads ANY AgentDefinition and answers questions with it.

Used by both the chat runner and the eval harness, so what is tested is exactly what ships.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from pydantic import BaseModel, Field

from .ingest import chunk_documents, load_folder
from .llm import GEN_MODEL, chat, json_call, render, _extract_json
from .models import AgentDefinition
from .retrieval import Index
from .textutil import looks_like_refusal, numbers
from .tools import REGISTRY, bind_tables, run_tool


class Route(BaseModel):
    tool: str | None = None
    args: dict = Field(default_factory=dict)


class Rewrite(BaseModel):
    query: str


@dataclass
class Response:
    answer: str
    citations: list[str]
    refused: bool
    retrieved: list[dict] = field(default_factory=list)  # {id, doc, relevance}
    tool_calls: list[dict] = field(default_factory=list)
    gate: str = ""             # why the abstain gate fired, if it did
    format_ok: bool = True
    context_text: str = ""

    def to_dict(self):
        d = self.__dict__.copy()
        d.pop("context_text")
        return d


class Agent:
    def __init__(self, definition: AgentDefinition, docs=None):
        self.d = definition
        self.docs = docs if docs is not None else load_folder(definition.docs_dir)
        bind_tables(self.docs)
        r = definition.retrieval
        self.index = Index(chunk_documents(self.docs, r.chunk_size, r.overlap), mode=r.mode)

    @classmethod
    def load(cls, path: str | Path) -> "Agent":
        return cls(AgentDefinition.model_validate_json(Path(path).read_text()))

    def _route_tools(self, question: str, context: str = "") -> list[dict]:
        if not self.d.tools:
            return []
        desc = "\n".join(f"- {n}: {REGISTRY[n].description} args={REGISTRY[n].args_schema}" for n in self.d.tools)
        calls: list[dict] = []
        # Deterministic pre-route: date questions always get today's date (small models forget).
        if "current_date" in self.d.tools and re.search(
                r"\b(today|tomorrow|date|deadline|when will|dispatch|arrive|due|by when|how long until)\b", question.lower()):
            calls.append({"tool": "current_date", "args": {}, "result": run_tool("current_date", {})})
        for _ in range(2):  # at most two chained tool calls (e.g. lookup -> calculate)
            prev = "\n".join(f"- {c['tool']}({json.dumps(c['args'])}) -> {c['result']}" for c in calls) or "(none)"
            route = json_call(render("agent_tool_route", tools=desc, question=question, previous=prev,
                                     context=context[:2500] or "(none)"), Route,
                              fallback=lambda: Route(), stage="agent_tools", temperature=0.0, retries=1)
            if not route.tool or route.tool not in self.d.tools:  # never run an unattached tool
                break
            if any(c["tool"] == route.tool and c["args"] == route.args for c in calls):
                break
            if route.tool == "calculator":
                # Grounding guard for arithmetic: every operand must come from somewhere real.
                known = numbers(question) | numbers(context) | numbers(prev)
                invented = numbers(str(route.args.get("expression", ""))) - known
                if invented:
                    calls.append({"tool": "calculator", "args": route.args,
                                  "result": f"REJECTED: operands {sorted(invented)} not found in question/excerpts"})
                    continue
            calls.append({"tool": route.tool, "args": route.args, "result": run_tool(route.tool, route.args)[:1200]})
        return calls

    def ask(self, question: str) -> Response:
        r = self.d.retrieval
        query = question
        if r.query_rewrite:
            rw = json_call(render("agent_query_rewrite", question=question), Rewrite,
                           fallback=lambda: Rewrite(query=""), stage="agent_rewrite", retries=0)
            query = f"{question} {rw.query}"
        hits = self.index.search(query, k=r.top_k)
        retrieved = [{"id": h.chunk.id, "doc": h.chunk.doc, "relevance": round(h.relevance, 3)} for h in hits]
        tool_calls = self._route_tools(question, "\n".join(f"[{h.chunk.id}] {h.chunk.text}" for h in hits))
        best = max((h.relevance for h in hits), default=0.0)
        refusal = self.d.output_contract.refusal_message

        # Abstain gate: nothing relevant retrieved and no tool result -> refuse deterministically.
        useful_tools = [c for c in tool_calls if not any(x in c["result"] for x in ("no matching", "error", "REJECTED"))]
        tool_calls_shown = [c for c in tool_calls if not c["result"].startswith("REJECTED")]
        if best < r.min_relevance and not useful_tools:
            return Response(refusal, [], True, retrieved, tool_calls,
                            gate=f"best relevance {best:.2f} < {r.min_relevance}")

        ctx = [f"[{h.chunk.id}] {h.chunk.text}" for h in hits]
        ctx += [f"[tool:{c['tool']}] {c['result']}" for c in tool_calls_shown]
        context = "\n\n".join(ctx)
        prompt = render("agent_answer", context=context, question=question,
                        max_words=self.d.output_contract.max_words, refusal=refusal,
                        example_id=hits[0].chunk.id if hits else "doc#c0",
                        tool_note=" and tool results" if tool_calls else "")
        raw = chat(prompt, system=self.d.system_prompt(), json_mode=True, stage="agent_answer",
                   temperature=0.1, max_tokens=500)
        format_ok = True
        try:
            data = _extract_json(raw)
            answer = str(data.get("answer", "")).strip()
            cites = [str(c).strip("[] ") for c in (data.get("citations") or []) if str(c).strip()]
            refused = bool(data.get("refused", False))
            if not isinstance(data.get("citations", []), list):
                format_ok = False
        except Exception:
            answer, cites, refused, format_ok = raw.strip(), [], False, False
        if not answer:
            format_ok = False
        refused = refused or looks_like_refusal(answer)
        valid_ids = {h.chunk.id for h in hits} | {f"tool:{c['tool']}" for c in tool_calls}
        # keep only citations that point at something actually shown to the model
        cites = [c for c in cites if c in valid_ids]
        return Response(answer, cites, refused, retrieved, tool_calls, format_ok=format_ok, context_text=context)
