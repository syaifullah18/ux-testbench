"""A small Markdown renderer for the project's own docs.

The docs under `docs/` are written by hand and use a known, limited set of Markdown: headings,
paragraphs, fenced code, bullet and numbered lists, tables, block quotes, rules, and the usual
inline emphasis, code and links. This renders exactly that, and nothing else, rather than adding
a dependency for a handful of pages.

It emits plain semantic HTML. All of the visual styling lives in the template, through Tailwind's
typography plugin, so the two themes come for free and this module never mentions a colour.

Everything is escaped before any markup is added, and inline code is lifted out before emphasis
runs so that a backtick span is never reinterpreted. The docs are repository files rather than
user input, but a renderer that only works on trusted input is a trap waiting for the first
person who points it at something else.
"""
import re

from markupsafe import Markup, escape

FENCE_RE = re.compile(r"^\s*```\s*([A-Za-z0-9_+-]*)\s*$")
HEADING_RE = re.compile(r"^(#{1,6})\s+(.*)$")
UL_RE = re.compile(r"^(\s*)[-*+]\s+(.*)$")
OL_RE = re.compile(r"^(\s*)(\d+)[.)]\s+(.*)$")
QUOTE_RE = re.compile(r"^>\s?(.*)$")
RULE_RE = re.compile(r"^\s*([-*_])(\s*\1){2,}\s*$")
TABLE_DIVIDER_RE = re.compile(r"^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?\s*$")

# Inline, applied after escaping. Code spans are pulled out first and put back last.
CODE_SPAN_RE = re.compile(r"`([^`]+)`")
LINK_RE = re.compile(r"\[([^\]]+)\]\(([^)\s]+)(?:\s+&#34;([^&]*)&#34;)?\)")
BOLD_RE = re.compile(r"\*\*([^*]+)\*\*")
ITALIC_RE = re.compile(r"(?<![*\w])\*([^*\n]+)\*(?!\*)")
AUTOLINK_RE = re.compile(r"(?<![\"'=(])\bhttps?://[^\s<>()\[\]&]+")


SAFE_SCHEME_RE = re.compile(r"^(?:https?:|mailto:|#|/|\.{0,2}/|[A-Za-z0-9._-]+(?:[/#?]|$))")


def safe_href(url):
    """An href we are willing to emit, or '#'.

    Anything that is not plainly http(s), mailto, a fragment or a relative path is refused,
    which is what keeps `javascript:` and `data:` out.
    """
    cleaned = url.strip().replace("\x00", "")
    if "\n" in cleaned or "\t" in cleaned:
        return "#"
    return cleaned if SAFE_SCHEME_RE.match(cleaned) else "#"


def slugify(text):
    """A stable id for a heading, so a section can be linked to directly."""
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return slug or "section"


def _inline(text):
    """Escape, then apply inline Markdown. Returns a string of safe HTML."""
    out = escape(text)                      # everything below builds on escaped text
    spans = []

    def stash(match):
        spans.append(match.group(1))
        return f"\x00{len(spans) - 1}\x00"

    out = CODE_SPAN_RE.sub(stash, str(out))
    def link(m):
        href = safe_href(m.group(2))
        title = f' title="{m.group(3)}"' if m.group(3) else ""
        external = ' target="_blank" rel="noopener noreferrer"' if href.startswith("http") else ""
        return f'<a href="{href}"{title}{external}>{m.group(1)}</a>'

    out = LINK_RE.sub(link, out)
    out = AUTOLINK_RE.sub(lambda m: f'<a href="{m.group(0)}" target="_blank" '
                                    f'rel="noopener noreferrer">{m.group(0)}</a>', out)
    out = BOLD_RE.sub(r"<strong>\1</strong>", out)
    out = ITALIC_RE.sub(r"<em>\1</em>", out)
    for i, code in enumerate(spans):
        out = out.replace(f"\x00{i}\x00", f"<code>{code}</code>")
    return out


def _split_row(line):
    """Cells of one table row, without the outer pipes. Escaped pipes stay in the cell."""
    line = line.strip()
    if line.startswith("|"):
        line = line[1:]
    if line.endswith("|") and not line.endswith("\\|"):
        line = line[:-1]
    return [c.strip().replace("\\|", "|") for c in re.split(r"(?<!\\)\|", line)]


def _alignments(divider):
    out = []
    for cell in _split_row(divider):
        left, right = cell.startswith(":"), cell.endswith(":")
        out.append("center" if left and right else "right" if right else "left" if left else None)
    return out


class _Renderer:
    def __init__(self, text):
        self.lines = text.replace("\r\n", "\n").split("\n")
        self.i = 0
        self.html = []
        self.headings = []      # (level, text, slug) for the on-page contents list

    # -- helpers ---------------------------------------------------------
    def peek(self, offset=0):
        j = self.i + offset
        return self.lines[j] if j < len(self.lines) else None

    def done(self):
        return self.i >= len(self.lines)

    # -- blocks ----------------------------------------------------------
    def render(self):
        while not self.done():
            line = self.lines[self.i]
            if not line.strip():
                self.i += 1
            elif FENCE_RE.match(line):
                self.code_block()
            elif HEADING_RE.match(line):
                self.heading()
            elif RULE_RE.match(line):
                self.html.append("<hr>")
                self.i += 1
            elif self.at_table():
                self.table()
            elif QUOTE_RE.match(line):
                self.quote()
            elif UL_RE.match(line) or OL_RE.match(line):
                self.html.append(self.list_block(indent=0))
            else:
                self.paragraph()
        return Markup("\n".join(self.html))

    def heading(self):
        level, text = HEADING_RE.match(self.lines[self.i]).groups()
        n = len(level)
        slug = slugify(text)
        self.headings.append((n, text.strip(), slug))
        self.html.append(f'<h{n} id="{slug}">{_inline(text.strip())}</h{n}>')
        self.i += 1

    def code_block(self):
        lang = FENCE_RE.match(self.lines[self.i]).group(1)
        self.i += 1
        body = []
        while not self.done() and not FENCE_RE.match(self.lines[self.i]):
            body.append(self.lines[self.i])
            self.i += 1
        self.i += 1                                    # closing fence, if it is there
        label = f'<span class="code-lang">{escape(lang)}</span>' if lang else ""
        klass = f' class="language-{escape(lang)}"' if lang else ""
        self.html.append(
            f'<div class="code-block">{label}<pre><code{klass}>'
            f'{escape(chr(10).join(body))}</code></pre></div>')

    def quote(self):
        body = []
        while not self.done() and QUOTE_RE.match(self.lines[self.i]):
            body.append(QUOTE_RE.match(self.lines[self.i]).group(1))
            self.i += 1
        inner = _Renderer("\n".join(body)).render()
        self.html.append(f"<blockquote>{inner}</blockquote>")

    def at_table(self):
        line, nxt = self.peek(), self.peek(1)
        return bool(line and nxt and "|" in line and TABLE_DIVIDER_RE.match(nxt))

    def table(self):
        header = _split_row(self.lines[self.i])
        aligns = _alignments(self.lines[self.i + 1])
        self.i += 2
        rows = []
        while not self.done() and "|" in self.lines[self.i] and self.lines[self.i].strip():
            rows.append(_split_row(self.lines[self.i]))
            self.i += 1

        def cell(tag, text, idx):
            align = aligns[idx] if idx < len(aligns) else None
            style = f' style="text-align:{align}"' if align else ""
            return f"<{tag}{style}>{_inline(text)}</{tag}>"

        head = "".join(cell("th", c, i) for i, c in enumerate(header))
        body = "".join(
            "<tr>" + "".join(cell("td", c, i) for i, c in enumerate(r)) + "</tr>" for r in rows)
        self.html.append(
            f'<div class="table-wrap"><table><thead><tr>{head}</tr></thead>'
            f"<tbody>{body}</tbody></table></div>")

    def list_block(self, indent):
        """One list, recursing for anything indented further. Handles a bullet list nested in a
        numbered one and the other way round, which the docs both use."""
        first = UL_RE.match(self.lines[self.i]) or OL_RE.match(self.lines[self.i])
        ordered = bool(OL_RE.match(self.lines[self.i]))
        tag = "ol" if ordered else "ul"
        items = []
        while not self.done():
            line = self.lines[self.i]
            if not line.strip():
                # A blank line ends the list unless the next line continues it.
                nxt = self.peek(1)
                if not nxt or not (UL_RE.match(nxt) or OL_RE.match(nxt)):
                    break
                self.i += 1
                continue
            m = UL_RE.match(line) or OL_RE.match(line)
            if not m:
                break
            depth = len(m.group(1))
            if depth < indent:
                break
            if depth > indent:
                nested = self.list_block(indent=depth)
                if items and items[-1].endswith("</li>"):
                    items[-1] = items[-1][:-len("</li>")] + nested + "</li>"
                else:
                    items.append(f"<li>{nested}</li>")
                continue
            if bool(OL_RE.match(line)) != ordered:
                break
            content = m.groups()[-1]
            self.i += 1
            # A wrapped line belongs to the item it follows.
            while (not self.done() and self.lines[self.i].strip()
                   and not (UL_RE.match(self.lines[self.i]) or OL_RE.match(self.lines[self.i]))
                   and not HEADING_RE.match(self.lines[self.i])
                   and not FENCE_RE.match(self.lines[self.i])
                   and len(self.lines[self.i]) - len(self.lines[self.i].lstrip()) > indent):
                content += " " + self.lines[self.i].strip()
                self.i += 1
            items.append(f"<li>{_inline(content)}</li>")
        return f"<{tag}>{''.join(items)}</{tag}>"

    def paragraph(self):
        body = []
        while not self.done():
            line = self.lines[self.i]
            if (not line.strip() or HEADING_RE.match(line) or FENCE_RE.match(line)
                    or UL_RE.match(line) or OL_RE.match(line) or QUOTE_RE.match(line)
                    or RULE_RE.match(line) or self.at_table()):
                break
            body.append(line.strip())
            self.i += 1
        if body:
            self.html.append(f"<p>{_inline(' '.join(body))}</p>")


def render(text):
    """Markdown to safe HTML. Returns (html, headings) where headings drives the contents list."""
    renderer = _Renderer(text)
    html = renderer.render()
    return html, renderer.headings
