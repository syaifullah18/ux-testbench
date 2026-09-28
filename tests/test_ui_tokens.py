"""Guards against styling that silently does nothing.

Tailwind drops a class it has no definition for, without warning: the page still renders, it is
just missing the colour or the spacing the author intended. `px-4.5` and `brand-950` both got
into the templates that way. These tests read the templates as text, so they need no browser.
"""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TEMPLATES = sorted((ROOT / "testbench" / "templates").rglob("*.html"))

# Tailwind's default spacing scale. Anything else must be an arbitrary value in [brackets] or be
# added to the theme in both templates/_head.html and tailwind.config.js.
VALID_SPACING = {
    "0", "0.5", "1", "1.5", "2", "2.5", "3", "3.5", "4", "5", "6", "7", "8", "9", "10", "11",
    "12", "14", "16", "18", "20", "24", "28", "32", "36", "40", "44", "48", "52", "56", "60",
    "64", "72", "80", "96", "px", "auto", "full", "screen", "min", "max", "fit", "svh", "dvh",
}
SPACING_RE = re.compile(r"\b(?:p|m)[xytrbl]?-(\d+\.5|\d+)\b|\b(?:gap|space-[xy]|h|w)-(\d+\.5)\b")

BRAND_RE = re.compile(r"\bbrand-(\d{2,3})\b")
DEFINED_BRAND = {"50", "100", "200", "300", "400", "500", "600", "700", "800", "900", "950"}


def test_no_undefined_spacing_steps():
    problems = []
    for path in TEMPLATES:
        for line_no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            for match in SPACING_RE.finditer(line):
                value = match.group(1) or match.group(2)
                if value not in VALID_SPACING:
                    problems.append(f"{path.relative_to(ROOT)}:{line_no} {match.group(0)}")
    assert not problems, "undefined spacing classes render as nothing:\n  " + "\n  ".join(problems)


def test_every_brand_step_used_is_defined():
    head = (ROOT / "testbench" / "templates" / "_head.html").read_text()
    config = (ROOT / "tailwind.config.js").read_text()
    used = set()
    for path in TEMPLATES:
        used |= set(BRAND_RE.findall(path.read_text(encoding="utf-8")))
    undefined = used - DEFINED_BRAND
    assert not undefined, f"brand steps used but not defined: {sorted(undefined)}"
    for step in used:
        assert f"--brand-{step}:" in head, f"--brand-{step} is never emitted by _head.html"
        assert f"--brand-{step}" in config or "map(" in config, f"{step} missing from the build config"


def test_only_the_landing_page_defines_its_own_head():
    """Every page takes its stylesheets from _head.html, so LOCAL_ASSETS applies everywhere and
    one palette serves the whole app."""
    for path in TEMPLATES:
        text = path.read_text(encoding="utf-8")
        if "cdn.tailwindcss.com" in text:
            assert path.name == "_head.html", f"{path.relative_to(ROOT)} loads Tailwind itself"


def test_no_interpolated_utility_classes():
    """A class built by string interpolation never appears in the source Tailwind scans, so the
    compiled build purges it and the element silently loses its colour. Write full class names
    into the data instead."""
    bad = []
    for path in TEMPLATES:
        for line_no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            for m in re.finditer(r'class="[^"]*?\b(?:text|bg|border|from|to|via)-\{\{', line):
                bad.append(f"{path.relative_to(ROOT)}:{line_no} {m.group(0)[:60]}")
    assert not bad, "interpolated class names get purged from the compiled build:\n  " + "\n  ".join(bad)


def test_landing_page_uses_surface_tokens_not_hand_written_dark_variants():
    """The tokens are what keep the two themes in step. A page that reaches for `dark:` on every
    surface is how a dark card ends up with a `text-slate-900` heading nobody can read."""
    landing = (ROOT / "testbench" / "templates" / "public" / "landing.html").read_text()
    for token in ("bg-surface", "text-ink", "border-line", "bg-card"):
        assert token in landing, f"landing page does not use {token}"
    # A handful of genuine one-offs is fine; a hundred means the tokens are being bypassed.
    assert landing.count("dark:") < 40, f"{landing.count('dark:')} dark: variants — use the tokens"


# Everything below guards the surface tokens. The two themes are one set of markup driven by
# `--surface`/`--ink`/`--line`, so a literal slate or white in a themed surface is not a style
# choice — it is a page that stops flipping, and it shows up as unreadable text on the dark
# theme rather than as anything that looks wrong in review.

SLATE_RE = re.compile(r"\b(?:text|bg|border|divide|ring)-slate-\d{2,3}\b(?!/)")

# The literal colours that survive on purpose, with the reason each one does.
SLATE_ALLOWED = {
    # The self-host terminal and the docs code blocks are a dark console in both themes.
    ("public/landing.html", "bg-slate-950"),
    ("public/landing.html", "text-slate-400"),
    ("public/landing.html", "text-slate-300"),
    ("public/landing.html", "shadow-slate-900/5"),
    ("public/doc.html", "bg-slate-950"),
    ("public/doc.html", "text-slate-200"),
    # _head.html names the class in prose, as the example of what not to write.
    ("_head.html", "text-slate-900"),
}


def test_no_literal_slate_outside_the_allowed_one_offs():
    problems = []
    for path in TEMPLATES:
        rel = str(path.relative_to(ROOT / "testbench" / "templates"))
        for line_no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            for match in SLATE_RE.finditer(line):
                if (rel, match.group(0)) in SLATE_ALLOWED:
                    continue
                problems.append(f"{rel}:{line_no} {match.group(0)}")
    assert not problems, (
        "literal slate colours do not flip with the theme — use the surface tokens:\n  "
        + "\n  ".join(problems))


def test_brand_utilities_name_a_step_that_exists():
    """`bg-brand-dark` rendered as nothing for as long as it was in the admin dashboard. Only
    `brand` itself and the numbered steps are defined."""
    bad = []
    suffix = re.compile(r"\b(?:text|bg|border|ring|divide|from|to|via)-brand-([a-z][a-z-]*)\b")
    for path in TEMPLATES:
        for line_no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            for match in suffix.finditer(line):
                bad.append(f"{path.relative_to(ROOT)}:{line_no} {match.group(0)}")
    assert not bad, "brand utilities must use a numbered step:\n  " + "\n  ".join(bad)


def test_translucent_overlays_stay_literal_white():
    """`bg-white/10` on the coloured app bar is an overlay, not a themed surface. Swapping it for
    `bg-card/10` makes the pill vanish on the dark theme, where card is already near-black."""
    bad = []
    for path in TEMPLATES:
        for line_no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            for match in re.finditer(r"\bbg-card/\d+\b", line):
                bad.append(f"{path.relative_to(ROOT)}:{line_no} {match.group(0)}")
    assert not bad, "a translucent overlay must stay bg-white/N:\n  " + "\n  ".join(bad)


def test_state_tints_carry_their_state_into_the_dark_variant():
    """`hover:bg-brand-50 dark:bg-brand-950` reads like a pair but is not one: the dark half has
    no state, so it paints every card all the time and the selected answer stops being visible on
    the dark theme. A bare `dark:` tint is only correct when a bare light one sits beside it."""
    bare_dark = re.compile(r"(?<![:\w-])dark:bg-brand-(?:900|950)\b")
    bare_light = re.compile(r"(?<![:\w-])bg-brand-(?:50|100)\b")
    has_state = re.compile(r"(?<![:\w-])(?:hover|has-\[:checked\]):bg-brand-\d")

    problems = []
    for path in TEMPLATES:
        for line_no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if not has_state.search(line) or bare_light.search(line):
                continue
            for match in bare_dark.finditer(line):
                problems.append(f"{path.relative_to(ROOT)}:{line_no} {match.group(0)}")
    assert not problems, (
        "a dark variant of a state tint must repeat the state:\n  " + "\n  ".join(problems))
