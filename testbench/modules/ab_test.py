"""Within-subject comparison of two or more static prototypes.

Each participant works through the same timed tasks on every variant, in a counterbalanced
order, then answers a short survey per variant and an optional final survey. Prototypes are
served under neutral URLs (view/1, view/2) so the file name never hints which is which.
"""
import math
import random
import re
import statistics
from collections import Counter


def sign_test(wins, losses):
    n = wins + losses
    k = max(wins, losses)
    if n == 0:
        return 1.0
    return min(1.0, 2 * sum(math.comb(n, i) for i in range(k, n + 1)) / 2 ** n)


WILCOXON_EXACT_MAX = 25   # exact distribution up to this many non-zero pairs, normal approximation above


def wilcoxon(differences):
    """Two-sided Wilcoxon signed-rank test on paired differences.

    Zero differences are dropped (Wilcoxon's method); tied |d| share their average rank. Up to
    WILCOXON_EXACT_MAX pairs the p-value is exact, from the permutation distribution of W+ given
    those ranks; above it, the normal approximation with tie and continuity corrections.
    Returns {n, w_plus, w_minus, p, r} where r is the matched-pairs rank-biserial correlation
    (+1: every difference positive)."""
    d = [x for x in differences if x != 0]
    n = len(d)
    if n == 0:
        return {"n": 0, "w_plus": 0.0, "w_minus": 0.0, "p": 1.0, "r": 0.0, "exact": True}
    order = sorted(range(n), key=lambda i: abs(d[i]))
    ranks = [0.0] * n
    i = 0
    while i < n:
        j = i
        while j + 1 < n and abs(d[order[j + 1]]) == abs(d[order[i]]):
            j += 1
        for k in range(i, j + 1):
            ranks[order[k]] = (i + j) / 2 + 1
        i = j + 1
    w_plus = sum(r for r, x in zip(ranks, d) if x > 0)
    total = n * (n + 1) / 2
    w_minus = total - w_plus
    if n <= WILCOXON_EXACT_MAX:
        # Ranks are whole or half numbers; doubling makes them integers for the counting table.
        doubled = [int(round(2 * r)) for r in ranks]
        top = sum(doubled)
        counts = [0] * (top + 1)
        counts[0] = 1
        for r in doubled:
            for v in range(top, r - 1, -1):
                counts[v] += counts[v - r]
        observed = int(round(2 * min(w_plus, w_minus)))
        tail = sum(counts[: observed + 1]) / 2 ** n
        p, exact = min(1.0, 2 * tail), True
    else:
        mu = total / 2
        ties = {}
        for r in ranks:
            ties[r] = ties.get(r, 0) + 1
        var = n * (n + 1) * (2 * n + 1) / 24 - sum(t ** 3 - t for t in ties.values()) / 48
        z = (abs(w_plus - mu) - 0.5) / math.sqrt(var) if var > 0 else 0.0
        p, exact = min(1.0, math.erfc(max(z, 0) / math.sqrt(2))), False
    return {"n": n, "w_plus": w_plus, "w_minus": w_minus, "p": p, "r": (w_plus - w_minus) / total, "exact": exact}


def bootstrap_interval(differences, iterations=1000):
    if not differences:
        return None, None
    n = len(differences)
    means = []
    for _ in range(iterations):
        sample = [random.choice(differences) for _ in range(n)]
        means.append(sum(sample) / n)
    means.sort()
    return means[int(iterations * 0.025)], means[int(iterations * 0.975)]


def paired_time_check(pairs, base, k, test="sign"):
    """Sign test by default; with test='wilcoxon' the signed-rank p-value decides instead."""
    faster = sum(1 for p in pairs if p[k]["total"] < p[base]["total"])
    slower = sum(1 for p in pairs if p[k]["total"] > p[base]["total"])
    time_p = sign_test(faster, slower)
    if test == "wilcoxon":
        time_p = wilcoxon([p[base]["total"] - p[k]["total"] for p in pairs])["p"]
    if time_p < 0.05:
        verdict = "evidence for" if faster > slower else "evidence against"
        ok = faster > slower
    else:
        verdict = "not enough evidence yet"
        ok = False
    return faster, time_p, verdict, ok


def paired_success_check(pairs, base, k):
    srb = success_rate([effective_grade(x) for p in pairs for x in p[base]["rows"].values()])
    srk = success_rate([effective_grade(x) for p in pairs for x in p[k]["rows"].values()])
    diffs = []
    for p in pairs:
        sb_i = success_rate([effective_grade(x) for x in p[base]["rows"].values()])
        sk_i = success_rate([effective_grade(x) for x in p[k]["rows"].values()])
        if sb_i is not None and sk_i is not None:
            diffs.append(sk_i - sb_i)
    if not diffs:
        return srb, srk, None, None, "not enough evidence yet", False

    lo, hi = bootstrap_interval(diffs)
    if lo > 0:
        verdict = "evidence for"
        ok = True
    elif hi < 0:
        verdict = "evidence against"
        ok = False
    else:
        verdict = "not enough evidence yet"
        ok = False
    return srb, srk, lo, hi, verdict, ok


def paired_survey_check(pairs, base, k, by_variant, survey_questions, min_gain):
    if not survey_questions:
        return None
    sb = [by_variant[base]["survey"].get(q) for q in survey_questions]
    sk = [by_variant[k]["survey"].get(q) for q in survey_questions]
    sb, sk = mean(sb), mean(sk)
    gain = (sk - sb) if sk is not None and sb is not None else None

    diffs = []
    for p in pairs:
        sb_i = mean([p[base]["survey"].get(q) for q in survey_questions])
        sk_i = mean([p[k]["survey"].get(q) for q in survey_questions])
        if sb_i is not None and sk_i is not None:
            diffs.append(sk_i - sb_i)
    if not diffs:
        return sb, sk, gain, None, None, "not enough evidence yet", False

    lo, hi = bootstrap_interval(diffs)
    if lo > min_gain:
        verdict = "evidence for"
        ok = True
    elif hi < min_gain:
        verdict = "evidence against"
        ok = False
    else:
        verdict = "not enough evidence yet"
        ok = False
    return sb, sk, gain, lo, hi, verdict, ok

import urllib.parse
from flask import abort, jsonify, render_template, request, send_from_directory, session

from .. import questions as Q
from .. import db, locks, storage
from ..heatmap import MOBILE_MAX_W
from ..config import _safe_regex
from .base import ADVANCE, ModuleType

ORDERS = {"rotate", "code_parity", "random", "fixed"}
CLICK_PATH_MAX = 40
EVENTS_MAX = 200           # prototype events (goals, pages, errors, misclicks) kept per task
EVENT_KINDS = {"goal", "page", "error", "miss"}
GOAL_RE = re.compile(r"^[A-Za-z0-9_.:-]{1,80}$")
GRADES = ("success", "assisted", "fail")


def norm(text):
    return re.sub(r"[^0-9A-Z]", "", str(text or "").upper())


def to_int(value, lo=0, hi=None):
    try:
        n = int(value)
    except (TypeError, ValueError):
        return lo
    n = max(lo, n)
    return min(n, hi) if hi is not None else n


def mean(values):
    values = [v for v in values if v is not None]
    return sum(values) / len(values) if values else None


def median(values):
    values = [v for v in values if v is not None]
    return statistics.median(values) if values else None


def effective_grade(row):
    """Coordinator grade wins. Without one, giving up is a failure and the answer key decides
    the rest. Tasks without a key stay ungraded until the coordinator grades them."""
    if row["grade"]:
        return row["grade"]
    if row["gave_up"]:
        return "fail"
    if row["auto_pass"] is None:
        return None
    return "success" if row["auto_pass"] else "fail"


def success_rate(grades):
    graded = [g for g in grades if g]
    return (sum(1 for g in graded if g in ("success", "assisted")) / len(graded)) if graded else None


def goal_outcome(task, events):
    """'success' or 'failure' for the first listed goal the participant reached, else None.
    Goals the task does not list are recorded but never decide the outcome."""
    goals = task.get("goals")
    if not goals:
        return None
    for e in events or []:
        if e.get("kind") != "goal":
            continue
        if e.get("value") in goals["success"]:
            return "success"
        if e.get("value") in goals["failure"]:
            return "failure"
    return None


LABEL_PREFIX = re.compile(r"^[a-z][a-z0-9]*: ")


def click_label(recorded):
    """'button: Save draft' -> 'Save draft'. The runner records '<tag>: <label>'."""
    return LABEL_PREFIX.sub("", str(recorded or ""), count=1)


def first_click_correct(task, variant, recorded):
    """True/False when the task names first-click targets for this variant, else None."""
    targets = (task.get("first_click") or {}).get(variant)
    if not targets:
        return None
    return bool(recorded) and norm(click_label(recorded)) in {norm(x) for x in targets}


def paired_first_click_check(pairs, base, k, tasks):
    """Per person, count tasks whose first click hit a target on each variant; the exact sign
    test runs on people who did better on one variant. Shown next to the rule, never part of it."""
    fc = [t for t in tasks if (t.get("first_click") or {}).get(base) and (t.get("first_click") or {}).get(k)]
    if not fc or not pairs:
        return None
    better = worse = hits_b = hits_k = 0
    for p in pairs:
        sb = sum(bool(first_click_correct(t, base, p[base]["rows"][t["id"]]["first_click"])) for t in fc)
        sk = sum(bool(first_click_correct(t, k, p[k]["rows"][t["id"]]["first_click"])) for t in fc)
        better += sk > sb
        worse += sk < sb
        hits_b += sb
        hits_k += sk
    p_value = sign_test(better, worse)
    if p_value < 0.05:
        verdict = "evidence for" if better > worse else "evidence against"
    else:
        verdict = "not enough evidence yet"
    n = len(pairs) * len(fc)
    return {"key": "first_click", "a": hits_b / n, "b": hits_k / n, "value": f"{better}/{better + worse}",
            "p_value": p_value, "verdict": verdict, "ok": verdict == "evidence for", "counts": False}


def rework(task, events):
    """Confusion and rework from a task's events.

    back: returns to a page or state already visited in this task (repeats in a row are one visit).
    errors: validation errors the prototype reported. misclicks: clicks on no control.
    lostness (Smith 1996): sqrt((N/S - 1)^2 + (R/N - 1)^2) with S pages visited, N distinct pages
    and R the task's optimal_steps; 0 is a perfect path, above about 0.4 people look lost."""
    events = events or []
    pages = [e["value"] for e in events if e.get("kind") == "page"]
    seq = [x for i, x in enumerate(pages) if i == 0 or x != pages[i - 1]]
    back = sum(1 for i, x in enumerate(seq) if x in seq[:i])
    lost = None
    if task.get("optimal_steps") and seq:
        n_s, n_n, r = len(seq), len(set(seq)), task["optimal_steps"]
        lost = math.sqrt((n_n / n_s - 1) ** 2 + (r / n_n - 1) ** 2)
    return {"pages": len(seq), "back": back, "lostness": lost,
            "errors": sum(1 for e in events if e.get("kind") == "error"),
            "misclicks": sum(1 for e in events if e.get("kind") == "miss")}


REWORK_KEYS = ("back", "misclicks", "errors", "lostness")


def paired_rework(pairs, base, k, tasks):
    """Per metric: paired Wilcoxon on each person's total (lostness: mean over tasks), base minus
    challenger, so a positive r means fewer on the challenger."""
    out = {}
    for key in REWORK_KEYS:
        def person_value(run):
            vals = [rework(t, run["events"].get(t["id"]))[key] for t in tasks]
            vals = [v for v in vals if v is not None]
            if not vals:
                return None
            return sum(vals) / len(vals) if key == "lostness" else sum(vals)
        diffs = []
        for p in pairs:
            vb, vk = person_value(p[base]), person_value(p[k])
            if vb is not None and vk is not None:
                diffs.append(vb - vk)
        out[key] = dict(wilcoxon(diffs), pairs=len(diffs)) if diffs else None
    return out


def rework_cells(task, events):
    rw = rework(task, events)
    return [rw["pages"], rw["back"], rw["misclicks"], rw["errors"],
            "" if rw["lostness"] is None else round(rw["lostness"], 3)]


def fc_cell(ok):
    return "" if ok is None else int(ok)


def preset_stats(q, scores):
    scores = [x for x in scores if x is not None]
    if not scores:
        return {"n": 0, "mean": None, "median": None, "interval": (None, None), "grade": None, "sus_equiv": None}
    m = sum(scores) / len(scores)
    sus_like = m if q["preset"] == "sus" else Q.umux_lite_to_sus(m)
    return {"n": len(scores), "mean": m, "median": statistics.median(scores), "interval": bootstrap_interval(scores),
            "grade": Q.sus_grade(sus_like), "sus_equiv": None if q["preset"] == "sus" else sus_like}


def first_goal_ms(task, events):
    """Task time at the first listed goal, the moment the timer stops with end_on_goal."""
    goals = task.get("goals") or {}
    listed = set(goals.get("success", [])) | set(goals.get("failure", []))
    for e in events or []:
        if e.get("kind") == "goal" and e.get("value") in listed:
            return e.get("t_ms")
    return None


ENTRY_EXT = {
    ".html", ".htm",
    ".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp", ".avif", ".ico", ".bmp", ".tif", ".tiff",
    ".pdf",
    ".mp4", ".webm", ".mov", ".ogg",
    ".mp3", ".wav", ".m4a",
    ".txt", ".md"
}


class ABTest(ModuleType):
    type_name = "ab_test"

    # ------------------------------------------------------------ config

    def validate(self, raw, scope):
        project_dir = self.m.project.dir.resolve()
        variants = {}
        raw_variants = raw.get("variants")
        if not isinstance(raw_variants, dict) or len(raw_variants) < 1:
            scope.add("variants must be a mapping of key -> {label, file}")
            raw_variants = {}
        for key, v in raw_variants.items():
            key = str(key)
            if not re.match(r"^[A-Za-z0-9_-]{1,20}$", key):
                scope.add(f"variant key '{key}' must be short letters/digits")
            v = v or {}
            path = (project_dir / str(v.get("file", ""))).resolve()
            if not v.get("file") or not path.is_file():
                scope.add(f"variants.{key}.file '{v.get('file')}' does not exist under {project_dir.name}/")
            elif project_dir not in path.parents:
                scope.add(f"variants.{key}.file must stay inside the project folder")
            elif path.parent == project_dir:
                scope.add(f"variants.{key}.file must not sit at the project root")
            elif path.suffix.lower() not in ENTRY_EXT:
                scope.add(f"variants.{key}.file must be a page, document, or media file")
            variants[key] = {"label": str(v.get("label") or key), "path": path}
        keys = list(variants)
        baseline = str(raw.get("baseline") or (keys[0] if keys else ""))
        if keys and baseline not in variants:
            scope.add(f"baseline '{baseline}' is not a variant")
        order = raw.get("order", "rotate")
        if order not in ORDERS:
            scope.add(f"order must be one of {sorted(ORDERS)}")

        tasks = []
        for i, t in enumerate(raw.get("tasks") or []):
            w = f"tasks[{i}]"
            if not isinstance(t, dict) or not t.get("id") or not t.get("prompt"):
                scope.add(f"{w}: needs id and prompt")
                continue
            fields = []
            for f in t.get("fields") or []:
                kind = f.get("kind", "text")
                if kind not in ("text", "choice"):
                    scope.add(f"{w}.fields.{f.get('id')}: kind must be text or choice")
                field = {"id": str(f.get("id")), "label": str(f.get("label") or f.get("id")), "kind": kind,
                         "placeholder": str(f.get("placeholder") or "")}
                if kind == "choice":
                    field["options"] = [str(o) for o in f.get("options") or []]
                    if not field["options"]:
                        scope.add(f"{w}.fields.{field['id']}: choice needs options")
                fields.append(field)
            goals = self._goals(t.get("goals"), scope, w)
            first_click = self._first_click(t.get("first_click"), variants, scope, w)
            optimal = t.get("optimal_steps")
            if optimal is not None and (not isinstance(optimal, int) or isinstance(optimal, bool) or optimal < 1):
                scope.add(f"{w}.optimal_steps must be a whole number of pages, 1 or more")
                optimal = None
            if not fields:
                fields = [] if goals else [{"id": "answer", "label": "", "kind": "text", "placeholder": ""}]
            accept = t.get("accept") or {}
            for fid in accept:
                if fid not in {f["id"] for f in fields}:
                    scope.add(f"{w}.accept.{fid}: no field with that id")
            tasks.append({"id": str(t["id"]), "title": str(t.get("title") or t["id"]), "prompt": str(t["prompt"]),
                          "fields": fields, "accept": {k: [str(x) for x in v] for k, v in accept.items()},
                          "contains": [str(x) for x in t.get("contains") or []], "probe": str(t.get("probe") or ""),
                          "goals": goals, "end_on_goal": bool(t.get("end_on_goal", True)) if goals else False,
                          "first_click": first_click, "optimal_steps": optimal})
        if not tasks:
            scope.add("tasks must list at least one task")
        if len({t["id"] for t in tasks}) != len(tasks):
            scope.add("task ids must be unique")

        segments = {}
        raw_seg = raw.get("segments") or {}
        if not isinstance(raw_seg, dict):
            scope.add("segments must map a group name to an identity pattern (regex)")
            raw_seg = {}
        for name, pattern in raw_seg.items():
            _, err = _safe_regex(str(pattern))
            if err:
                scope.add(f"segments.{name}: {err}")
            else:
                segments[str(name)] = str(pattern)
        post = Q.normalize(raw.get("post_survey") or [], scope, "post_survey", locale=getattr(self.m.project, "locale", None))
        final = Q.normalize(raw.get("final_survey") or [], scope, "final_survey", locale=getattr(self.m.project, "locale", None))
        rule = raw.get("decision_rule") or {}
        time_test = str(rule.get("time_test", "sign"))
        if time_test not in ("sign", "wilcoxon"):
            scope.add("decision_rule.time_test must be sign or wilcoxon")
            time_test = "sign"
        for qid in rule.get("survey_questions") or []:
            if qid not in {q["id"] for q in post if q["type"] == "scale"}:
                scope.add(f"decision_rule.survey_questions: '{qid}' is not a scale question in post_survey")
        return {
            "variants": variants, "baseline": baseline, "order": order, "intro": str(raw.get("intro") or ""),
            "segments": segments,
            "tasks": tasks, "ease": bool(raw.get("ease_question", True)), "post": post, "final": final,
            "preference": bool(raw.get("preference", len(variants) > 1)),
            # planned_participants is the pre-registered sample size; before it is reached every
            # verdict is interim. min_participants is the older name for the same number.
            "rule": {"min_participants": int(rule.get("planned_participants", rule.get("min_participants", 20))),
                     "time_test": time_test,
                     "survey_questions": list(rule.get("survey_questions") or []),
                     "min_survey_gain": float(rule.get("min_survey_gain", 0.5))},
        }

    @staticmethod
    def _goals(raw, scope, where):
        """`goals: {success: [...], failure: [...]}` -> the same with clean ids, or None."""
        if raw is None:
            return None
        if not isinstance(raw, dict) or not set(raw) <= {"success", "failure"}:
            scope.add(f"{where}.goals must be a mapping with success and/or failure lists")
            return None
        out = {}
        for k in ("success", "failure"):
            ids = raw.get(k) or []
            if isinstance(ids, str):
                ids = [ids]
            out[k] = [str(x) for x in ids]
            for g in out[k]:
                if not GOAL_RE.match(g):
                    scope.add(f"{where}.goals.{k}: '{g}' must be 1 to 80 letters, digits, - _ . or :")
        if not out["success"]:
            scope.add(f"{where}.goals.success must list at least one goal id")
        if set(out["success"]) & set(out["failure"]):
            scope.add(f"{where}.goals: a goal id cannot be both success and failure")
        return out

    @staticmethod
    def _first_click(raw, variants, scope, where):
        """`first_click: {A: [label, ...], B: [...]}` -> {variant: [label, ...]}, or None."""
        if raw is None:
            return None
        if not isinstance(raw, dict):
            scope.add(f"{where}.first_click must map variant keys to lists of element labels")
            return None
        out = {}
        for key, labels in raw.items():
            key = str(key)
            if key not in variants:
                scope.add(f"{where}.first_click.{key}: no variant with that key")
                continue
            labels = [labels] if isinstance(labels, str) else list(labels or [])
            out[key] = [str(x) for x in labels if str(x).strip()]
            if not out[key]:
                scope.add(f"{where}.first_click.{key} must list at least one element label")
        return out or None

    def check_refs(self, scope):
        c = self.m.conf
        for qs in (c.get("post", []), c.get("final", [])):
            Q.check_refs(qs, scope, self.m.project, self.m.id, self.question_ids())

    def question_ids(self):
        return {q["id"] for q in self.m.conf.get("final", [])}

    # ------------------------------------------------------------ flow

    def on_start(self, ctx):
        keys = list(self.m.conf["variants"])
        how = self.m.conf["order"]
        if how == "random":
            order = random.sample(keys, len(keys))
        elif how == "fixed":
            order = keys
        else:
            if how == "code_parity":
                digits = re.findall(r"\d+", ctx.participant["identity"])
                k = int(digits[-1]) - 1 if digits else 0
            else:  # rotate: next row of a cyclic Latin square
                k = ctx.conn.execute("SELECT COUNT(*) FROM sessions WHERE module_id = ?", (self.m.id,)).fetchone()[0]
            k %= len(keys)
            order = keys[k:] + keys[:k]
        return {"order": order}

    def steps(self, state):
        out = ["intro"]
        for n in range(1, len(state["order"]) + 1):
            out += [f"brief{n}", f"tasks{n}"] + ([f"survey{n}"] if self.m.conf["post"] else [])
        if self.m.conf["final"] or (self.m.conf["preference"] and len(state["order"]) > 1):
            out.append("final")
        return out

    def step_label(self, step, t):
        if step == "intro":
            return t("ab.step_intro")
        if step == "final":
            return t("ab.step_final")
        kind, n = re.match(r"([a-z]+)(\d+)", step).groups()
        return t(f"ab.step_{kind}", n=n)

    def final_questions(self, ctx):
        qs = list(self.m.conf["final"])
        order = getattr(ctx, "state", {}).get("order", []) if isinstance(getattr(ctx, "state", None), dict) else []
        if self.m.conf["preference"] and len(order) > 1:
            opts = [(str(i), ctx.t("ab.view_n", n=i)) for i in range(1, len(order) + 1)] + [("none", ctx.t("ab.no_preference"))]
            qs = [{"id": "_preference", "type": "single", "label": ctx.t("ab.preference_q"), "hint": "",
                   "required": True, "show_if": None, "options": opts, "columns": min(3, len(opts))},
                  {"id": "_preference_reason", "type": "text", "label": ctx.t("ab.preference_why"), "hint": "",
                   "required": False, "show_if": None, "long": True, "max_length": 2000, "placeholder": ""}] + qs
        return qs

    def handle(self, ctx, step):
        c = self.m.conf
        n_views = len(ctx.state["order"])
        if step == "intro":
            if request.method == "POST":
                return ADVANCE
            return ctx.render("modules/ab_intro.html", intro=c["intro"], n_views=n_views, n_tasks=len(c["tasks"]))
        if step == "final" or step.startswith("survey"):
            qs = self.final_questions(ctx) if step == "final" else c["post"]
            answers, errors = storage.page_answers(ctx.conn, ctx.session["id"], step), {}
            if request.method == "POST":
                answers, errors = Q.parse(qs, request.form, {}, ctx.lookup)
                if not errors:
                    storage.save_page(ctx.conn, ctx.session["id"], step, answers)
                    ctx.conn.commit()
                    return ADVANCE
            n = None if step == "final" else int(step[6:])
            page = {"title": ctx.t("ab.final_title") if n is None else ctx.t("ab.survey_title", n=n),
                    "intro": "" if n is None else ctx.t("ab.survey_intro"), "questions": qs}
            return ctx.render("modules/survey_page.html", page=page, answers=answers, errors=errors, prior={},
                              page_no=None, page_count=None, last=step == ctx.steps[-1])
        n = int(re.sub(r"\D", "", step))
        if step.startswith("brief"):
            if request.method == "POST":
                return ADVANCE
            return ctx.render("modules/ab_brief.html", n=n, n_views=n_views, n_tasks=len(c["tasks"]))
        if step.startswith("tasks"):
            rows = self._rows(ctx.conn, ctx.session["id"], n)
            events = storage.bulk_task_events(ctx.conn, [ctx.session["id"]]).get(ctx.session["id"], {}).get(n, {})
            tasks = self.tasks_for(ctx.state["order"][n - 1])
            cfg = {
                "tasks": [{"id": t["id"], "title": t["title"], "prompt": t["prompt"], "fields": t["fields"],
                           "goals": (t["goals"]["success"] + t["goals"]["failure"]) if t["goals"] else [],
                           "end_on_goal": t["end_on_goal"],
                           "done": bool(rows.get(t["id"]) and rows[t["id"]]["finished_at"]),
                           "initial": dict({k: rows[t["id"]][k] for k in ("time_ms", "clicks", "scroll_reversals")}
                                           if t["id"] in rows else {}, events=events.get(t["id"], [])),
                           "frameUrl": self.task_frame_url(ctx, n, i)}
                          for i, t in enumerate(tasks)],
                "ease": c["ease"],
                "frameUrl": ctx.action_url(f"view/{n}/"),
                "metricsUrl": ctx.action_url(f"api/{n}/__T__/metrics"),
                "submitUrl": ctx.action_url(f"api/{n}/__T__/submit"),
                "csrfToken": session.get("csrf_token", ""),
                "strings": {k: ctx.t(f"runner.{k}") for k in (
                    "task_of", "start", "answer", "hide", "show", "your_answer", "submit", "gave_up", "back",
                    "ease_q", "ease_low", "ease_high", "incomplete", "save_failed", "offline",
                    "stuck", "stuck_title", "stuck_confirm")},
            }
            return ctx.render("modules/ab_tasks.html", n=n, n_views=n_views, cfg=cfg, total=len(tasks),
                              hide_progress=True)
        abort(404)

    def tasks_for(self, variant):
        """The tasks a variant runs. Every A/B variant runs the same tasks; journey_test overrides."""
        return self.m.conf["tasks"]

    def task_frame_url(self, ctx, n, index):
        """A task's own prototype URL, or None to use the variant's (journey steps have one each)."""
        return None

    def serve_view(self, ctx, n, rest):
        return self._serve(ctx.state["order"][n - 1], rest)

    def _rows(self, conn, session_id, position):
        rows = conn.execute("SELECT * FROM task_results WHERE session_id = ? AND position = ?",
                            (session_id, position)).fetchall()
        return {r["task_id"]: r for r in rows}

    # ------------------------------------------------------------ participant actions

    def action(self, ctx, path):
        m = re.match(r"^view/(\d+)/(.*)$", path)
        if m:
            n = int(m.group(1))
            if ctx.session["step"] != f"tasks{n}":
                abort(404)
            return self.serve_view(ctx, n, m.group(2))
        m = re.match(r"^api/(\d+)/([^/]+)/(metrics|submit)$", path)
        if m and request.method == "POST":
            n, task_id, what = int(m.group(1)), m.group(2), m.group(3)
            if ctx.session["step"] != f"tasks{n}":
                abort(409)
            variant = ctx.state["order"][n - 1]
            task = next((t for t in self.tasks_for(variant) if t["id"] == task_id), None)
            if task is None:
                abort(404)
            data = request.get_json(silent=True) or {}
            if what == "metrics":
                self._save_metrics(ctx.conn, ctx.session["id"], n, variant, task_id, data)
                ctx.conn.commit()
                return ("", 204)
            return self._submit(ctx, n, variant, task, data)
        abort(404)

    def _serve(self, variant, asset):
        """The variant file at the root of its URL; its sibling files (css, images) below it,
        so relative links in a prototype folder keep working."""
        path = self.m.conf["variants"][variant]["path"]
        if not asset:
            if request.args.get("raw"):
                return send_from_directory(path.parent, path.name, max_age=0)
            if path.suffix.lower() in {".html", ".htm"}:
                return send_from_directory(path.parent, path.name, mimetype="text/html", max_age=0)
            
            ext = path.suffix.lower()
            if ext in {".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp", ".avif", ".ico", ".bmp", ".tif", ".tiff"}:
                file_type = "image"
            elif ext in {".pdf"}:
                file_type = "pdf"
            elif ext in {".mp4", ".webm", ".mov", ".ogg"}:
                file_type = "video"
            elif ext in {".mp3", ".wav", ".m4a"}:
                file_type = "audio"
            else:
                file_type = "document"

            return render_template(
                "modules/ab_embed.html",
                filename=path.name,
                file_url=urllib.parse.quote(path.name),
                file_type=file_type,
            )
        from pathlib import Path
        from ..studio import PROTOTYPE_EXT
        if Path(asset).suffix.lower() not in PROTOTYPE_EXT:
            abort(404)
        return send_from_directory(path.parent, asset, max_age=0)

    def _save_metrics(self, conn, session_id, position, variant, task_id, m):
        """The browser sends running totals. Keep the larger value so a stale or reordered
        request never rolls a counter back. The first click is written once."""
        if not isinstance(m, dict):
            return
        path = m.get("click_path") if isinstance(m.get("click_path"), list) else []
        path = [str(x)[:80] for x in path[:CLICK_PATH_MAX]]
        conn.execute(
            "INSERT INTO task_results (session_id, position, variant, task_id, time_ms, clicks, scroll_reversals, "
            "first_click, click_path, viewport_w, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
            # Target columns are qualified with the table name: PostgreSQL calls a bare `time_ms`
            # ambiguous next to `excluded.time_ms`, and SQLite accepts either form.
            "ON CONFLICT(session_id, position, task_id) DO UPDATE SET "
            f"time_ms = {db.greatest(conn, 'task_results.time_ms', 'excluded.time_ms')}, "
            f"clicks = {db.greatest(conn, 'task_results.clicks', 'excluded.clicks')}, "
            f"scroll_reversals = {db.greatest(conn, 'task_results.scroll_reversals', 'excluded.scroll_reversals')}, "
            "first_click = COALESCE(task_results.first_click, excluded.first_click), "
            "click_path = CASE WHEN length(COALESCE(excluded.click_path, '')) "
            "> length(COALESCE(task_results.click_path, '')) "
            "THEN excluded.click_path ELSE task_results.click_path END, "
            "viewport_w = COALESCE(task_results.viewport_w, excluded.viewport_w), "
            "updated_at = excluded.updated_at",
            (session_id, position, variant, task_id, to_int(m.get("time_ms"), hi=3_600_000),
             to_int(m.get("clicks"), hi=100_000), to_int(m.get("scroll_reversals"), hi=100_000),
             path[0] if path else None, storage.dumps(path) if path else None,
             to_int(m.get("viewport_w"), hi=10_000) or None, storage.now_iso()))
        self._save_events(conn, session_id, position, task_id, m.get("events"))

    @staticmethod
    def _clean_events(raw):
        out = []
        for e in raw if isinstance(raw, list) else []:
            if not isinstance(e, dict) or e.get("kind") not in EVENT_KINDS:
                continue
            value = str(e.get("value") or "")[:80]
            if value:
                out.append({"kind": e["kind"], "value": value, "t_ms": to_int(e.get("t_ms"), hi=3_600_000)})
            if len(out) >= EVENTS_MAX:
                break
        return out

    def _save_events(self, conn, session_id, position, task_id, raw):
        """Like the click path, the browser sends the whole list; the longer list wins so a stale
        request never drops an event."""
        events = self._clean_events(raw)
        if not events:
            return
        row = conn.execute("SELECT events FROM task_events WHERE session_id = ? AND position = ? AND task_id = ?",
                           (session_id, position, task_id)).fetchone()
        if row is None:
            conn.execute("INSERT INTO task_events (session_id, position, task_id, events, updated_at) VALUES (?, ?, ?, ?, ?)",
                         (session_id, position, task_id, storage.dumps(events), storage.now_iso()))
        elif len(events) > len(storage.loads(row["events"], [])):
            conn.execute("UPDATE task_events SET events = ?, updated_at = ? WHERE session_id = ? AND position = ? AND task_id = ?",
                         (storage.dumps(events), storage.now_iso(), session_id, position, task_id))

    def _events(self, conn, session_id, position, task_id):
        row = conn.execute("SELECT events FROM task_events WHERE session_id = ? AND position = ? AND task_id = ?",
                           (session_id, position, task_id)).fetchone()
        return storage.loads(row["events"], []) if row else []

    def grade_auto(self, task, answer, events):
        """Answer key and goals both decide when both are set; either alone decides on its own."""
        checks = []
        if task["accept"]:
            checks.append(self.auto_check(task, answer))
        if task.get("goals"):
            checks.append(goal_outcome(task, events) == "success")
        return None if not checks else all(checks)

    def auto_check(self, task, answer):
        if not task["accept"]:
            return None
        for fid, alternatives in task["accept"].items():
            given = norm(answer.get(fid))
            alts = [norm(a) for a in alternatives]
            if not given or not (any(a in given for a in alts) if fid in task["contains"] else given in alts):
                return False
        return True

    def _submit(self, ctx, n, variant, task, data):
        conn, sid = ctx.conn, ctx.session["id"]
        rows = self._rows(conn, sid, n)
        pending = [t["id"] for t in self.tasks_for(variant) if not (rows.get(t["id"]) and rows[t["id"]]["finished_at"])]
        if not pending or pending[0] != task["id"]:
            return jsonify(error=ctx.t("runner.out_of_order")), 409
        gave_up = bool(data.get("gave_up"))
        raw = data.get("answer") if isinstance(data.get("answer"), dict) else {}
        answer = {}
        for f in task["fields"]:
            val = str(raw.get(f["id"]) or "").strip()[:1000]
            if f["kind"] == "choice" and val and val not in f["options"]:
                val = ""
            answer[f["id"]] = val
        if not gave_up and not all(answer.values()):
            return jsonify(error=ctx.t("runner.incomplete")), 400
        ease = to_int(data.get("ease"), lo=0, hi=7)
        if self.m.conf["ease"] and ease < 1:
            return jsonify(error=ctx.t("runner.ease_required")), 400
        self._save_metrics(conn, sid, n, variant, task["id"], data.get("metrics") or {})
        events = self._events(conn, sid, n, task["id"])
        auto = None if gave_up else self.grade_auto(task, answer, events)
        if task.get("end_on_goal"):
            at = first_goal_ms(task, events)
            if at is not None:
                conn.execute("UPDATE task_results SET time_ms = ? WHERE session_id = ? AND position = ? AND task_id = ?",
                             (at, sid, n, task["id"]))
        ts = storage.now_iso()
        conn.execute("UPDATE task_results SET answer = ?, gave_up = ?, ease = ?, auto_pass = ?, finished_at = ?, "
                     "updated_at = ? WHERE session_id = ? AND position = ? AND task_id = ?",
                     (storage.dumps(answer), int(gave_up), ease or None, None if auto is None else int(auto),
                      ts, ts, sid, n, task["id"]))
        conn.commit()
        if len(pending) == 1:
            return jsonify(next=ctx.advance_url())
        return jsonify(next=None)

    # ------------------------------------------------------------ admin

    def admin_action(self, ctx, path):
        m = re.match(r"^preview/([^/]+)/(.*)$", path)
        if m and m.group(1) in self.m.conf["variants"]:
            return self._serve(m.group(1), m.group(2))
        abort(404)

    def _finished_runs(self, ctx, since=None):
        """One entry per participant and position whose tasks are all submitted. With `since`
        (the lock time), sessions started earlier are pilots and left out."""
        conn = ctx.conn
        sessions = conn.execute("SELECT s.*, p.identity FROM sessions s JOIN participants p ON p.id = s.participant_id "
                                "WHERE s.module_id = ?", (self.m.id,)).fetchall()
        if since:
            sessions = [s for s in sessions if s["started_at"] >= since]

        session_ids = [s["id"] for s in sessions]
        answers_by_sid = storage.bulk_answers_by_page(conn, session_ids)
        task_results_by_sid = storage.bulk_task_results(conn, session_ids)
        events_by_sid = storage.bulk_task_events(conn, session_ids)
        runs = []
        for s in sessions:
            state = storage.loads(s["state"], {})
            answers = answers_by_sid.get(s["id"], {})
            for n, variant in enumerate(state.get("order", []), start=1):
                rows = task_results_by_sid.get(s["id"], {}).get(n, {})
                task_ids = [t["id"] for t in self.tasks_for(variant)]
                if not all(rows.get(t) and rows[t]["finished_at"] for t in task_ids):
                    continue
                vw = next((r["viewport_w"] for r in rows.values() if r["viewport_w"]), None)
                runs.append({"session": s, "identity": s["identity"], "position": n, "variant": variant,
                             "order": " → ".join(state["order"]), "rows": rows, "total": sum(r["time_ms"] for r in rows.values()),
                             "mobile": vw is not None and vw <= MOBILE_MAX_W, "viewport_w": vw,
                             "survey": answers.get(f"survey{n}"), "final": answers.get("final"),
                             "events": events_by_sid.get(s["id"], {}).get(n, {}),
                             "order_list": state["order"]})
        return runs

    def _summarize(self, runs):
        per_task = []
        for t in self.m.conf["tasks"]:
            rs = [r["rows"][t["id"]] for r in runs]
            fc = [(first_click_correct(t, r["variant"], r["rows"][t["id"]]["first_click"]), r["rows"][t["id"]]["first_click"])
                  for r in runs]
            fc = [(ok, label) for ok, label in fc if ok is not None]
            grades = [effective_grade(x) for x in rs]
            per_task.append({
                "task": t, "n": len(rs), "success": success_rate(grades),
                "unaided": (sum(1 for g in grades if g == "success") / len([g for g in grades if g])) if any(grades) else None,
                "ungraded": sum(1 for g in grades if not g),
                "median_ms": median([x["time_ms"] for x in rs]), "clicks": mean([x["clicks"] for x in rs]),
                "reversals": mean([x["scroll_reversals"] for x in rs]), "ease": mean([x["ease"] for x in rs]),
                "gave_up": sum(1 for x in rs if x["gave_up"]),
                "first_clicks": Counter(click_label(x["first_click"]) for x in rs if x["first_click"]).most_common(3),
                "rework": {key: mean([rework(t, r["events"].get(t["id"]))[key] for r in runs]) for key in REWORK_KEYS},
                "fc_targeted": bool(fc),
                "fc_rate": (sum(1 for ok, _ in fc if ok) / len(fc)) if fc else None,
                "fc_wrong": Counter(click_label(label) if label else "(no click)" for ok, label in fc if not ok).most_common(3),
            })
        scale = [q for q in self.m.conf["post"] if q["type"] == "scale"]
        return {"n": len(runs), "tasks": per_task, "median_total": median([r["total"] for r in runs]),
                "success": success_rate([effective_grade(x) for r in runs for x in r["rows"].values()]),
                "survey": {q["id"]: mean([(r["survey"] or {}).get(q["id"]) for r in runs]) for q in scale}}

    def _rework_shown(self, runs):
        """Metrics worth a column: lostness needs optimal_steps, errors need the prototype to report
        them; back-navigation and misclicks need page or click events from the runner."""
        events = [e for r in runs for evs in r["events"].values() for e in evs]
        kinds = {e.get("kind") for e in events}
        return {"back": "page" in kinds, "misclicks": bool(events), "errors": "error" in kinds,
                "lostness": "page" in kinds and any(t.get("optimal_steps") for t in self.m.conf["tasks"])}

    def rules_state(self, ctx):
        """Lock status for the report: the lock, whether the live config drifted from it (an
        edit outside Studio), how many pilot sessions are hidden, and the lock history."""
        conn = ctx.conn
        lock = locks.get(conn, self.m.id)
        show_pilot = request.args.get("pilot") == "1"
        drift = locks.changed_fields(lock["fields"], locks.snapshot(self.m)) if lock else []
        pilots = conn.execute("SELECT COUNT(*) FROM sessions WHERE module_id = ? AND started_at < ?",
                              (self.m.id, lock["at"])).fetchone()[0] if lock else 0
        history = []
        for row in conn.execute("SELECT timestamp, action, details FROM audit_log "
                                "WHERE action IN ('lock_rules', 'unlock_rules') ORDER BY id DESC").fetchall():
            d = storage.loads(row["details"], {})
            if d.get("module") == self.m.id:
                history.append({"when": row["timestamp"], "action": row["action"], "reason": d.get("reason", "")})
        return {"lock": lock, "drift": drift, "pilots": pilots, "show_pilot": show_pilot, "history": history,
                "since": lock["at"] if lock and not show_pilot else None}

    def report(self, ctx):
        c = self.m.conf
        rs = self.rules_state(ctx)
        runs = self._finished_runs(ctx, since=rs["since"])
        keys = list(c["variants"])
        by_variant = {k: self._summarize([r for r in runs if r["variant"] == k]) for k in keys}
        by_device = {dev: {k: self._summarize([r for r in runs if r["variant"] == k and r["mobile"] == (dev == "mobile")])
                           for k in keys} for dev in ("desktop", "mobile")}

        # Paired comparisons of each challenger against the baseline, per protocol-style rule.
        people = {}
        for r in runs:
            people.setdefault(r["session"]["id"], {})[r["variant"]] = r
        base = c["baseline"]
        rules = []
        rework_cmp = {}
        for k in keys:
            if k == base:
                continue
            pairs = [p for p in people.values() if base in p and k in p]
            faster, time_p, time_verdict, time_ok = paired_time_check(pairs, base, k, c["rule"]["time_test"])
            wx = wilcoxon([p[base]["total"] - p[k]["total"] for p in pairs])
            srb, srk, succ_lo, succ_hi, succ_verdict, succ_ok = paired_success_check(pairs, base, k)

            checks = [
                {"key": "faster", "value": f"{faster}/{len(pairs)}", "p_value": time_p, "verdict": time_verdict, "ok": time_ok,
                 "test": c["rule"]["time_test"], "sign_p": sign_test(faster, sum(1 for p in pairs if p[k]["total"] > p[base]["total"])),
                 "wilcoxon": wx},
                {"key": "success", "a": srb, "b": srk, "interval": (succ_lo, succ_hi), "verdict": succ_verdict, "ok": succ_ok},
            ]
            
            surv = paired_survey_check(pairs, base, k, by_variant, c["rule"]["survey_questions"], c["rule"]["min_survey_gain"])
            if surv:
                sb, sk, gain, surv_lo, surv_hi, surv_verdict, surv_ok = surv
                checks.append({"key": "survey", "a": sb, "b": sk, "gain": gain, "interval": (surv_lo, surv_hi),
                               "verdict": surv_verdict, "ok": surv_ok})
            rework_cmp[k] = paired_rework(pairs, base, k, c["tasks"])
            fc = paired_first_click_check(pairs, base, k, c["tasks"])
            if fc:
                checks.append(fc)
            rules.append({"variant": k, "n": len(pairs), "checks": checks,
                          "all_ok": all(x["ok"] for x in checks if x.get("counts", True)),
                          # Rules changed after the lock, or pilots mixed in: never a final call.
                          "enough": len(pairs) >= c["rule"]["min_participants"] and not rs["drift"]
                          and not (rs["lock"] and rs["show_pilot"] and rs["pilots"])})

        def describe_group(ps):
            """Descriptives only: segments are too small for verdicts."""
            out = {"n": len(ps), "per": {}, "diff": {}}
            for k in keys:
                rs = [p[k] for p in ps if k in p]
                grades = [effective_grade(x) for r in rs for x in r["rows"].values()]
                out["per"][k] = {"success": success_rate(grades), "median": median([r["total"] for r in rs])}
                if k != base:
                    out["diff"][k] = median([p[k]["total"] - p[base]["total"] for p in ps if k in p and base in p])
            return out

        segments = []
        if c.get("segments"):
            matched = set()
            for name, pattern in c["segments"].items():
                rx = re.compile(pattern)
                ps = [p for p in people.values() if rx.search(next(iter(p.values()))["identity"])]
                matched |= {id(p) for p in ps}
                segments.append(dict(describe_group(ps), name=name))
            rest = [p for p in people.values() if id(p) not in matched]
            if rest:
                segments.append(dict(describe_group(rest), name="Other"))

        # Carryover check: the challenger's median gain should not flip sign with the order.
        carryover = []
        for k in keys:
            if k == base:
                continue
            by_first = {}
            for p in people.values():
                if base in p and k in p:
                    by_first.setdefault(next(iter(p.values()))["order_list"][0], []).append(p[k]["total"] - p[base]["total"])
            db, dk = median(by_first.get(base, [])), median(by_first.get(k, []))
            if db is not None and dk is not None and (db < 0) != (dk < 0) and db != 0 and dk != 0:
                carryover.append({"variant": k, "base_first": db, "k_first": dk,
                                  "n_base": len(by_first[base]), "n_k": len(by_first[k])})

        orders = {}
        for p in people.values():
            first = next(iter(p.values()))
            orders.setdefault(first["order"], []).append(p)
        order_effect = [{"order": o, "n": len(ps), "medians": {k: median([p[k]["total"] for p in ps if k in p]) for k in keys}}
                        for o, ps in sorted(orders.items())]

        pref = Counter()
        final_responses = []
        seen = set()
        for r in runs:
            sid = r["session"]["id"]
            if sid in seen or not r["final"]:
                continue
            seen.add(sid)
            choice = r["final"].get("_preference")
            if choice == "none":
                pref["none"] += 1
            elif choice and choice.isdigit() and int(choice) <= len(r["order_list"]):
                pref[r["order_list"][int(choice) - 1]] += 1
            final_responses.append((r["identity"], r["final"]))
        presets = []
        for q in c["post"]:
            if not q.get("preset"):
                continue
            score = lambda r: Q.preset_score(q, (r["survey"] or {}).get(q["id"]))
            per = {k: preset_stats(q, [score(r) for r in runs if r["variant"] == k]) for k in keys}
            compare = []
            for k in keys:
                if k == base:
                    continue
                diffs = [score(p[k]) - score(p[base]) for p in people.values()
                         if base in p and k in p and score(p[k]) is not None and score(p[base]) is not None]
                lo, hi = bootstrap_interval(diffs)
                verdict = ("evidence for" if lo is not None and lo > 0 else
                           "evidence against" if hi is not None and hi < 0 else "not enough evidence yet")
                compare.append({"variant": k, "n": len(diffs), "gain": (sum(diffs) / len(diffs)) if diffs else None,
                                "interval": (lo, hi), "verdict": verdict})
            presets.append({"q": q, "where": "post", "per": per, "compare": compare})
        finals = {}
        for r in runs:
            finals.setdefault(r["session"]["id"], r["final"] or {})
        for q in c["final"]:
            if q.get("preset"):
                presets.append({"q": q, "where": "final", "compare": [],
                                "all": preset_stats(q, [Q.preset_score(q, f.get(q["id"])) for f in finals.values()])})
        texts = [(r["identity"], r["variant"], q["label"], (r["survey"] or {}).get(q["id"]))
                 for r in runs for q in c["post"] if q["type"] == "text" and (r["survey"] or {}).get(q["id"])]
        return ctx.render_fragment("modules/ab_report.html", c=c, keys=keys, by_variant=by_variant, by_device=by_device,
                          rules=rules, rules_state=rs, presets=presets, rework_cmp=rework_cmp,
                          rework_shown=self._rework_shown(runs), segments=segments, carryover=carryover, slug=self.m.project.slug, mid=self.m.id, order_effect=order_effect, pref=pref, texts=texts,
                          final_summary=Q.summarize(c["final"], final_responses),
                          scale_q=[q for q in c["post"] if q["type"] == "scale"], mobile_max=MOBILE_MAX_W)

    def detail(self, ctx, session):
        state = storage.loads(session["state"], {})
        views = []
        for n, variant in enumerate(state.get("order", []), start=1):
            rows = self._rows(ctx.conn, session["id"], n)
            v_conf = self.m.conf.get("variants", {}).get(variant, {})
            v_label = v_conf.get("label", variant)
            survey = storage.page_answers(ctx.conn, session["id"], f"survey{n}")
            views.append({"n": n, "variant": variant, "label": v_label,
                          "viewport_w": next((r["viewport_w"] for r in rows.values() if r["viewport_w"]), None),
                          "tasks": [{"task": t, "row": rows.get(t["id"]),
                                     "answer": storage.loads(rows[t["id"]]["answer"], {}) if t["id"] in rows else {},
                                     "path": storage.loads(rows[t["id"]]["click_path"], []) if t["id"] in rows else [],
                                     "grade": effective_grade(rows[t["id"]]) if t["id"] in rows else None}
                                    for t in self.tasks_for(variant)],
                          "survey": [(q, Q.display(q, survey.get(q["id"]))) for q in self.m.conf["post"] if q["id"] in survey]})
        final = storage.page_answers(ctx.conn, session["id"], "final")
        ctx.state = state
        final_rows = [(q, Q.display(q, final.get(q["id"]))) for q in self.final_questions(ctx) if q["id"] in final]
        return ctx.render_fragment("modules/ab_detail.html", views=views, final_rows=final_rows,
                                   sid=session["id"], grades=GRADES)

    def save_detail(self, ctx, session, form):
        for n, variant in enumerate(storage.loads(session["state"], {}).get("order", []), start=1):
            for t in self.tasks_for(variant):
                key = f"{session['id']}_{n}_{t['id']}"
                if f"grade_{key}" not in form:
                    continue
                grade = form.get(f"grade_{key}") or None
                note = form.get(f"note_{key}", "").strip()[:2000] or None
                ctx.conn.execute("UPDATE task_results SET grade = ?, observer_note = ? "
                                 "WHERE session_id = ? AND position = ? AND task_id = ?",
                                 (grade if grade in GRADES else None, note, session["id"], n, t["id"]))

    def export(self, ctx):
        c = self.m.conf
        header = ["participant", "order", "position", "variant", "viewport_w", "device", "task", "time_s", "clicks",
                  "scroll_reversals", "first_click", "gave_up", "ease", "auto_pass", "grade", "answer",
                  "observer_note", "click_path", "goal_outcome", "goal_events",
                  "first_click_label", "first_click_correct", "pilot",
                  "pages_visited", "back_nav", "misclicks", "errors", "lostness"]
        post_cols = list(Q.flatten(c["post"], {}))
        header += [f"post.{k}" for k in post_cols] + ["final.preference_variant", "final.preference_reason"] \
            + [f"final.{k}" for k in Q.flatten(c["final"], {})]
        out = []
        sessions = ctx.conn.execute("SELECT s.*, p.identity FROM sessions s JOIN participants p ON p.id = s.participant_id "
                                    "WHERE s.module_id = ? ORDER BY p.identity", (self.m.id,)).fetchall()
        events_by_sid = storage.bulk_task_events(ctx.conn, [s["id"] for s in sessions])
        lock_at = locks.locked_at(ctx.conn, self.m.id)
        for s in sessions:
            order = storage.loads(s["state"], {}).get("order", [])
            final = storage.page_answers(ctx.conn, s["id"], "final")
            choice = final.get("_preference", "")
            pref_variant = order[int(choice) - 1] if choice.isdigit() and int(choice) <= len(order) else choice
            final_cols = [pref_variant, final.get("_preference_reason", "")] + list(Q.flatten(c["final"], final).values())
            for n, variant in enumerate(order, start=1):
                rows = self._rows(ctx.conn, s["id"], n)
                post = list(Q.flatten(c["post"], storage.page_answers(ctx.conn, s["id"], f"survey{n}")).values())
                evs = events_by_sid.get(s["id"], {}).get(n, {})
                for t in c["tasks"]:
                    x = rows.get(t["id"])
                    vw = x["viewport_w"] if x else None
                    out.append([s["identity"], ">".join(order), n, variant, vw or "",
                                "" if vw is None else ("mobile" if vw <= MOBILE_MAX_W else "desktop"), t["id"],
                                round(x["time_ms"] / 1000, 1) if x else "", x["clicks"] if x else "",
                                x["scroll_reversals"] if x else "", (x["first_click"] or "") if x else "",
                                x["gave_up"] if x else "", (x["ease"] or "") if x else "",
                                "" if not x or x["auto_pass"] is None else x["auto_pass"],
                                (effective_grade(x) or "") if x else "", (x["answer"] or "") if x else "",
                                (x["observer_note"] or "") if x else "",
                                " > ".join(storage.loads(x["click_path"], [])) if x else "",
                                goal_outcome(t, evs.get(t["id"])) or "",
                                " > ".join(f"{e['value']}@{round(e['t_ms'] / 1000, 1)}s"
                                           for e in evs.get(t["id"], []) if e.get("kind") == "goal"),
                                click_label(x["first_click"]) if x and x["first_click"] else "",
                                fc_cell(first_click_correct(t, variant, x["first_click"] if x else None)),
                                int(bool(lock_at) and s["started_at"] < lock_at)]
                               + rework_cells(t, evs.get(t["id"]))
                               + post + final_cols)
        return header, out

    def export_cols(self, ctx, session_ids):
        if not session_ids:
            return [], {}
        c = self.m.conf
        headers = []
        for n in range(1, len(c.get("variants", {})) + 1):
            for t in c.get("tasks", []):
                for col in ["variant", "time_s", "clicks", "scroll_reversals", "first_click", "gave_up", "ease", "auto_pass", "grade", "answer"]:
                    headers.append(f"{self.m.id}.task{n}_{t['id']}.{col}")
            for k in Q.flatten(c.get("post", []), {}):
                headers.append(f"{self.m.id}.task{n}.post.{k}")
                
        headers += [f"{self.m.id}.preference_variant", f"{self.m.id}.preference_reason"]
        for k in Q.flatten(c.get("final", []), {}):
            headers.append(f"{self.m.id}.final.{k}")
            
        placeholders = ",".join("?" * len(session_ids))
        sessions = ctx.conn.execute(
            f"SELECT id, state FROM sessions WHERE id IN ({placeholders})", session_ids
        ).fetchall()
        
        out = {}
        for s in sessions:
            cols = {}
            order = storage.loads(s["state"], {}).get("order", [])
            final = storage.page_answers(ctx.conn, s["id"], "final")
            choice = final.get("_preference", "")
            pref_variant = order[int(choice) - 1] if choice.isdigit() and int(choice) <= len(order) else choice
            cols[f"{self.m.id}.preference_variant"] = pref_variant
            cols[f"{self.m.id}.preference_reason"] = final.get("_preference_reason", "")
            
            for k, v in Q.flatten(c.get("final", []), final).items():
                cols[f"{self.m.id}.final.{k}"] = v
                
            for n, variant in enumerate(order, start=1):
                rows = self._rows(ctx.conn, s["id"], n)
                post = Q.flatten(c.get("post", []), storage.page_answers(ctx.conn, s["id"], f"survey{n}"))
                
                for t in c.get("tasks", []):
                    x = rows.get(t["id"])
                    pfx = f"{self.m.id}.task{n}_{t['id']}"
                    cols[f"{pfx}.variant"] = variant
                    cols[f"{pfx}.time_s"] = round(x["time_ms"] / 1000, 1) if x and x["time_ms"] is not None else ""
                    cols[f"{pfx}.clicks"] = x["clicks"] if x else ""
                    cols[f"{pfx}.scroll_reversals"] = x["scroll_reversals"] if x else ""
                    cols[f"{pfx}.first_click"] = (x["first_click"] or "") if x else ""
                    cols[f"{pfx}.gave_up"] = x["gave_up"] if x else ""
                    cols[f"{pfx}.ease"] = (x["ease"] or "") if x else ""
                    cols[f"{pfx}.auto_pass"] = ("" if not x or x["auto_pass"] is None else x["auto_pass"])
                    cols[f"{pfx}.grade"] = (effective_grade(x) or "") if x else ""
                    cols[f"{pfx}.answer"] = (x["answer"] or "") if x else ""
                    
                for k, v in post.items():
                    cols[f"{self.m.id}.task{n}.post.{k}"] = v
                    
            out[s["id"]] = cols
        return headers, out
