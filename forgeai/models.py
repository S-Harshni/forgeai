"""Typed artifacts the factory produces. Everything on disk is one of these, as JSON."""
from __future__ import annotations

from typing import ClassVar, Literal, Optional

from pydantic import BaseModel, Field, field_validator


def _strlist(v):
    if v is None:
        return []
    if isinstance(v, str):
        return [s.strip() for s in v.replace(";", ",").split(",") if s.strip()]
    return [str(x).strip() for x in v if str(x).strip()]


class TopicVerdict(BaseModel):
    topic: str
    status: Literal["supported", "partial", "unsupported"]
    relevance: float
    evidence: str = ""
    evidence_source: str = ""
    reason: str = ""


class Requirements(BaseModel):
    agent_name: str = "Assistant"
    scope: str
    audience: str
    tone: str = "professional, concise"
    must_answer_topics: list[str] = Field(default_factory=list)
    must_refuse_topics: list[str] = Field(default_factory=list)
    clarifications: list[dict] = Field(default_factory=list)  # {question, answer, source: user|auto}
    assumptions: list[str] = Field(default_factory=list)
    groundability: list[TopicVerdict] = Field(default_factory=list)
    unsupported_topics: list[str] = Field(default_factory=list)
    approved_by: str = "auto"

    @field_validator("must_answer_topics", "must_refuse_topics", "assumptions", mode="before")
    @classmethod
    def _norm_lists(cls, v):
        return _strlist(v)


class RetrievalConfig(BaseModel):
    chunk_size: int = 180
    overlap: int = 40
    top_k: int = 4
    min_relevance: float = 0.25   # abstain gate: below this, refuse without calling the LLM
    mode: Literal["hybrid", "bm25"] = "hybrid"
    query_rewrite: bool = False   # LLM keyword expansion before retrieval


class OutputContract(BaseModel):
    format: str = "JSON object: {\"answer\": str, \"citations\": [chunk ids], \"refused\": bool}"
    citations_required: bool = True
    max_words: int = 150
    refusal_message: str = "I'm sorry, I can't answer that from the documents I have access to."


class AgentDefinition(BaseModel):
    name: str
    description: str
    docs_dir: str
    prompt_sections: dict[str, str]  # role, scope, grounding, refusal, format, tone
    retrieval: RetrievalConfig = Field(default_factory=RetrievalConfig)
    model: str = ""  # Ollama model the agent runs on; empty = factory default
    tools: list[str] = Field(default_factory=list)
    tool_rationale: dict[str, str] = Field(default_factory=dict)
    output_contract: OutputContract = Field(default_factory=OutputContract)
    version: int = 0
    changelog: list[str] = Field(default_factory=list)

    SECTION_ORDER: ClassVar[tuple] = ("role", "scope", "grounding", "refusal", "tone", "format")

    def system_prompt(self) -> str:
        parts = []
        for key in self.SECTION_ORDER:
            if self.prompt_sections.get(key):
                parts.append(f"## {key.upper()}\n{self.prompt_sections[key].strip()}")
        for key, val in self.prompt_sections.items():
            if key not in self.SECTION_ORDER and val:
                parts.append(f"## {key.upper()}\n{val.strip()}")
        return "\n\n".join(parts)


CaseType = Literal["answerable", "multi_doc", "ambiguous", "out_of_scope"]


class EvalCase(BaseModel):
    id: str
    type: CaseType
    question: str
    should_refuse: bool
    expected_sources: list[str] = Field(default_factory=list)
    key_facts: list[str] = Field(default_factory=list)
    generated_from: list[str] = Field(default_factory=list)  # chunk ids
    split: Literal["dev", "holdout"] = "dev"
    prompt: str = ""  # which generation prompt produced it
