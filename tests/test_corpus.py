"""CorpusIndex: walk, slug resolution, and the visible-vs-solution split (the leak control).

A synthetic corpus exercises the logic deterministically; a real-corpus smoke (skipped when the cortex
checkout isn't present) confirms the canonical two-sum problem resolves through the real tree.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from grounding_mcp.corpus import CorpusIndex, split_visible_solution, strip_frontmatter


def _write(p: Path, content: str) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")


@pytest.fixture
def corpus(tmp_path: Path) -> CorpusIndex:
    root = tmp_path / "content"
    _write(root / "book-a" / "book.json", json.dumps({"title": "Book A", "order": 1}))
    _write(root / "book-a" / "01-intro.md", "---\ntitle: Intro\n---\n# Heading Loses\n\nintro body")
    _write(root / "book-a" / "02-arrays" / "_section.json", json.dumps({"title": "Arrays"}))
    _write(root / "book-a" / "02-arrays" / "01-pattern.md", "# Pattern\n\nlesson body, no details")
    _write(root / "book-a" / "02-arrays" / "02-problems" / "_section.json", json.dumps({"title": "Problems"}))
    _write(
        root / "book-a" / "02-arrays" / "02-problems" / "01-two-sum.md",
        "---\ntitle: Two Sum\nsummary: Find two.\n---\n\n"
        "## Problem Statement\n\nGiven nums and target, return indices.\n\n"
        "## Examples\n\nInput: [2,7], 9 -> [0,1]\n\n"
        "<details>\n<summary><h3>Solution</h3></summary>\n\nSECRET: use a hashmap.\n\n</details>\n",
    )
    return CorpusIndex(root)


def test_books_and_hierarchical_slugs(corpus: CorpusIndex):
    assert [b.slug for b in corpus.books] == ["book-a"]
    assert corpus.books[0].title == "Book A"
    assert [c.slug for c in corpus.chapters] == ["intro", "arrays/pattern", "arrays/problems/two-sum"]


def test_resolve_meta(corpus: CorpusIndex):
    doc = corpus.doc("book-a/arrays/problems/two-sum")
    assert doc is not None
    assert doc.meta.title == "Two Sum"  # frontmatter title wins over the body H1
    assert doc.meta.summary == "Find two."
    assert doc.meta.is_problem is True
    assert doc.meta.group_path == ("Arrays", "Problems")


def test_solution_withheld_from_visible(corpus: CorpusIndex):
    doc = corpus.doc("book-a/arrays/problems/two-sum")
    assert doc is not None
    assert "Problem Statement" in doc.visible
    assert "Examples" in doc.visible
    assert "SECRET" not in doc.visible  # ← the load-bearing leak control
    assert "hashmap" in doc.solution  # available only when explicitly requested
    assert "<details>" not in doc.solution and "<summary>" not in doc.solution


def test_lesson_has_no_solution(corpus: CorpusIndex):
    doc = corpus.doc("book-a/arrays/pattern")
    assert doc is not None
    assert doc.meta.is_problem is False
    assert doc.meta.title == "Pattern"  # H1 fallback (no frontmatter)
    assert "lesson body" in doc.visible
    assert doc.solution == ""


def test_unknown_and_unsafe_problem_ids(corpus: CorpusIndex):
    assert corpus.doc("book-a/nope/missing") is None
    assert corpus.doc("book-a/a/../b") is None  # traversal guard via chapter_path_like
    assert corpus.doc("book-a") is None  # no slug part


def test_split_visible_solution_unit():
    body = "Visible.\n\n<details>\n<summary>Hint</summary>\nhidden\n</details>\n\nMore visible."
    vis, sol = split_visible_solution(body)
    assert "Visible." in vis and "More visible." in vis and "hidden" not in vis
    assert "hidden" in sol and "<details>" not in sol


def test_strip_frontmatter_unterminated_is_plain_body():
    fm, body = strip_frontmatter("---\ntitle: X\n\nno closing fence")
    assert fm == {}
    assert body.startswith("---")


# ── real-corpus smoke ─────────────────────────────────────────────────────────
_REAL = Path.home() / "Development" / "homelab" / "cortex" / "content" / "cortex"


@pytest.mark.skipif(not _REAL.is_dir(), reason="cortex content checkout not present")
def test_real_corpus_two_sum_resolves():
    idx = CorpusIndex(_REAL)
    doc = idx.doc(
        "data-structures-and-algorithms/linear-structures/arrays/"
        "pattern-two-pointers-reduction/problems/two-sum"
    )
    assert doc is not None, "the canonical two-sum problem_id must resolve in the real corpus"
    assert doc.meta.is_problem is True
    assert doc.visible.strip(), "problem statement should be present in the visible part"
