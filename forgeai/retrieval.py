"""Hybrid retrieval: BM25 (always) + Ollama embeddings (when an embedding model is installed).

`relevance` is an absolute, corpus-independent score in [0,1]: the IDF-weighted share of
the query's content terms that occur in a chunk (blended with cosine similarity when
embeddings exist). It is what the abstain gate and the groundability guard threshold on,
because raw BM25 scores are not comparable across corpora.
"""
from __future__ import annotations

import math
import os
from collections import Counter
from dataclasses import dataclass

import numpy as np
import requests

from .ingest import Chunk
from .llm import OLLAMA_URL, available_models
from .textutil import tokens

EMBED_CANDIDATES = ["nomic-embed-text", "mxbai-embed-large", "all-minilm", "bge-m3"]


def pick_embed_model() -> str | None:
    if os.environ.get("FORGE_NO_EMBED"):
        return None
    have = available_models()
    for cand in EMBED_CANDIDATES:
        for m in have:
            if m.split(":")[0] == cand:
                return m
    return None


@dataclass
class Hit:
    chunk: Chunk
    score: float      # fused ranking score
    relevance: float  # absolute [0,1]


class Index:
    def __init__(self, chunks: list[Chunk], mode: str = "hybrid", embed_model: str | None = "auto"):
        self.chunks = chunks
        self.toks = [tokens(c.text) for c in chunks]
        self.tfs = [Counter(t) for t in self.toks]
        self.lens = np.array([len(t) for t in self.toks], dtype=float)
        self.avgdl = float(self.lens.mean()) if len(chunks) else 1.0
        df = Counter()
        for t in self.toks:
            df.update(set(t))
        n = len(chunks)
        self.idf = {w: math.log(1 + (n - d + 0.5) / (d + 0.5)) for w, d in df.items()}
        self.max_idf = math.log(1 + (n + 0.5) / 0.5) if n else 1.0
        self.mode = mode
        self.embed_model = pick_embed_model() if embed_model == "auto" else embed_model
        self.emb = None
        if mode == "hybrid" and self.embed_model:
            try:
                self.emb = self._embed([c.text for c in chunks])
            except Exception as e:
                print(f"  [retrieval] embeddings unavailable ({e}); using BM25 only")
                self.emb = None

    # ---- embeddings -------------------------------------------------------
    def _embed(self, texts: list[str]) -> np.ndarray:
        out = []
        for i in range(0, len(texts), 32):
            r = requests.post(f"{OLLAMA_URL}/api/embed",
                              json={"model": self.embed_model, "input": texts[i:i + 32]}, timeout=120)
            r.raise_for_status()
            out.extend(r.json()["embeddings"])
        a = np.array(out, dtype=float)
        return a / (np.linalg.norm(a, axis=1, keepdims=True) + 1e-9)

    # ---- scoring ----------------------------------------------------------
    def _bm25(self, q: list[str], k1=1.4, b=0.75) -> np.ndarray:
        s = np.zeros(len(self.chunks))
        for i, tf in enumerate(self.tfs):
            for w in q:
                f = tf.get(w)
                if f:
                    s[i] += self.idf.get(w, 0) * f * (k1 + 1) / (f + k1 * (1 - b + b * self.lens[i] / self.avgdl))
        return s

    def _coverage(self, q: list[str]) -> np.ndarray:
        qs = set(q)
        if not qs:
            return np.zeros(len(self.chunks))
        w = {t: self.idf.get(t, self.max_idf) for t in qs}  # unseen terms get max idf
        tot = sum(w.values())
        return np.array([sum(w[t] for t in qs if t in tf) / tot for tf in self.tfs])

    def search(self, query: str, k: int = 4) -> list[Hit]:
        if not self.chunks:
            return []
        q = tokens(query)
        bm = self._bm25(q)
        cov = self._coverage(q)
        rel = cov
        fused = bm / (bm.max() + 1e-9)
        if self.emb is not None:
            try:
                qe = self._embed([query])[0]
                cos = self.emb @ qe
                cos_n = np.clip((cos - 0.35) / 0.45, 0, 1)  # map typical cosine range to [0,1]
                rel = 0.5 * cov + 0.5 * cos_n
                # reciprocal-rank fusion of lexical and dense rankings
                rr = np.zeros(len(self.chunks))
                for arr in (bm, cos):
                    order = np.argsort(-arr)
                    rr[order] += 1.0 / (60 + np.arange(1, len(order) + 1))
                fused = rr
            except Exception:
                pass
        order = np.argsort(-fused)[:k]
        return [Hit(self.chunks[i], float(fused[i]), float(rel[i])) for i in order]
