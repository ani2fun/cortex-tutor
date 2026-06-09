"""Cortex chapter-slug derivation — ported byte-for-byte from the Scala `CortexIndexWalker`.

The grounding server resolves a tutor ``problem_id`` (``<book>/<hierarchical-chapter-slug>``) back to a
markdown file, so its slugs MUST equal what the cortex frontend emits — otherwise the join key silently
breaks. Parity is pinned by ``tests/test_slug_parity.py``.

Reference: cortex ``shared/src/main/scala/cortex/shared/book/CortexIndexWalker.scala``
(``stripOrderPrefix`` / ``slugify`` / ``chapterSlugFromPath`` / ``slugLike`` / ``chapterPathLike``).
A chapter slug is the book-root-relative path with each segment order-prefix-stripped + slugified,
joined by ``/`` (ordering prefixes intentionally dropped → URLs encode identity, not position). The
**book** slug is the raw top-level directory name (NOT slugified).
"""

from __future__ import annotations

import re

# Leading numeric ordering prefix: ``01-`` / ``1.`` / ``10_`` / ``1`` …
_ORDER_PREFIX = re.compile(r"^\d+[._-]?")

# Companion source/asset dirs that are never chapter content (checked on the prefix-stripped name).
RESERVED_AUX_DIRS = frozenset({"examples", "c4"})


def strip_order_prefix(name: str) -> str:
    """``01-foo`` → ``foo``, ``1.bar`` → ``bar``, ``10_baz`` → ``baz``, ``noprefix`` → ``noprefix``."""
    return _ORDER_PREFIX.sub("", name, count=1)


def slugify(seg: str) -> str:
    """Lowercase letters/digits, keep ``_``, collapse every other run to a single ``-``, trim ends.

    Mirrors the Scala ``slugify``: the leading-separator guard (``sb.nonEmpty``) drops leading dashes,
    and a trailing dash is dropped at the end.
    """
    out: list[str] = []
    last_dash = False
    for ch in seg:
        if ch.isalnum():
            out.append(ch.lower())
            last_dash = False
        elif ch == "_":
            out.append("_")
            last_dash = False
        elif not last_dash and out:
            out.append("-")
            last_dash = True
        # leading / repeated separators are skipped
    s = "".join(out)
    return s[:-1] if s.endswith("-") else s


def chapter_slug(path_segs: list[str]) -> str:
    """Book-root-relative path segments → hierarchical slug.

    ``["02-system", "01-next-step.md"]`` → ``"system/next-step"``. Each segment is ``.md``-stripped,
    order-prefix-stripped, slugified, and joined with ``/``; empty results are dropped.
    """
    parts: list[str] = []
    for raw in path_segs:
        seg = raw.removesuffix(".md")
        if not seg:
            continue
        s = slugify(strip_order_prefix(seg))
        if s:
            parts.append(s)
    return "/".join(parts)


def slug_like(name: str) -> bool:
    """Letters, digits, hyphens, underscores; non-empty. (Single segment — no ``/``.)"""
    return bool(name) and all(c.isalnum() or c in "-_" for c in name)


def chapter_path_like(s: str) -> bool:
    """A ``/``-joined chapter slug whose every segment is ``slug_like``; rejects empty segments + ``..``
    (so it doubles as the path-traversal guard when resolving a ``problem_id``)."""
    return bool(s) and all(slug_like(seg) for seg in s.split("/"))


def includes_as_content(name: str) -> bool:
    """Whether a directory name is eligible to be a book/section: slug-like, not ``_``/``.``-prefixed,
    not a reserved companion-asset dir (checked on the order-prefix-stripped name)."""
    return (
        slug_like(name)
        and not name.startswith("_")
        and not name.startswith(".")
        and strip_order_prefix(name) not in RESERVED_AUX_DIRS
    )
