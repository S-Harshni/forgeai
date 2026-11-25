"""Tool registry the factory chooses from. Each tool declares the evidence a brief must
contain before it may be attached - the LLM's proposal is intersected with this gate, so a
hallucinated "the HR bot might like the weather" never ships."""
from __future__ import annotations

import ast
import datetime as dt
import operator as op
import re
from dataclasses import dataclass
from typing import Callable

import requests

from .ingest import Document
from .textutil import tokens


@dataclass
class Tool:
    name: str
    description: str
    args_schema: dict
    triggers: list[str]          # brief/requirements keywords that justify the tool
    needs_table: bool = False    # requires a CSV in the document folder
    fn: Callable | None = None


# ---------------------------------------------------------------- calculator
_OPS = {ast.Add: op.add, ast.Sub: op.sub, ast.Mult: op.mul, ast.Div: op.truediv,
        ast.Pow: op.pow, ast.USub: op.neg, ast.Mod: op.mod, ast.FloorDiv: op.floordiv}


def _eval(node):
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
        return node.value
    if isinstance(node, ast.BinOp) and type(node.op) in _OPS:
        if isinstance(node.op, ast.Pow) and abs(_eval(node.right)) > 10:
            raise ValueError("exponent too large")
        return _OPS[type(node.op)](_eval(node.left), _eval(node.right))
    if isinstance(node, ast.UnaryOp) and type(node.op) in _OPS:
        return _OPS[type(node.op)](_eval(node.operand))
    raise ValueError("unsupported expression")


def calculator(expression: str = "", **_) -> str:
    expr = str(expression).replace(",", "").replace("x", "*").replace("×", "*").replace("$", "")
    expr = re.sub(r"[^0-9+\-*/().% ]", "", expr)
    try:
        val = _eval(ast.parse(expr, mode="eval").body)
        return f"{expression} = {round(val, 4)}"
    except Exception as e:
        return f"calculator error: {e}"


# ---------------------------------------------------------------- csv lookup
_TABLES: list[Document] = []


def bind_tables(docs: list[Document]):
    _TABLES.clear()
    _TABLES.extend(d for d in docs if d.kind == "table")


def csv_lookup(query: str = "", max_rows: int = 5, **_) -> str:
    q = set(tokens(str(query)))
    raw_q = str(query).lower()
    scored = []
    for t in _TABLES:
        for row in t.rows:
            text = " ".join(row.values())
            rt = set(tokens(text))
            exact = sum(1 for v in row.values() if v and len(v) > 2 and v.lower() in raw_q)
            s = len(q & rt) + 3 * exact
            if s:
                scored.append((s, t.name, row))
    scored.sort(key=lambda x: -x[0])
    if not scored:
        return "csv_lookup: no matching rows"
    return "\n".join(f"[{name}] " + "; ".join(f"{k}: {v}" for k, v in row.items())
                     for _, name, row in scored[:max_rows])


# ---------------------------------------------------------------- date / weather
def current_date(add_days=None, **_) -> str:
    today = dt.date.today()
    out = f"Today is {today.isoformat()} ({today.strftime('%A')})."
    try:
        if add_days not in (None, "", 0):
            n = int(float(str(add_days).split()[0]))
            d = today + dt.timedelta(days=n)
            out += f" {n} days from today is {d.isoformat()} ({d.strftime('%A')})."
    except (ValueError, IndexError):
        pass
    return out


def weather(location: str = "", **_) -> str:
    try:
        g = requests.get("https://geocoding-api.open-meteo.com/v1/search",
                         params={"name": location, "count": 1}, timeout=10).json()
        if not g.get("results"):
            return f"weather: unknown location {location!r}"
        p = g["results"][0]
        w = requests.get("https://api.open-meteo.com/v1/forecast",
                         params={"latitude": p["latitude"], "longitude": p["longitude"],
                                 "current": "temperature_2m,wind_speed_10m,weather_code"},
                         timeout=10).json()["current"]
        return (f"Weather in {p['name']}, {p.get('country', '')}: {w['temperature_2m']}°C, "
                f"wind {w['wind_speed_10m']} km/h (code {w['weather_code']}).")
    except Exception as e:
        return f"weather: service unavailable ({e})"


REGISTRY: dict[str, Tool] = {
    "csv_lookup": Tool("csv_lookup", "Look up rows in the tabular (CSV) documents by keywords, codes or names.",
                       {"query": "str"},
                       ["catalogue", "catalog", "inventory", "stock", "part", "parts", "sku", "price", "list",
                        "table", "lookup", "look up", "spares", "item", "product", "record"],
                       needs_table=True, fn=csv_lookup),
    "calculator": Tool("calculator", "Evaluate an arithmetic expression built ONLY from numbers in the question, excerpts or earlier tool results, e.g. '<quantity> * <unit price> + <fee>'.",
                       {"expression": "str"},
                       ["calculate", "calculation", "total", "cost", "sum", "compute", "percentage", "percent",
                        "how much", "quantity", "budget", "price", "prices", "amount", "multiply", "estimate",
                        "pro-rata", "prorate", "accrual"],
                       fn=calculator),
    "current_date": Tool("current_date", "Return today's date; with add_days=N also the date N days from today "
                         "(use it for lead times / deadlines).", {"add_days": "int (optional)"},
                         ["today", "date", "dates", "deadline", "deadlines", "days until", "delivery date",
                          "expiry", "expire", "schedule", "lead time", "when will", "arrive", "eta", "due"],
                         fn=current_date),
    "weather": Tool("weather", "Current weather for a named location (open-meteo, no key).",
                    {"location": "str"},
                    ["weather", "forecast", "temperature", "wind", "storm", "rain", "climate", "sea state"],
                    fn=weather),
}


def tool_evidence(name: str, text: str, docs: list[Document]) -> str | None:
    """Return the justification for attaching `name`, or None if the brief gives no evidence."""
    tool = REGISTRY[name]
    low = text.lower()
    if tool.needs_table and not any(d.kind == "table" for d in docs):
        return None
    hits = [t for t in tool.triggers if re.search(r"\b" + re.escape(t) + r"\b", low)]
    if not hits:
        return None
    extra = " + CSV present in documents" if tool.needs_table else ""
    return f"brief/requirements mention: {', '.join(hits[:4])}{extra}"


def run_tool(name: str, args: dict) -> str:
    tool = REGISTRY.get(name)
    if not tool or not tool.fn:
        return f"unknown tool {name}"
    try:
        return tool.fn(**(args or {}))
    except TypeError:
        return tool.fn()
