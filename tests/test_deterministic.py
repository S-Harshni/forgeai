"""Fast tests for the deterministic safety checks (no LLM needed).

    .venv/bin/python tests/test_deterministic.py      (or: pytest tests/)
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from forgeai.generator import select_tools
from forgeai.ingest import Document, chunk_documents, load_folder
from forgeai.judge import unsupported_numbers
from forgeai.models import Requirements
from forgeai.textutil import fact_present, looks_like_refusal
from forgeai.tools import bind_tables, calculator, csv_lookup

ROOT = Path(__file__).resolve().parent.parent
HR_BRIEF = (ROOT / "demos/hr_policy/brief.txt").read_text()
SHIP_BRIEF = (ROOT / "demos/ship_spares/brief.txt").read_text()


def _req(topics):
    return Requirements(scope="x", audience="y", must_answer_topics=topics)


def test_hr_agent_gets_no_weather_even_if_llm_asks():
    docs = [Document("leave.pdf", "text", "Annual leave is 24 days.")]
    tools, why = select_tools(["weather", "calculator"], {}, _req(["annual leave"]), HR_BRIEF, docs)
    assert "weather" not in tools, why
    assert "REJECTED" in why["weather"]


def test_unsupported_topic_is_not_tool_evidence():
    docs = [Document("leave.pdf", "text", "Annual leave is 24 days.")]
    req = _req(["annual leave"])
    req.unsupported_topics = ["income tax and pension deductions"]
    tools, why = select_tools(["calculator"], {}, req, HR_BRIEF, docs)
    assert "calculator" not in tools, why


def test_ship_agent_gets_lookup_calc_date_not_weather():
    docs = load_folder(ROOT / "demos/ship_spares/docs")
    tools, why = select_tools(["csv_lookup", "calculator", "current_date", "weather"], {},
                              _req(["part lookup"]), SHIP_BRIEF, docs)
    assert set(tools) == {"csv_lookup", "calculator", "current_date"}, why


def test_csv_lookup_attached_by_rule_when_table_present():
    docs = load_folder(ROOT / "demos/ship_spares/docs")
    tools, _ = select_tools([], {}, _req(["catalogue"]), SHIP_BRIEF, docs)
    assert "csv_lookup" in tools


def test_fact_matching_requires_exact_numbers():
    text = "Full-time shore staff receive 24 working days of paid annual leave per calendar year."
    assert fact_present("24 working days of paid annual leave", text) >= 0.75
    assert fact_present("25 working days of paid annual leave", text) == 0.0


def test_unsupported_numbers_detected():
    ctx = "Hotel costs are reimbursed up to 180 USD per night."
    assert unsupported_numbers("You can claim up to 200 USD per night.", ctx, "hotel cap?") == {"200"}
    assert unsupported_numbers("Up to 180 USD per night.", ctx, "hotel cap?") == set()


def test_vocab_matching_for_groundability():
    from forgeai.textutil import in_vocab, tokens
    hr = set(tokens((ROOT / "demos/_src/hr_travel_expenses.txt").read_text()))
    assert not in_vocab("tax", hr)            # "taxis" must not make "tax" look covered
    assert in_vocab(tokens("lockouts")[0], {"lock", "account"})
    assert tokens("allowances") == tokens("allowance")


def test_refusal_detection():
    assert looks_like_refusal("I'm sorry, I can't answer that from the documents I have access to.")
    assert looks_like_refusal("That is not covered in the policy documents.")
    assert not looks_like_refusal("You receive 24 days of annual leave.")


def test_calculator_is_safe():
    assert calculator("3 * 1450 + 350").endswith("4700")
    assert "error" in calculator("__import__('os').system('ls')")


def test_csv_lookup_finds_part():
    bind_tables(load_folder(ROOT / "demos/ship_spares/docs"))
    out = csv_lookup("MS-2004 turbocharger")
    assert "MS-2004" in out.splitlines()[0]


def test_table_rows_become_chunks():
    docs = load_folder(ROOT / "demos/ship_spares/docs")
    ids = [c.id for c in chunk_documents(docs)]
    assert "spares_catalogue.csv#r1" in ids


if __name__ == "__main__":
    fns = [v for k, v in dict(globals()).items() if k.startswith("test_")]
    for f in fns:
        f()
        print(f"ok  {f.__name__}")
    print(f"{len(fns)} passed")
