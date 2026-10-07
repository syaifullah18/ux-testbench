"""Prototype preflight: static checks on A/B prototypes before participants see them.

Broken assets, blocked requests and unlabeled controls otherwise surface only after
participants run into them, and they silently degrade click-path and first-click data. This is
static analysis of the uploaded HTML only (no browser), so anything a prototype builds with
script at runtime is invisible to it; findings that may come from that are warnings.

The label rule mirrors describe() in static/task-runner.js: data-testbench-label, then
aria-label, then visible text (an input that takes typing is labelled by placeholder or name).
"""
from collections import Counter
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlsplit

from .modules.ab_test import norm

ERROR, WARNING = "error", "warning"
SESSION_MINUTES_MAX = 60          # longer sessions tire participants; a warning, not an error
HTML_EXT = {".html", ".htm"}
INTERACTIVE = {"a", "button", "select", "input", "textarea", "summary"}
TYPED_INPUTS_EXCLUDED = {"button", "submit", "reset", "checkbox", "radio"}
VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "source", "track", "wbr"}

# What the app's Content-Security-Policy lets a prototype load from other hosts (see csp()).
CDN_HOSTS = {"script": {"cdn.tailwindcss.com"},
             "style": {"fonts.googleapis.com", "cdnjs.cloudflare.com"},
             "img": set()}


def finding(severity, file, message, fix, snippet=""):
    return {"severity": severity, "file": file, "message": message, "fix": fix, "snippet": snippet[:160]}


class _Page(HTMLParser):
    """Collects resources, links, interactive elements with their labels, goals and script text."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.resources = []        # (kind, url, snippet)
        self.links = []            # (url, snippet)
        self.controls = []         # {"tag", "label", "snippet"} once closed
        self.goals = set()
        self.scripts = 0
        self.script_text = []
        self._open = []            # stack of controls collecting visible text
        self._in_script = False

    def handle_starttag(self, tag, attrs):
        a = {k: (v or "") for k, v in attrs}
        snippet = self.get_starttag_text() or f"<{tag}>"
        if tag == "script":
            self.scripts += 1
            self._in_script = True
            if a.get("src"):
                self.resources.append(("script", a["src"], snippet))
        elif tag == "link" and "stylesheet" in a.get("rel", "").lower() and a.get("href"):
            self.resources.append(("style", a["href"], snippet))
        elif tag in ("img", "source") and a.get("src"):
            self.resources.append(("img", a["src"], snippet))
        if tag == "a" and a.get("href"):
            self.links.append((a["href"], snippet))
        if tag == "form" and a.get("action"):
            self.links.append((a["action"], snippet))
        if a.get("data-testbench-goal"):
            self.goals.add(a["data-testbench-goal"])

        interactive = (tag in INTERACTIVE or "onclick" in a or a.get("role") == "button"
                       or "data-testbench-label" in a)
        if not interactive:
            if tag not in VOID:
                self._open.append(None)   # keep the stack aligned with the tree
            return
        label = a.get("data-testbench-label") or a.get("aria-label")
        if not label and tag in ("input", "textarea"):
            typed = tag == "textarea" or a.get("type", "text").lower() not in TYPED_INPUTS_EXCLUDED
            label = (a.get("placeholder") or a.get("name")) if typed else a.get("value")
        control = {"tag": tag, "label": label or "", "text": [], "snippet": snippet, "fixed": bool(label)}
        if tag in VOID:
            self._close(control)
        else:
            self._open.append(control)

    def handle_endtag(self, tag):
        if tag == "script":
            self._in_script = False
        if tag in VOID or not self._open:
            return
        control = self._open.pop()
        if control is not None:
            self._close(control)

    def _close(self, control):
        if not control["fixed"]:
            control["label"] = " ".join("".join(control["text"]).split())
        self.controls.append({"tag": control["tag"], "label": control["label"][:80], "snippet": control["snippet"]})

    def handle_data(self, data):
        if self._in_script:
            self.script_text.append(data)
            return
        for c in self._open:
            if c is not None and not c["fixed"]:
                c["text"].append(data)


def _parse(path):
    page = _Page()
    try:
        page.feed(path.read_text(encoding="utf-8", errors="replace"))
        page.close()
    except Exception:   # malformed HTML: report what was gathered so far
        pass
    return page


def _pages(entry, exclude=()):
    """The entry page plus every HTML page in its folder tree (a prototype may link between
    pages), leaving out other variants' entry pages when variants share a folder."""
    skip = {Path(x).resolve() for x in exclude} | {entry.resolve()}
    others = sorted(p for p in entry.parent.rglob("*") if p.suffix.lower() in HTML_EXT and p.resolve() not in skip)
    return [entry] + others


def check_variant(project_dir, entry, local_assets, first_click_targets=(), goal_ids=(), exclude=()):
    """Findings for one variant's prototype."""
    out = []
    project_dir = Path(project_dir).resolve()
    rel = lambda p: p.resolve().relative_to(project_dir).as_posix() if project_dir in p.resolve().parents else p.name
    if entry.suffix.lower() not in HTML_EXT:
        return out
    labels, goals, script_text, has_scripts = set(), set(), [], False
    for page_path in _pages(entry, exclude):
        page = _parse(page_path)
        f = rel(page_path)
        has_scripts |= page.scripts > 0
        goals |= page.goals
        script_text += page.script_text
        for kind, url, snippet in page.resources:
            u = urlsplit(url)
            if u.scheme in ("data", "blob"):
                continue
            if u.scheme in ("http", "https") or url.startswith("//"):
                host = u.hostname or ""
                if local_assets or host not in CDN_HOSTS.get(kind, set()):
                    out.append(finding(ERROR, f, f"Loads {kind} from {host}, which the app blocks for participants",
                                       "Copy the file into the prototype folder and link it relatively", snippet))
                else:
                    out.append(finding(WARNING, f, f"Loads {kind} from {host}; this breaks if LOCAL_ASSETS=1 is set",
                                       "Copy the file into the prototype folder and link it relatively", snippet))
                continue
            target = (page_path.parent / u.path).resolve() if u.path else None
            if target is not None and not target.is_file():
                out.append(finding(ERROR, f, f"{kind.capitalize()} '{u.path}' is not in the upload",
                                   "Upload the file next to the page, or fix the path", snippet))
        for url, snippet in page.links:
            u = urlsplit(url)
            if u.scheme in ("http", "https", "mailto", "tel") or url.startswith("//"):
                out.append(finding(WARNING, f, f"Link or form leaves the prototype ({url[:60]}); the runner blocks it",
                                   "Point it at a page inside the prototype, or use '#'", snippet))
        seen = Counter()
        for c in page.controls:
            if not c["label"].strip():
                out.append(finding(WARNING, f, f"<{c['tag']}> has no label, so click paths record only the tag",
                                   "Add visible text, aria-label or data-testbench-label", c["snippet"]))
                continue
            labels.add(norm(c["label"]))
            seen[norm(c["label"])] += 1
        for c in page.controls:
            key = norm(c["label"])
            if key and seen[key] > 1:
                out.append(finding(WARNING, f, f"Label '{c['label']}' is used by {seen[key]} controls on this page",
                                   "Give each a distinct data-testbench-label", c["snippet"]))
                seen[key] = 0   # report each duplicate label once

    f = rel(entry)
    for target in first_click_targets:
        if norm(target) not in labels:
            severity = WARNING if has_scripts else ERROR
            out.append(finding(severity, f, f"First-click target '{target}' does not match any control label"
                               + (" in the static HTML (script may add it)" if has_scripts else ""),
                               "Match the target to a label, or add data-testbench-label to the element"))
    js = "\n".join(script_text) + "\n" + "\n".join(
        p.read_text(encoding="utf-8", errors="replace") for p in entry.parent.rglob("*.js"))
    for g in goal_ids:
        if g not in goals and g not in js:
            out.append(finding(WARNING, f, f"Goal '{g}' is not on any data-testbench-goal and not in the prototype's script",
                               f"Add data-testbench-goal=\"{g}\" to the element that completes the task"))
    return out


def run(project, local_assets=False):
    """All findings for a project: every ab_test variant and journey step, plus the session length."""
    out = []
    for m in project.modules.values():
        for key, v in (m.conf["variants"].items() if m.type == "ab_test" else []):
            targets = sorted({x for t in m.conf["tasks"] for x in ((t.get("first_click") or {}).get(key) or [])})
            goals = sorted({g for t in m.conf["tasks"] if t.get("goals") for g in t["goals"]["success"] + t["goals"]["failure"]})
            others = [o["path"] for k2, o in m.conf["variants"].items() if k2 != key]
            for fd in check_variant(project.dir, v["path"], local_assets, targets, goals, exclude=others):
                fd["module"], fd["variant"] = m.id, key
                out.append(fd)
        if m.type == "journey_test":
            # Steps of one flow often share a folder: each step is checked as its own page.
            for key, v in m.conf["variants"].items():
                for st in v["steps"]:
                    others = [o["path"] for vv in m.conf["variants"].values() for o in vv["steps"] if o["path"] != st["path"]]
                    for fd in check_variant(project.dir, st["path"], local_assets, (), st["goals"]["success"],
                                            exclude=others):
                        fd["module"], fd["variant"] = m.id, f"{key}/{st['id']}"
                        out.append(fd)
    total = sum(m.minutes or 0 for m in project.modules.values())
    if total > SESSION_MINUTES_MAX:
        out.append(finding(WARNING, "project.yaml", f"Modules add up to about {total} minutes; over {SESSION_MINUTES_MAX} tires participants",
                           "Split the study or shorten modules"))
    return out


def summary(findings):
    counts = Counter(f["severity"] for f in findings)
    return counts[ERROR], counts[WARNING]
