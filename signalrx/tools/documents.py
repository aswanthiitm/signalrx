"""Turn fetched documents into searchable text, and verify quoted passages.

Grounding rule: an Evidence passage extracted by the LLM is kept only if it can
be found in the document it claims to come from (verify_quote). This is what
stops the model from inventing a sentence and attaching a real URL to it.
"""
from __future__ import annotations

import io
import re
from dataclasses import dataclass, field
from difflib import SequenceMatcher

from bs4 import BeautifulSoup

_WS = re.compile(r"\s+")


def norm(s: str) -> str:
    s = s.replace("’", "'").replace("‘", "'").replace("“", '"').replace("”", '"').replace("–", "-").replace("—", "-")
    return _WS.sub(" ", s).strip().lower()


def html_to_text(html: str) -> str:
    soup = BeautifulSoup(html, "lxml")
    for t in soup(["script", "style", "noscript", "svg", "header", "footer", "nav"]):
        t.decompose()
    text = soup.get_text("\n")
    return "\n".join(line.strip() for line in text.splitlines() if line.strip())


def pdf_to_text(data: bytes, max_pages: int = 400) -> str:
    from pypdf import PdfReader
    reader = PdfReader(io.BytesIO(data))
    pages = []
    for i, page in enumerate(reader.pages[:max_pages]):
        try:
            pages.append(f"[page {i + 1}]\n" + (page.extract_text() or ""))
        except Exception:  # damaged page: keep going, note the gap
            pages.append(f"[page {i + 1}] <unreadable>")
    return "\n".join(pages)


def to_text(body: bytes, content_type: str, url: str = "") -> str:
    if "pdf" in content_type or url.lower().endswith(".pdf") or body[:4] == b"%PDF":
        return pdf_to_text(body)
    return html_to_text(body.decode("utf-8", errors="replace"))


@dataclass
class Doc:
    url: str
    title: str
    text: str
    chunks: list[str] = field(default_factory=list)

    def __post_init__(self):
        if not self.chunks:
            self.chunks = chunk(self.text)


def chunk(text: str, size: int = 1400, overlap: int = 200) -> list[str]:
    out, i = [], 0
    while i < len(text):
        out.append(text[i:i + size])
        i += size - overlap
    return out or [""]


class DocIndex:
    """BM25 over chunks of every document fetched in a run."""

    def __init__(self):
        self.docs: dict[str, Doc] = {}
        self._rows: list[tuple[str, int]] = []
        self._bm25 = None

    def add(self, doc: Doc) -> None:
        self.docs[doc.url] = doc
        self._bm25 = None

    def _build(self):
        from rank_bm25 import BM25Okapi
        self._rows = [(u, i) for u, d in self.docs.items() for i in range(len(d.chunks))]
        corpus = [norm(self.docs[u].chunks[i]).split() for u, i in self._rows]
        self._bm25 = BM25Okapi(corpus) if corpus else None

    def search(self, query: str, k: int = 5, url: str | None = None) -> list[tuple[str, str, float]]:
        if self._bm25 is None:
            self._build()
        if not self._bm25:
            return []
        scores = self._bm25.get_scores(norm(query).split())
        ranked = sorted(range(len(scores)), key=lambda j: -scores[j])
        out = []
        for j in ranked:
            u, i = self._rows[j]
            if url and u != url:
                continue
            out.append((u, self.docs[u].chunks[i], float(scores[j])))
            if len(out) >= k:
                break
        return out


_NUMS = re.compile(r"\d[\d,]*(?:\.\d+)?")


def _numbers(s: str) -> list[str]:
    return [n.replace(",", "") for n in _NUMS.findall(s)]


def verify_quote(quote: str, text: str, threshold: float = 0.90) -> tuple[bool, float]:
    """Is `quote` (or something within OCR/whitespace noise of it) in `text`?

    Words may differ slightly (OCR, hyphenation, curly quotes); numbers may not:
    every number in the quote must occur verbatim in the matched window."""
    q, t = norm(quote), norm(text)
    if not q:
        return False, 0.0
    if q in t:
        return True, 1.0
    qnums = _numbers(q)
    # sliding window fuzzy match anchored on the rarest long token
    toks = sorted(set(q.split()), key=lambda w: (t.count(w) if len(w) > 4 else 1e9))
    anchors = [w for w in toks if len(w) > 4 and w in t][:3]
    best = 0.0
    for a in anchors or [q.split()[0]]:
        for m in re.finditer(re.escape(a), t):
            start = max(0, m.start() - len(q))
            window = t[start:m.end() + len(q)]
            sm = SequenceMatcher(None, q, window, autojunk=False)
            blk = sm.find_longest_match(0, len(q), 0, len(window))
            lo = max(0, blk.b - blk.a)
            cand = window[lo:lo + len(q)]
            ratio = SequenceMatcher(None, q, cand, autojunk=False).ratio()
            wnums = set(_numbers(window))
            if qnums and not all(n in wnums for n in qnums):
                continue                     # a changed number is never "noise"
            best = max(best, ratio)
            if best >= threshold:
                return True, best
    return best >= threshold, best
