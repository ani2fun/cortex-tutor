"""Lexical retrieval over the corpus — a hand-rolled Okapi BM25 (v1, no numpy).

Keeps the image lean and the ranking explainable; the id-based ``get_problem``/``get_lesson`` path is
the dominant one, with ``search_corpus``/``list_related`` as the fuzzy fallback. A hybrid/embedding
re-ranker can replace ``Bm25Index.search`` later behind the same interface.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass

from grounding_mcp.corpus import ChapterMeta, CorpusIndex

_TOKEN = re.compile(r"[a-z0-9]+")
_K1 = 1.5
_B = 0.75
_TITLE_WEIGHT = 3  # repeat the title in the indexed text so title matches rank higher


def tokenize(text: str) -> list[str]:
    return _TOKEN.findall(text.lower())


@dataclass(frozen=True)
class Hit:
    meta: ChapterMeta
    score: float


class Bm25Index:
    """Okapi BM25 over each chapter's (title-weighted) visible text."""

    def __init__(self, corpus: CorpusIndex) -> None:
        self._metas: list[ChapterMeta] = []
        self._tf: list[dict[str, int]] = []
        self._doc_len: list[int] = []
        self._df: dict[str, int] = {}
        for meta in corpus.chapters:
            doc = corpus.doc(meta.problem_id)
            text = ((meta.title + " ") * _TITLE_WEIGHT) + (doc.visible if doc else "")
            tokens = tokenize(text)
            tf: dict[str, int] = {}
            for t in tokens:
                tf[t] = tf.get(t, 0) + 1
            self._metas.append(meta)
            self._tf.append(tf)
            self._doc_len.append(len(tokens))
            for term in tf:
                self._df[term] = self._df.get(term, 0) + 1
        self._n = len(self._metas)
        self._avgdl = (sum(self._doc_len) / self._n) if self._n else 0.0

    def _idf(self, term: str) -> float:
        df = self._df.get(term, 0)
        if df == 0:
            return 0.0
        return math.log(1 + (self._n - df + 0.5) / (df + 0.5))

    def search(self, query: str, *, limit: int, book: str | None = None) -> list[Hit]:
        terms = tokenize(query)
        if not terms or self._n == 0:
            return []
        idf = {t: self._idf(t) for t in set(terms)}
        hits: list[Hit] = []
        for i, meta in enumerate(self._metas):
            if book is not None and meta.book != book:
                continue
            tf, dl = self._tf[i], self._doc_len[i]
            score = 0.0
            for term in terms:
                f = tf.get(term, 0)
                if f == 0:
                    continue
                norm = 1 - _B + _B * (dl / self._avgdl if self._avgdl else 1.0)
                score += idf[term] * (f * (_K1 + 1)) / (f + _K1 * norm)
            if score > 0:
                hits.append(Hit(meta=meta, score=score))
        hits.sort(key=lambda h: (h.score, h.meta.problem_id), reverse=True)
        return hits[:limit]


def snippet(text: str, query: str, *, max_chars: int) -> str:
    """A whitespace-collapsed window around the first query-term hit (or the head if none)."""
    flat = " ".join(text.split())
    if not flat:
        return ""
    low = flat.lower()
    pos = min((low.find(t) for t in tokenize(query) if low.find(t) != -1), default=-1)
    if pos <= 0:
        out = flat[:max_chars]
        return out + ("…" if len(flat) > max_chars else "")
    start = max(0, pos - max_chars // 3)
    out = flat[start : start + max_chars]
    return ("…" if start > 0 else "") + out + ("…" if start + max_chars < len(flat) else "")
