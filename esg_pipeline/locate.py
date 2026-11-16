"""Stage 3 - Locate: rank pages per field so the extractor only reads what matters.

A 200-page annual report would blow up LLM cost if sent whole for 28 fields.
A small BM25 index (no external dependencies) scores each page against the
field's keywords; tables get a boost because quantitative KPIs live there.
"""
from __future__ import annotations

import math
import re
from collections import Counter

from .schema import Document, Field

TOKEN = re.compile(r"[a-z0-9]+")


def tokenize(text: str) -> list[str]:
    return TOKEN.findall(text.lower())


class PageIndex:
    def __init__(self, doc: Document, k1: float = 1.5, b: float = 0.75):
        self.doc = doc
        self.k1, self.b = k1, b
        self.tfs = [Counter(tokenize(p.text)) for p in doc.pages]
        self.lens = [sum(tf.values()) for tf in self.tfs]
        self.avg_len = (sum(self.lens) / len(self.lens)) if self.lens else 1.0
        df: Counter = Counter()
        for tf in self.tfs:
            df.update(tf.keys())
        n = len(self.tfs)
        self.idf = {t: math.log(1 + (n - d + 0.5) / (d + 0.5)) for t, d in df.items()}

    def _bm25(self, i: int, terms: list[str]) -> float:
        tf, length, score = self.tfs[i], self.lens[i], 0.0
        for t in terms:
            if t not in tf:
                continue
            f = tf[t]
            denom = f + self.k1 * (1 - self.b + self.b * length / self.avg_len)
            score += self.idf.get(t, 0.0) * f * (self.k1 + 1) / denom
        return score

    def top_pages(self, field: Field, k: int = 3) -> list[int]:
        """Return the 1-based page numbers most likely to contain `field`."""
        scores = []
        for i, page in enumerate(self.doc.pages):
            s = 0.0
            for kw in field.keywords:
                terms = tokenize(kw)
                s += self._bm25(i, terms)
                if kw.lower() in page.text.lower():       # exact phrase bonus
                    s += 2.0
            if field.type not in ("text", "boolean") and "[TABLE" in page.text:
                s *= 1.3
            scores.append((s, page.number))
        scores.sort(reverse=True)
        return [num for s, num in scores[:k] if s > 0]
