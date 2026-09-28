"""The docs renderer. These are the cases the project's own docs actually contain, plus the
escaping rules, because a renderer that is only safe on trusted input is a trap for whoever
points it at something else later.
"""
import re
from pathlib import Path

import pytest

from testbench import markdown

ROOT = Path(__file__).resolve().parent.parent
DOCS = sorted((ROOT / "docs").rglob("*.md"))


def html(src):
    return str(markdown.render(src)[0])


# ---------------------------------------------------------------- blocks

def test_headings_carry_an_id_so_sections_can_be_linked():
    out = html("# Title\n\n## A section\n\n### Deeper\n")
    assert '<h1 id="title">Title</h1>' in out
    assert '<h2 id="a-section">A section</h2>' in out
    assert '<h3 id="deeper">Deeper</h3>' in out


def test_headings_are_returned_for_the_contents_list():
    _, headings = markdown.render("# Title\n\n## One\n\n### Under one\n\n## Two\n")
    assert headings == [(1, "Title", "title"), (2, "One", "one"),
                        (3, "Under one", "under-one"), (2, "Two", "two")]


def test_bullet_and_numbered_lists():
    assert html("- one\n- two\n") == "<ul><li>one</li><li>two</li></ul>"
    assert html("1. one\n2. two\n") == "<ol><li>one</li><li>two</li></ol>"


def test_a_nested_list_sits_inside_its_parent_item():
    """As a sibling of the <li> it is invalid HTML and browsers indent it inconsistently."""
    assert html("- parent\n  - child\n- sibling\n") == (
        "<ul><li>parent<ul><li>child</li></ul></li><li>sibling</li></ul>")
    assert html("1. step\n   - detail\n2. next\n") == (
        "<ol><li>step<ul><li>detail</li></ul></li><li>next</li></ol>")


def test_a_wrapped_list_item_stays_one_item():
    assert html("- an item that wraps\n  onto the next line\n") == (
        "<ul><li>an item that wraps onto the next line</li></ul>")


def test_tables_render_with_a_header_and_alignment():
    out = html("| Name | Count |\n| --- | ---: |\n| a | 1 |\n| b | 2 |\n")
    assert "<table>" in out and "<thead>" in out
    assert "<th>Name</th>" in out
    assert '<th style="text-align:right">Count</th>' in out
    assert out.count("<tr>") == 3          # one header row plus two body rows
    assert '<div class="table-wrap">' in out   # so a wide table scrolls on its own


def test_a_table_needs_its_divider_row():
    """Without it, a line with pipes is just a paragraph, not a one-column table."""
    assert "<table>" not in html("| not | a table |\n\nplain paragraph\n")


def test_fenced_code_keeps_its_language_and_is_not_formatted():
    out = html("```python\nx = {'a': 1}  # **not bold**\n```\n")
    assert 'class="language-python"' in out
    assert "<strong>" not in out
    assert "**not bold**" in out
    assert '<span class="code-lang">python</span>' in out


def test_block_quotes_and_rules():
    assert "<blockquote>" in html("> quoted\n")
    assert "<hr>" in html("a\n\n---\n\nb\n")


# ---------------------------------------------------------------- inline

def test_inline_emphasis_code_and_links():
    out = html("A **bold** and *italic* with `code` and [a link](https://example.org/x).")
    assert "<strong>bold</strong>" in out
    assert "<em>italic</em>" in out
    assert "<code>code</code>" in out
    assert '<a href="https://example.org/x" target="_blank" rel="noopener noreferrer">a link</a>' in out


def test_markup_inside_inline_code_is_left_alone():
    out = html("Use `**not bold**` here.")
    assert "<code>**not bold**</code>" in out
    assert "<strong>" not in out


def test_a_relative_link_is_not_given_a_target():
    out = html("See [the plan](plans/saas-readiness.md).")
    assert 'href="plans/saas-readiness.md"' in out
    assert "target=" not in out


# ---------------------------------------------------------------- safety

def test_html_in_the_source_is_escaped():
    out = html("<script>alert(1)</script> and <b>raw</b>")
    assert "<script>" not in out
    assert "&lt;script&gt;" in out
    assert "<b>" not in out


@pytest.mark.parametrize("url", [
    "javascript:alert(1)", "JaVaScript:alert(1)", "data:text/html,<script>", "vbscript:x",
])
def test_dangerous_link_schemes_are_refused(url):
    assert markdown.safe_href(url) == "#"
    assert url.split(":")[0] not in html(f"[x]({url})")


@pytest.mark.parametrize("url", [
    "https://example.org/a", "http://example.org", "/docs/x", "#anchor",
    "mailto:a@example.org", "../plans/x.md", "statistics.md",
])
def test_ordinary_links_survive(url):
    assert markdown.safe_href(url) == url


# ---------------------------------------------------------------- the real docs

@pytest.mark.parametrize("path", DOCS, ids=[p.stem for p in DOCS])
def test_every_project_doc_renders_without_leftover_markup(path):
    out = html(path.read_text(encoding="utf-8"))
    assert out.strip(), f"{path.name} rendered empty"
    # A table divider left in the output means the table was not recognised.
    assert "|---" not in out and "| --- |" not in out
    # A heading marker at the start of a paragraph means the heading was missed.
    assert "<p>#" not in out
    assert "<script>" not in out


def test_the_docs_with_tables_actually_produce_tables():
    for path in DOCS:
        source = path.read_text(encoding="utf-8")
        if re.search(r"^\|.*\|\s*$", source, re.M) and re.search(r"^\|?\s*:?-{2,}", source, re.M):
            assert "<table>" in html(source), f"{path.name} has tables that did not render"
