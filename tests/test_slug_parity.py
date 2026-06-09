"""Pin the Python slug derivation byte-for-byte against the cortex Scala `CortexIndexWalker`.

If these drift, the tutor's `problem_id` (`<book>/<hierarchical-chapter-slug>`) stops resolving to the
right chapter in the grounding server. The expected values mirror cortex's `CortexIndexWalkerSpec`
(public-helper cases) plus the live-verified two-sum slug from the hierarchical-slug migration.
"""

from __future__ import annotations

from grounding_mcp import slug


def test_chapter_slug_deep_problem():
    # Book-root-relative path of the canonical two-sum problem (verified live against the cortex server).
    segs = (
        "02-linear-structures/01-arrays/05-pattern-two-pointers-reduction/02-problems/01-two-sum.md"
    ).split("/")
    assert (
        slug.chapter_slug(segs) == "linear-structures/arrays/pattern-two-pointers-reduction/problems/two-sum"
    )


def test_chapter_slug_examples():
    assert slug.chapter_slug(["01-foundations", "index.md"]) == "foundations/index"
    assert slug.chapter_slug(["02-system", "01-next-step.md"]) == "system/next-step"
    assert slug.chapter_slug(["hello.md"]) == "hello"  # single segment at book root
    assert slug.chapter_slug(["10-late.md"]) == "late"


def test_slugify_matches_scala_helpers():
    # Mirrors CortexIndexWalkerSpec "public helpers" → slugify.
    assert slug.slugify("Hello World!") == "hello-world"
    assert slug.slugify("foo--bar") == "foo-bar"
    assert slug.slugify("keep_underscore") == "keep_underscore"
    assert slug.slugify("-trim-") == "trim"


def test_strip_order_prefix_matches_scala_helpers():
    assert slug.strip_order_prefix("01-foo") == "foo"
    assert slug.strip_order_prefix("1.bar") == "bar"
    assert slug.strip_order_prefix("10_baz") == "baz"
    assert slug.strip_order_prefix("noprefix") == "noprefix"


def test_slug_like():
    assert slug.slug_like("hello-world_2")
    assert not slug.slug_like("")
    assert not slug.slug_like("has space")
    assert not slug.slug_like("has.dot")
    assert not slug.slug_like("a/b")  # single segment only


def test_chapter_path_like_guards_traversal():
    assert slug.chapter_path_like("arrays")
    assert slug.chapter_path_like("linear-structures/arrays/two-sum")
    assert not slug.chapter_path_like("")
    assert not slug.chapter_path_like("a//b")
    assert not slug.chapter_path_like("/leading")
    assert not slug.chapter_path_like("trailing/")
    assert not slug.chapter_path_like("a/../b")  # `..` is not slug_like → rejected


def test_includes_as_content_skips_reserved_and_hidden():
    assert slug.includes_as_content("data-structures-and-algorithms")
    assert slug.includes_as_content("01-foundations")
    assert not slug.includes_as_content("_drafts")
    assert not slug.includes_as_content(".git")
    assert not slug.includes_as_content("examples")
    assert not slug.includes_as_content("01-examples")  # prefix-stripped name is reserved
    assert not slug.includes_as_content("c4")
