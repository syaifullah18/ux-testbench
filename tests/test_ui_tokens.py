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
