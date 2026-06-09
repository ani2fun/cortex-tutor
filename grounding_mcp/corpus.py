"""Walk the cortex markdown corpus → an in-memory index the MCP tools serve from.

Slugs come from ``slug.py`` (parity with the cortex frontend): the **book** slug is the raw top-level
directory name; a **chapter** slug is the book-root-relative path, hierarchical (``a/b/c``). Each
chapter body is split into a VISIBLE part and a withheld COLLAPSIBLE part:

* ``visible``  — everything outside ``<details>`` blocks: the flat ``## Problem Statement`` + ``##
  Examples`` for a DSA problem, or the whole lesson otherwise.
* ``solution`` — the ``<details>`` blocks (intuition / solution / analysis / key takeaway).

This split is the **load-bearing solution-leak control**: ``get_problem`` and
``get_lesson(include_solution=False)`` only ever return ``visible``. (Assumes the cortex convention of
*flat sibling* ``<details>`` — not nested — which the DSA problem-section spec guarantees.)
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

from grounding_mcp import slug

# Flat (non-nested) <details>…</details> block — DOTALL so it spans newlines.
_DETAILS = re.compile(r"<details\b[^>]*>.*?</details>", re.DOTALL | re.IGNORECASE)
_DETAIL_TAGS = re.compile(r"</?(?:details|summary)\b[^>]*>", re.IGNORECASE)
_LEADING_NUM = re.compile(r"^\d+")
_BLANK_RUN = re.compile(r"\n{3,}")


# ── public data ─────────────────────────────────────────────────────────────
@dataclass(frozen=True)
class ChapterMeta:
    book: str
    slug: str  # hierarchical, book-root-relative (e.g. "linear-structures/arrays/two-sum")
    title: str
    rel_path: str  # path within the book (e.g. "02-linear-structures/01-arrays/.../01-two-sum.md")
    group_path: tuple[str, ...]  # section display titles, root → leaf
    is_problem: bool
    summary: str | None = None

    @property
    def problem_id(self) -> str:
        return f"{self.book}/{self.slug}"


@dataclass(frozen=True)
class ChapterDoc:
    meta: ChapterMeta
    visible: str  # statement + examples (or the whole lesson); solution withheld
    solution: str  # the <details> blocks; withheld until the implement/test steps


@dataclass(frozen=True)
class BookMeta:
    slug: str
    title: str
    description: str
    chapters: tuple[ChapterMeta, ...]


# ── markdown helpers ──────────────────────────────────────────────────────────
def strip_frontmatter(raw: str) -> tuple[dict[str, str], str]:
    """Split YAML frontmatter (top-level ``key: value`` only) from the body. Unterminated frontmatter
    is treated as plain body (mirrors the cortex Frontmatter seam)."""
    if not raw.startswith("---"):
        return {}, raw
    lines = raw.split("\n")
    end = next((i for i in range(1, len(lines)) if lines[i].strip() == "---"), -1)
    if end < 0:
        return {}, raw
    fm: dict[str, str] = {}
    for line in lines[1:end]:
        if not line or line[0] in " \t-":  # skip nested keys / list items
            continue
        key, sep, val = line.partition(":")
        if sep and key.strip():
            fm[key.strip()] = val.strip().strip("\"'")
    return fm, "\n".join(lines[end + 1 :]).lstrip("\n")


def first_h1(body: str) -> str | None:
    in_code = False
    for line in body.split("\n"):
        if line.startswith("```"):
            in_code = not in_code
        elif not in_code and line.startswith("# "):
            return line[2:].strip()
    return None


def humanise(name: str) -> str:
    cleaned = slug.strip_order_prefix(name).removesuffix(".md")
    return " ".join(w.capitalize() for w in re.split(r"[-_.]", cleaned) if w)


def split_visible_solution(body: str) -> tuple[str, str]:
    """(visible, solution) — visible = body minus all ``<details>`` blocks; solution = those blocks
    (with the ``<details>``/``<summary>`` tags stripped for readability)."""
    blocks = [m.group(0) for m in _DETAILS.finditer(body)]
    visible = _BLANK_RUN.sub("\n\n", _DETAILS.sub("", body)).strip()
    solution = _BLANK_RUN.sub("\n\n", _DETAIL_TAGS.sub("", "\n\n".join(blocks))).strip()
    return visible, solution


def _safe_read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return ""


def _read_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _order_key(p: Path) -> tuple[int, str]:
    """Sibling order: ``index`` first, then leading-integer, then everything else; ties by name."""
    stem = p.name.removesuffix(".md")
    if stem == "index":
        return (-1, p.name.lower())
    m = _LEADING_NUM.match(p.name)
    return (int(m.group(0)) if m else 2**31, p.name.lower())


# ── walk + index ──────────────────────────────────────────────────────────────
def _collect(book_dir: Path) -> list[tuple[ChapterMeta, str]]:
    """All chapters under ``book_dir`` as (meta, post-frontmatter-body), in sidebar order."""
    out: list[tuple[ChapterMeta, str]] = []

    def rec(d: Path, segs: list[str], group: tuple[str, ...]) -> None:
        for entry in sorted(d.iterdir(), key=_order_key):
            name = entry.name
            if name.startswith(("_", ".")):
                continue
            if entry.is_file():
                if not name.endswith(".md"):
                    continue
                cslug = slug.chapter_slug([*segs, name])
                if not cslug:
                    continue
                fm, body = strip_frontmatter(_safe_read(entry))
                title = fm.get("title") or first_h1(body) or humanise(name)
                is_problem = any(slug.strip_order_prefix(s) == "problems" for s in segs)
                meta = ChapterMeta(
                    book=book_dir.name,
                    slug=cslug,
                    title=title,
                    rel_path="/".join([*segs, name]),
                    group_path=group,
                    is_problem=is_problem,
                    summary=fm.get("summary"),
                )
                out.append((meta, body))
            elif entry.is_dir() and slug.includes_as_content(name):
                section_title = _read_json(entry / "_section.json").get("title") or humanise(name)
                rec(entry, [*segs, name], (*group, section_title))

    rec(book_dir, [], ())
    return out


class CorpusIndex:
    """In-memory corpus: built once from the content dir, served by the MCP tools."""

    def __init__(self, content_dir: Path) -> None:
        self.content_dir = content_dir
        self.books: list[BookMeta] = []
        self._docs: dict[str, ChapterDoc] = {}
        self._build()

    def _build(self) -> None:
        if not self.content_dir.is_dir():
            return
        book_dirs = sorted(
            (d for d in self.content_dir.iterdir() if d.is_dir() and slug.includes_as_content(d.name)),
            key=lambda d: (_read_json(d / "book.json").get("order") or 2**31, d.name.lower()),
        )
        for bdir in book_dirs:
            bjson = _read_json(bdir / "book.json")
            chapters: list[ChapterMeta] = []
            for meta, body in _collect(bdir):
                visible, solution = split_visible_solution(body)
                self._docs[meta.problem_id] = ChapterDoc(meta=meta, visible=visible, solution=solution)
                chapters.append(meta)
            self.books.append(
                BookMeta(
                    slug=bdir.name,
                    title=bjson.get("title") or humanise(bdir.name),
                    description=bjson.get("description") or "",
                    chapters=tuple(chapters),
                )
            )

    @property
    def chapters(self) -> list[ChapterMeta]:
        return [c for b in self.books for c in b.chapters]

    def doc(self, problem_id: str) -> ChapterDoc | None:
        """Resolve ``<book>/<hierarchical-slug>`` to its chapter doc; ``None`` if unknown or unsafe."""
        book, _, slug_part = problem_id.partition("/")
        if not slug_part or not slug.chapter_path_like(slug_part):
            return None
        return self._docs.get(f"{book}/{slug_part}")
