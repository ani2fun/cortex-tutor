"""The 5 grounding tools as plain methods returning JSON-serializable dicts.

``server.py`` registers these as MCP tools; keeping them plain keeps them unit-testable without the MCP
transport. Every payload is capped server-side (≈4 chars/token) so a result never blows the model's
context. **Leak control:** ``get_problem`` never returns the solution; ``get_lesson`` returns it only
when ``include_solution=True`` (the implement/test steps).
"""

from __future__ import annotations

from grounding_mcp.config import GroundingSettings, get_settings
from grounding_mcp.corpus import ChapterMeta, CorpusIndex
from grounding_mcp.search import Bm25Index, snippet


def _cap(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[:limit].rstrip() + "\n\n…[truncated]"


class Grounding:
    """Holds the built corpus + BM25 index; one instance per process (built at server startup)."""

    def __init__(self, settings: GroundingSettings | None = None) -> None:
        self.settings = settings or get_settings()
        self.corpus = CorpusIndex(self.settings.content_path)
        self.bm25 = Bm25Index(self.corpus)

    def _ref(self, meta: ChapterMeta) -> dict:
        return {
            "problemId": meta.problem_id,
            "title": meta.title,
            "book": meta.book,
            "groupPath": list(meta.group_path),
            "isProblem": meta.is_problem,
            "citationUrl": self.settings.citation_url(meta.problem_id),
        }

    def search_corpus(self, query: str, book: str | None = None, limit: int | None = None) -> dict:
        n = min(limit or self.settings.max_search_results, self.settings.max_search_results)
        results = []
        for hit in self.bm25.search(query, limit=n, book=book):
            doc = self.corpus.doc(hit.meta.problem_id)
            ref = self._ref(hit.meta)
            ref["snippet"] = snippet(
                doc.visible if doc else hit.meta.title, query, max_chars=self.settings.max_snippet_chars
            )
            ref["score"] = round(hit.score, 3)
            results.append(ref)
        return {"query": query, "results": results}

    def get_problem(self, problem_id: str) -> dict:
        doc = self.corpus.doc(problem_id)
        if doc is None:
            return {"error": "not_found", "problemId": problem_id}
        ref = self._ref(doc.meta)
        ref["summary"] = doc.meta.summary
        ref["statement"] = _cap(doc.visible, self.settings.max_body_chars)  # solution NEVER included
        return ref

    def get_lesson(self, problem_id: str, include_solution: bool = False) -> dict:
        doc = self.corpus.doc(problem_id)
        if doc is None:
            return {"error": "not_found", "problemId": problem_id}
        ref = self._ref(doc.meta)
        ref["summary"] = doc.meta.summary
        ref["content"] = _cap(doc.visible, self.settings.max_body_chars)
        # Withheld unless explicitly requested at the implement/test steps.
        ref["solution"] = (
            _cap(doc.solution, self.settings.max_body_chars) if include_solution and doc.solution else None
        )
        return ref

    def list_related(self, problem_id: str, limit: int | None = None) -> dict:
        doc = self.corpus.doc(problem_id)
        if doc is None:
            return {"error": "not_found", "problemId": problem_id, "related": []}
        n = min(limit or self.settings.max_related, self.settings.max_related)
        related = []
        for hit in self.bm25.search(f"{doc.meta.title} {doc.visible}", limit=n + 1):
            if hit.meta.problem_id == problem_id:
                continue
            ref = self._ref(hit.meta)
            ref["score"] = round(hit.score, 3)
            related.append(ref)
            if len(related) >= n:
                break
        return {"problemId": problem_id, "related": related}

    def get_corpus_outline(self, book: str | None = None) -> dict:
        budget = self.settings.max_outline_chars
        used = 0
        books = []
        truncated = False
        for b in self.corpus.books:
            if book is not None and b.slug != book:
                continue
            chapters = []
            for c in b.chapters:
                est = len(c.problem_id) + len(c.title) + sum(len(g) for g in c.group_path) + 40
                if used + est > budget:
                    truncated = True
                    break
                used += est
                chapters.append(
                    {
                        "problemId": c.problem_id,
                        "title": c.title,
                        "groupPath": list(c.group_path),
                        "isProblem": c.is_problem,
                    }
                )
            books.append(
                {"slug": b.slug, "title": b.title, "description": b.description, "chapters": chapters}
            )
            if truncated:
                break
        result: dict = {"books": books}
        if truncated:
            result["truncated"] = True  # surface the cap (no silent truncation)
        return result
