"""Thin LLM client (local Ollama, or any OpenAI-compatible API such as Groq) with schema-validated JSON calls, retries and budget accounting.

Small local models frequently emit broken or off-schema JSON. Every structured call
here is validated with Pydantic, retried with the validation error fed back, and
finally falls back to a caller-supplied default so one bad generation never crashes
an unattended run.
"""
from __future__ import annotations

import json
import os
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional, Type, TypeVar

import requests
from pydantic import BaseModel, ValidationError

OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://localhost:11434")
# PROVIDER: "ollama" (local, default) or "openai" (any OpenAI-compatible API: Groq, Together, vLLM...).
# FORGE_PROVIDER=groq is shorthand for openai + Groq's endpoint.
PROVIDER = os.environ.get("FORGE_PROVIDER", "ollama").lower()
OPENAI_BASE_URL = os.environ.get("OPENAI_BASE_URL", "https://api.groq.com/openai/v1" if PROVIDER == "groq" else "")
OPENAI_API_KEY = os.environ.get("GROQ_API_KEY") or os.environ.get("OPENAI_API_KEY", "")
if PROVIDER == "groq":
    PROVIDER = "openai"
_DEFAULTS = {"ollama": ("qwen2.5:3b", "llama3.2:3b"),
             # different model families for generator and judge (self-preference defence)
             "openai": ("qwen/qwen3.8-27b", "openai/gpt-oss-20b")}[PROVIDER if PROVIDER in ("ollama", "openai") else "ollama"]
GEN_MODEL = os.environ.get("FORGE_GEN_MODEL", _DEFAULTS[0])
JUDGE_MODEL = os.environ.get("FORGE_JUDGE_MODEL", _DEFAULTS[1])
# The model the *produced agent* runs on (recorded in its definition).
AGENT_MODEL = os.environ.get("FORGE_AGENT_MODEL", GEN_MODEL)

PROMPT_DIR = Path(__file__).resolve().parent.parent / "prompts"

T = TypeVar("T", bound=BaseModel)


class BudgetExceeded(RuntimeError):
    pass


@dataclass
class Usage:
    calls: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    seconds: float = 0.0
    failures: int = 0
    stalls: int = 0
    by_stage: dict = field(default_factory=dict)

    def add(self, stage: str, p: int, c: int, s: float):
        self.calls += 1
        self.prompt_tokens += p
        self.completion_tokens += c
        self.seconds += s
        st = self.by_stage.setdefault(stage, {"calls": 0, "tokens": 0, "seconds": 0.0})
        st["calls"] += 1
        st["tokens"] += p + c
        st["seconds"] = round(st["seconds"] + s, 2)

    def to_dict(self):
        return {
            "calls": self.calls,
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "total_tokens": self.prompt_tokens + self.completion_tokens,
            "seconds": round(self.seconds, 1),
            "json_failures_recovered": self.failures,
            "server_stalls_retried": self.stalls,
            "by_stage": self.by_stage,
        }


USAGE = Usage()
# Optional hard budget (tokens). 0 = unlimited.
REQUEST_TIMEOUT = float(os.environ.get("FORGE_REQUEST_TIMEOUT", "90"))
TOKEN_BUDGET = int(os.environ.get("FORGE_TOKEN_BUDGET", "0"))


def load_prompt(name: str) -> str:
    return (PROMPT_DIR / f"{name}.txt").read_text()


def render(name: str, **kw) -> str:
    text = load_prompt(name)
    for k, v in kw.items():
        text = text.replace("{{" + k + "}}", str(v))
    return text


def available_models() -> list[str]:
    try:
        if PROVIDER == "openai":
            r = requests.get(f"{OPENAI_BASE_URL}/models", timeout=10,
                             headers={"Authorization": f"Bearer {OPENAI_API_KEY}"})
            return [m["id"] for m in r.json().get("data", [])]
        r = requests.get(f"{OLLAMA_URL}/api/tags", timeout=5)
        return [m["name"] for m in r.json().get("models", [])]
    except Exception:
        return []


def _chat_openai(messages, model, json_mode, temperature, max_tokens, seed, stage, t0) -> str:
    body: dict[str, Any] = {"model": model, "messages": messages, "temperature": temperature,
                            "max_tokens": max_tokens, "seed": seed}
    if json_mode:
        body["response_format"] = {"type": "json_object"}
        if not any("json" in m["content"].lower() for m in messages):  # API requires the word
            messages[-1]["content"] += "\n\nReply in JSON."
    last_err = None
    for attempt in range(6):
        try:
            r = requests.post(f"{OPENAI_BASE_URL}/chat/completions", json=body, timeout=(10, REQUEST_TIMEOUT),
                              headers={"Authorization": f"Bearer {OPENAI_API_KEY}"})
            if r.status_code == 429 or r.status_code >= 500:  # rate limit / transient: honour Retry-After
                wait = float(r.headers.get("retry-after", 0) or 0) or 2.0 * (attempt + 1)
                USAGE.stalls += 1
                time.sleep(min(wait, 30))
                continue
            if r.status_code == 400 and json_mode and "json" in r.text.lower():
                body.pop("response_format", None)  # model rejected JSON mode; rely on prompt + parser
                continue
            r.raise_for_status()
            data = r.json()
            u = data.get("usage", {})
            USAGE.add(stage, u.get("prompt_tokens", 0), u.get("completion_tokens", 0), time.time() - t0)
            content = data["choices"][0]["message"].get("content") or ""
            return re.sub(r"<think>.*?</think>", "", content, flags=re.S).strip()  # reasoning models
        except requests.RequestException as e:
            last_err = e
            USAGE.stalls += 1
            time.sleep(2.0 * (attempt + 1))
    raise RuntimeError(f"LLM API unreachable at {OPENAI_BASE_URL}: {last_err}")


def chat(prompt: str, *, system: str = "", model: str = GEN_MODEL, json_mode: bool = False,
         temperature: float = 0.2, stage: str = "misc", max_tokens: int = 700, seed: int = 7) -> str:
    if TOKEN_BUDGET and USAGE.prompt_tokens + USAGE.completion_tokens > TOKEN_BUDGET:
        raise BudgetExceeded(f"token budget {TOKEN_BUDGET} exhausted")
    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})
    if PROVIDER == "openai":
        return _chat_openai(messages, model, json_mode, temperature, max_tokens, seed, stage, time.time())
    body: dict[str, Any] = {
        "model": model,
        "messages": messages,
        "stream": False,
        "keep_alive": "30m",  # generator and judge alternate; avoid reloading models between calls
        "options": {"temperature": temperature, "num_ctx": 6144, "num_predict": max_tokens, "seed": seed},
    }
    if json_mode:
        body["format"] = "json"
    t0 = time.time()
    last_err = None
    for attempt in range(4):
        try:
            # Short read timeout: a stalled local server should cost one retry, not minutes.
            r = requests.post(f"{OLLAMA_URL}/api/chat", json=body, timeout=(5, REQUEST_TIMEOUT))
            r.raise_for_status()
            data = r.json()
            if time.time() - t0 > REQUEST_TIMEOUT:
                USAGE.stalls += 1
            USAGE.add(stage, data.get("prompt_eval_count", 0), data.get("eval_count", 0), time.time() - t0)
            return data["message"]["content"]
        except requests.RequestException as e:  # transient server hiccup / stall
            last_err = e
            USAGE.stalls += 1
            time.sleep(1.5 * (attempt + 1))
    raise RuntimeError(f"Ollama unreachable at {OLLAMA_URL}: {last_err}")


def _extract_json(text: str) -> Any:
    text = text.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    m = re.search(r"```(?:json)?\s*(.*?)```", text, re.S)
    if m:
        try:
            return json.loads(m.group(1))
        except json.JSONDecodeError:
            pass
    m = re.search(r"(\{.*\}|\[.*\])", text, re.S)
    if m:
        return json.loads(m.group(1))
    raise ValueError("no JSON found")


def json_call(prompt: str, schema: Type[T], *, fallback: Optional[Callable[[], T]] = None,
              system: str = "", model: str = GEN_MODEL, stage: str = "misc",
              temperature: float = 0.2, retries: int = 2, max_tokens: int = 900, seed: int = 7) -> T:
    """Call the model, parse + validate JSON against `schema`, retry with the error, else fallback."""
    p = prompt
    err = ""
    for attempt in range(retries + 1):
        raw = chat(p, system=system, model=model, json_mode=True,
                   temperature=temperature + 0.15 * attempt, stage=stage, max_tokens=max_tokens,
                   seed=seed + attempt)
        try:
            data = _extract_json(raw)
            return schema.model_validate(data)
        except (ValueError, ValidationError, json.JSONDecodeError) as e:
            USAGE.failures += 1
            err = str(e)[:400]
            p = (prompt + "\n\nYour previous reply was invalid: " + err +
                 "\nReply again with ONLY valid JSON matching the requested keys.")
    if fallback is not None:
        return fallback()
    raise ValueError(f"[{stage}] could not get valid JSON: {err}")
