"""Deterministic text helpers shared by retrieval, eval validation and the judge."""
from __future__ import annotations

import re

STOP = set("""a an the and or of to in on for with by at from is are was were be been being this that these
those it its as into about over under than then so such can could should would may might will shall do does did
done has have had having not no nor any all each every some what which who whom whose when where why how i you he
she we they them their our your my me us his her if but also there here please tell explain give get much many
more most less least very just per via etc""".split())

NUM_RE = re.compile(r"\d+(?:[.,]\d+)*")


def norm(text: str) -> str:
    text = text.lower().replace("’", "'")
    text = re.sub(r"[^a-z0-9. ]+", " ", text)
    text = re.sub(r"(?<!\d)\.|\.(?!\d)", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def stem(w: str) -> str:
    """Tiny, consistent suffix stripper (singular/plural and verb forms map to the same stem)."""
    if len(w) <= 3 or w.isdigit():
        return w
    if w.endswith("ies") and len(w) > 4:
        return w[:-3] + "y"
    if w.endswith(("sses", "xes", "zes", "ches", "shes")):
        return w[:-2]
    if w.endswith("s") and not w.endswith(("ss", "us", "is")):
        w = w[:-1]
    for suf in ("ing", "ed"):
        if w.endswith(suf) and len(w) - len(suf) >= 3:
            w = w[: -len(suf)]
            break
    if w.endswith("e") and len(w) > 4:
        w = w[:-1]
    return w


def in_vocab(t: str, vocab: set[str]) -> bool:
    """True if t, or a word sharing its root (lockout ~ lock, allowance ~ allowanc), occurs in vocab."""
    if t in vocab:
        return True
    for v in vocab:
        if (len(v) >= 4 and t.startswith(v)) or (len(t) >= 5 and v.startswith(t)):
            return True
        if len(t) >= 6 and len(v) >= 6 and t[:6] == v[:6]:
            return True
    return False


def tokens(text: str) -> list[str]:
    return [stem(w) for w in norm(text).split() if w not in STOP and len(w) > 1 or w.isdigit()]


def numbers(text: str) -> set[str]:
    out = set()
    for n in NUM_RE.findall(text):
        n = n.replace(",", "")
        n = n.rstrip(".")
        try:
            f = float(n)
            out.add(str(int(f)) if f == int(f) else str(f))
        except ValueError:
            pass
    return out


def fact_present(fact: str, text: str, threshold: float = 0.75) -> float:
    """Score in [0,1] for whether `fact` is stated in `text`.

    Numbers in the fact must appear exactly (a wrong number is a wrong fact);
    content words are matched after stemming with a recall threshold.
    """
    fnums = numbers(fact)
    if fnums and not fnums <= numbers(text):
        return 0.0
    ft = set(tokens(fact)) - set(numbers(fact))
    if not ft:
        return 1.0 if fnums else 0.0
    tt = set(tokens(text))
    recall = len(ft & tt) / len(ft)
    return recall


def jaccard(a: str, b: str) -> float:
    sa, sb = set(tokens(a)), set(tokens(b))
    if not sa or not sb:
        return 0.0
    return len(sa & sb) / len(sa | sb)


REFUSAL_PATTERNS = [
    r"\bi (?:do not|don't|cannot|can't|am unable to|am not able to) (?:have|find|answer|help|provide|assist)",
    r"\bnot (?:covered|included|mentioned|available|addressed|specified|found) in (?:the|my|our) (?:documents?|policies|policy|sources?|context|information|knowledge)",
    r"\boutside (?:the |my )?(?:scope|remit)",
    r"\bno information (?:about|on|regarding)",
    r"\bplease (?:contact|consult|reach out to)\b.*\b(?:hr|team|department|manager)",
    r"\bi'?m (?:sorry|afraid)\b.*\b(?:can(?:no|')t|unable|not able)",
    r"\bbeyond (?:the scope|what)",
    r"\bthe (?:documents?|context|sources?) (?:do(?:es)? not|don't|doesn't) (?:contain|cover|mention|say|specify|provide)",
]


def looks_like_refusal(text: str) -> bool:
    t = text.lower()
    return any(re.search(p, t) for p in REFUSAL_PATTERNS)
