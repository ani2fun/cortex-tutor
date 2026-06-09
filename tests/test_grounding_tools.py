"""The 5 grounding tools + BM25 — ranking, book filter, and the solution-leak guardrails.

A synthetic 2-book corpus keeps ranking deterministic. The leak assertions (`get_problem` never leaks,
`get_lesson` withholds then reveals) are the ones that matter most for the tutor's anti-spoiler design.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from grounding_mcp.config import GroundingSettings
from grounding_mcp.search import snippet, tokenize
from grounding_mcp.tools import Grounding


def _write(p: Path, content: str) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")


@pytest.fixture
def grounding(tmp_path: Path) -> Grounding:
    root = tmp_path / "content"
    _write(root / "book-a" / "book.json", json.dumps({"title": "Book A", "order": 1}))
    _write(root / "book-a" / "02-arrays" / "_section.json", json.dumps({"title": "Arrays"}))
    _write(
        root / "book-a" / "02-arrays" / "01-pattern.md",
        "# Two Pointers\n\nUse two pointers on a sorted array.",
    )
    _write(root / "book-a" / "02-arrays" / "02-problems" / "_section.json", json.dumps({"title": "Problems"}))
    _write(
        root / "book-a" / "02-arrays" / "02-problems" / "01-two-sum.md",
        "---\ntitle: Two Sum\nsummary: Find two.\n---\n\n"
        "## Problem Statement\n\nReturn the indices summing to the target.\n\n"
        "<details>\n<summary>Solution</summary>\n\nSECRET hashmap approach.\n\n</details>\n",
    )
    _write(root / "book-b" / "book.json", json.dumps({"title": "Book B", "order": 2}))
    _write(root / "book-b" / "01-trees.md", "# Trees\n\nA tree has nodes and edges; no arrays here.")
    settings = GroundingSettings(cortex_content_dir=str(root), cortex_public_base="https://cortex.test")
    return Grounding(settings)


def test_search_finds_and_builds_citation(grounding: Grounding):
    res = grounding.search_corpus("return indices target")
    ids = [r["problemId"] for r in res["results"]]
    assert "book-a/arrays/problems/two-sum" in ids
    top = res["results"][0]
    assert top["citationUrl"] == "https://cortex.test/" + top["problemId"]
    assert top["snippet"]


def test_search_book_filter(grounding: Grounding):
    res = grounding.search_corpus("nodes edges tree", book="book-b")
    assert res["results"], "expected a hit in book-b"
    assert all(r["book"] == "book-b" for r in res["results"])


def test_get_problem_never_leaks_solution(grounding: Grounding):
    res = grounding.get_problem("book-a/arrays/problems/two-sum")
    assert res["isProblem"] is True
    assert "Problem Statement" in res["statement"]
    assert "solution" not in res  # the field isn't even present
    assert "SECRET" not in json.dumps(res)


def test_get_lesson_withholds_then_reveals(grounding: Grounding):
    closed = grounding.get_lesson("book-a/arrays/problems/two-sum", include_solution=False)
    assert closed["solution"] is None
    assert "SECRET" not in json.dumps(closed)
    opened = grounding.get_lesson("book-a/arrays/problems/two-sum", include_solution=True)
    assert opened["solution"] and "hashmap" in opened["solution"]


def test_not_found(grounding: Grounding):
    assert grounding.get_problem("book-a/nope/missing")["error"] == "not_found"
    assert grounding.get_lesson("book-a/a/../b")["error"] == "not_found"  # traversal guard


def test_list_related_excludes_self(grounding: Grounding):
    res = grounding.list_related("book-a/arrays/problems/two-sum")
    ids = [r["problemId"] for r in res["related"]]
    assert "book-a/arrays/problems/two-sum" not in ids
    assert "book-a/arrays/pattern" in ids  # shares the "two" / arrays vocabulary


def test_outline_full_and_filtered(grounding: Grounding):
    assert [b["slug"] for b in grounding.get_corpus_outline()["books"]] == ["book-a", "book-b"]
    just_a = grounding.get_corpus_outline(book="book-a")
    assert [b["slug"] for b in just_a["books"]] == ["book-a"]
    assert "Two Sum" in [c["title"] for c in just_a["books"][0]["chapters"]]


def test_tokenize_and_snippet():
    assert tokenize("Two-Sum, hashMAP!") == ["two", "sum", "hashmap"]
    assert "hashmap" in snippet("alpha beta gamma delta hashmap epsilon zeta", "hashmap", max_chars=20)
