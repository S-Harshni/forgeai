"""Load a document folder (PDF, TXT, MD, CSV, DOCX, JSON) and chunk it."""
from __future__ import annotations

import csv
import io
import json
import re
from dataclasses import dataclass, field
from pathlib import Path

SUPPORTED = {".pdf", ".txt", ".md", ".csv", ".docx", ".json", ".markdown", ".text"}


@dataclass
class Document:
    name: str
    kind: str  # "text" | "table"
    text: str
    rows: list[dict] = field(default_factory=list)  # tables only
    columns: list[str] = field(default_factory=list)


@dataclass
class Chunk:
    id: str
    doc: str
    text: str


def _read_pdf(path: Path) -> str:
    from pypdf import PdfReader
    reader = PdfReader(str(path))
    return "\n".join((p.extract_text() or "") for p in reader.pages)


def _read_docx(path: Path) -> str:
    import docx
    d = docx.Document(str(path))
    return "\n".join(p.text for p in d.paragraphs)


def _unwrap(text: str) -> str:
    """PDF text is hard-wrapped; join soft line breaks but keep headings / list items on their own line."""
    out: list[str] = []
    for line in text.splitlines():
        line = line.rstrip()
        starts_block = (not line.strip() or re.match(r"^\s*(\d+(\.\d+)*[.)]|[-*\u2022])\s", line)
                        or (line.strip().isupper() and len(line.strip()) > 3))
        if out and out[-1].strip() and not starts_block and not out[-1].strip().isupper():
            out[-1] = out[-1] + " " + line.strip()
        else:
            out.append(line)
    return "\n".join(out)


def _clean(text: str) -> str:
    text = text.replace("\x00", " ")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def load_folder(folder: str | Path) -> list[Document]:
    folder = Path(folder)
    if not folder.is_dir():
        raise FileNotFoundError(f"document folder not found: {folder}")
    docs: list[Document] = []
    for path in sorted(folder.rglob("*")):
        if not path.is_file() or path.name.startswith("."):
            continue
        ext = path.suffix.lower()
        if ext not in SUPPORTED:
            print(f"  [ingest] skipping unsupported file {path.name}")
            continue
        name = str(path.relative_to(folder))
        try:
            if ext == ".csv":
                raw = path.read_text(errors="ignore")
                reader = csv.DictReader(io.StringIO(raw))
                rows = [{(k or "").strip(): (v or "").strip() for k, v in r.items()} for r in reader]
                cols = [c.strip() for c in (reader.fieldnames or [])]
                docs.append(Document(name, "table", raw, rows, cols))
                continue
            if ext == ".pdf":
                text = _unwrap(_read_pdf(path))
            elif ext == ".docx":
                text = _read_docx(path)
            elif ext == ".json":
                text = json.dumps(json.loads(path.read_text(errors="ignore")), indent=1)
            else:
                text = path.read_text(errors="ignore")
            text = _clean(text)
            if len(text) < 20:
                print(f"  [ingest] {path.name}: no extractable text (scanned PDF?) - skipped")
                continue
            docs.append(Document(name, "text", text))
        except Exception as e:  # never crash on one bad file
            print(f"  [ingest] failed to read {path.name}: {e}")
    if not docs:
        raise ValueError(f"no readable documents in {folder}")
    return docs


def _sentences(text: str) -> list[str]:
    parts = re.split(r"(?<=[.!?])\s+|\n+", text)
    return [p.strip() for p in parts if p.strip()]


def chunk_documents(docs: list[Document], chunk_size: int = 180, overlap: int = 40) -> list[Chunk]:
    """Sentence-aware word-window chunking for text; one chunk per row for tables."""
    chunks: list[Chunk] = []
    for d in docs:
        if d.kind == "table":
            for i, row in enumerate(d.rows):
                text = "; ".join(f"{k}: {v}" for k, v in row.items() if k)
                chunks.append(Chunk(f"{d.name}#r{i + 1}", d.name, text))
            continue
        sents = _sentences(d.text)
        cur: list[str] = []
        cur_len = 0
        idx = 0
        for s in sents:
            w = len(s.split())
            if cur and cur_len + w > chunk_size:
                chunks.append(Chunk(f"{d.name}#c{idx}", d.name, " ".join(cur)))
                idx += 1
                # carry overlap words (whole sentences from the tail)
                tail, tl = [], 0
                for prev in reversed(cur):
                    if tl >= overlap:
                        break
                    tail.insert(0, prev)
                    tl += len(prev.split())
                cur, cur_len = tail, tl
            cur.append(s)
            cur_len += w
        if cur:
            chunks.append(Chunk(f"{d.name}#c{idx}", d.name, " ".join(cur)))
    return chunks


def inventory(docs: list[Document], preview_chars: int = 500) -> str:
    """Compact description of the folder for intake/generation prompts."""
    lines = []
    for d in docs:
        if d.kind == "table":
            sample = "; ".join(f"{k}={v}" for k, v in (d.rows[0].items() if d.rows else []))
            lines.append(f"- {d.name} (CSV table, {len(d.rows)} rows, columns: {', '.join(d.columns)})\n"
                         f"  sample row: {sample[:300]}")
        else:
            heads = [l.strip("# ").strip() for l in d.text.splitlines()
                     if l.strip().startswith("#") or (l.isupper() and 3 < len(l) < 80)]
            lines.append(f"- {d.name} (text, {len(d.text.split())} words)\n"
                         f"  headings: {'; '.join(heads[:12]) or 'n/a'}\n"
                         f"  opening: {d.text[:preview_chars].replace(chr(10), ' ')}")
    return "\n".join(lines)
