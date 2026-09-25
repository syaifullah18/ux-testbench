"""Within-subject comparison of two or more static prototypes.

Each participant works through the same timed tasks on every variant, in a counterbalanced
order, then answers a short survey per variant and an optional final survey. Prototypes are
served under neutral URLs (view/1, view/2) so the file name never hints which is which.
"""
import random
import re
import statistics
from collections import Counter

from flask import abort, jsonify, request, send_from_directory

from .. import questions as Q
from .. import storage
from .base import ADVANCE, ModuleType

ORDERS = {"rotate", "code_parity", "random", "fixed"}
MOBILE_MAX_W = 767
CLICK_PATH_MAX = 40
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
            if not fields:
                fields = [{"id": "answer", "label": "", "kind": "text", "placeholder": ""}]
            accept = t.get("accept") or {}
            for fid in accept:
                if fid not in {f["id"] for f in fields}:
                    scope.add(f"{w}.accept.{fid}: no field with that id")
            tasks.append({"id": str(t["id"]), "title": str(t.get("title") or t["id"]), "prompt": str(t["prompt"]),
                          "fields": fields, "accept": {k: [str(x) for x in v] for k, v in accept.items()},
                          "contains": [str(x) for x in t.get("contains") or []], "probe": str(t.get("probe") or "")})
        if not tasks:
            scope.add("tasks must list at least one task")
        if len({t["id"] for t in tasks}) != len(tasks):
            scope.add("task ids must be unique")

        post = Q.normalize(raw.get("post_survey") or [], scope, "post_survey")
        final = Q.normalize(raw.get("final_survey") or [], scope, "final_survey")
        rule = raw.get("decision_rule") or {}
        for qid in rule.get("survey_questions") or []:
            if qid not in {q["id"] for q in post if q["type"] == "scale"}:
                scope.add(f"decision_rule.survey_questions: '{qid}' is not a scale question in post_survey")
        return {
            "variants": variants, "baseline": baseline, "order": order, "intro": str(raw.get("intro") or ""),
            "tasks": tasks, "ease": bool(raw.get("ease_question", True)), "post": post, "final": final,
            "preference": bool(raw.get("preference", len(variants) > 1)),
            "rule": {"min_participants": int(rule.get("min_participants", 8)),
                     "survey_questions": list(rule.get("survey_questions") or []),
                     "min_survey_gain": float(rule.get("min_survey_gain", 0.5))},
        }

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
        order = ctx.state["order"]
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
            cfg = {
                "tasks": [{"id": t["id"], "title": t["title"], "prompt": t["prompt"], "fields": t["fields"],
                           "done": bool(rows.get(t["id"]) and rows[t["id"]]["finished_at"]),
                           "initial": {k: rows[t["id"]][k] for k in ("time_ms", "clicks", "scroll_reversals")}
                           if t["id"] in rows else {}} for t in c["tasks"]],
                "ease": c["ease"],
                "frameUrl": ctx.action_url(f"view/{n}/"),
                "metricsUrl": ctx.action_url(f"api/{n}/__T__/metrics"),
                "submitUrl": ctx.action_url(f"api/{n}/__T__/submit"),
                "strings": {k: ctx.t(f"runner.{k}") for k in (
                    "task_of", "start", "answer", "hide", "show", "your_answer", "submit", "gave_up", "back",
                    "ease_q", "ease_low", "ease_high", "incomplete", "save_failed", "offline")},
            }
            return ctx.render("modules/ab_tasks.html", n=n, n_views=n_views, cfg=cfg, total=len(c["tasks"]),
                              hide_progress=True)
        abort(404)

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
            return self._serve(ctx.state["order"][n - 1], m.group(2))
        m = re.match(r"^api/(\d+)/([^/]+)/(metrics|submit)$", path)
        if m and request.method == "POST":
            n, task_id, what = int(m.group(1)), m.group(2), m.group(3)
            if ctx.session["step"] != f"tasks{n}":
                abort(409)
            task = next((t for t in self.m.conf["tasks"] if t["id"] == task_id), None)
            if task is None:
                abort(404)
            variant = ctx.state["order"][n - 1]
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
            return send_from_directory(path.parent, path.name, mimetype="text/html", max_age=0)
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
            "ON CONFLICT(session_id, position, task_id) DO UPDATE SET "
            "time_ms = MAX(time_ms, excluded.time_ms), clicks = MAX(clicks, excluded.clicks), "
            "scroll_reversals = MAX(scroll_reversals, excluded.scroll_reversals), "
            "first_click = COALESCE(first_click, excluded.first_click), "
            "click_path = CASE WHEN length(COALESCE(excluded.click_path, '')) > length(COALESCE(click_path, '')) "
            "THEN excluded.click_path ELSE click_path END, "
            "viewport_w = COALESCE(viewport_w, excluded.viewport_w), updated_at = excluded.updated_at",
            (session_id, position, variant, task_id, to_int(m.get("time_ms"), hi=3_600_000),
             to_int(m.get("clicks"), hi=100_000), to_int(m.get("scroll_reversals"), hi=100_000),
             path[0] if path else None, storage.dumps(path) if path else None,
             to_int(m.get("viewport_w"), hi=10_000) or None, storage.now_iso()))

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
        pending = [t["id"] for t in self.m.conf["tasks"] if not (rows.get(t["id"]) and rows[t["id"]]["finished_at"])]
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
        auto = None if gave_up else self.auto_check(task, answer)
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

    def _finished_runs(self, ctx):
        """One entry per participant and position whose tasks are all submitted."""
        conn = ctx.conn
        sessions = conn.execute("SELECT s.*, p.identity FROM sessions s JOIN participants p ON p.id = s.participant_id "
                                "WHERE s.module_id = ?", (self.m.id,)).fetchall()
        task_ids = [t["id"] for t in self.m.conf["tasks"]]
        runs = []
        for s in sessions:
            state = storage.loads(s["state"], {})
            answers = {r["page"]: storage.loads(r["data"], {}) for r in
                       conn.execute("SELECT page, data FROM answers WHERE session_id = ?", (s["id"],))}
            for n, variant in enumerate(state.get("order", []), start=1):
                rows = self._rows(conn, s["id"], n)
                if not all(rows.get(t) and rows[t]["finished_at"] for t in task_ids):
                    continue
                vw = next((r["viewport_w"] for r in rows.values() if r["viewport_w"]), None)
                runs.append({"session": s, "identity": s["identity"], "position": n, "variant": variant,
                             "order": " → ".join(state["order"]), "rows": rows, "total": sum(r["time_ms"] for r in rows.values()),
                             "mobile": vw is not None and vw <= MOBILE_MAX_W, "viewport_w": vw,
                             "survey": answers.get(f"survey{n}"), "final": answers.get("final"),
                             "order_list": state["order"]})
        return runs

    def _summarize(self, runs):
        per_task = []
        for t in self.m.conf["tasks"]:
            rs = [r["rows"][t["id"]] for r in runs]
            grades = [effective_grade(x) for x in rs]
            per_task.append({
                "task": t, "n": len(rs), "success": success_rate(grades),
                "unaided": (sum(1 for g in grades if g == "success") / len([g for g in grades if g])) if any(grades) else None,
                "ungraded": sum(1 for g in grades if not g),
                "median_ms": median([x["time_ms"] for x in rs]), "clicks": mean([x["clicks"] for x in rs]),
                "reversals": mean([x["scroll_reversals"] for x in rs]), "ease": mean([x["ease"] for x in rs]),
                "gave_up": sum(1 for x in rs if x["gave_up"]),
                "first_clicks": Counter(x["first_click"] for x in rs if x["first_click"]).most_common(3),
            })
        scale = [q for q in self.m.conf["post"] if q["type"] == "scale"]
        return {"n": len(runs), "tasks": per_task, "median_total": median([r["total"] for r in runs]),
                "success": success_rate([effective_grade(x) for r in runs for x in r["rows"].values()]),
                "survey": {q["id"]: mean([(r["survey"] or {}).get(q["id"]) for r in runs]) for q in scale}}

    def report(self, ctx):
        c = self.m.conf
        runs = self._finished_runs(ctx)
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
        for k in keys:
            if k == base:
                continue
            pairs = [p for p in people.values() if base in p and k in p]
            faster = sum(1 for p in pairs if p[k]["total"] < p[base]["total"])
            sb = [by_variant[base]["survey"].get(q) for q in c["rule"]["survey_questions"]]
            sk = [by_variant[k]["survey"].get(q) for q in c["rule"]["survey_questions"]]
            sb, sk = mean(sb), mean(sk)
            srb, srk = success_rate([effective_grade(x) for p in pairs for x in p[base]["rows"].values()]), \
                success_rate([effective_grade(x) for p in pairs for x in p[k]["rows"].values()])
            checks = [
                {"key": "faster", "value": f"{faster}/{len(pairs)}", "ok": bool(pairs) and faster > len(pairs) / 2},
                {"key": "success", "a": srb, "b": srk, "ok": srb is not None and srk is not None and srk >= srb},
            ]
            if c["rule"]["survey_questions"]:
                gain = (sk - sb) if sk is not None and sb is not None else None
                checks.append({"key": "survey", "a": sb, "b": sk, "gain": gain,
                               "ok": gain is not None and gain >= c["rule"]["min_survey_gain"]})
            rules.append({"variant": k, "n": len(pairs), "checks": checks, "all_ok": all(x["ok"] for x in checks),
                          "enough": len(pairs) >= c["rule"]["min_participants"]})

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
        texts = [(r["identity"], r["variant"], q["label"], (r["survey"] or {}).get(q["id"]))
                 for r in runs for q in c["post"] if q["type"] == "text" and (r["survey"] or {}).get(q["id"])]
        return ctx.render_fragment("modules/ab_report.html", c=c, keys=keys, by_variant=by_variant, by_device=by_device,
                          rules=rules, order_effect=order_effect, pref=pref, texts=texts,
                          final_summary=Q.summarize(c["final"], final_responses),
                          scale_q=[q for q in c["post"] if q["type"] == "scale"], mobile_max=MOBILE_MAX_W)

    def detail(self, ctx, session):
        state = storage.loads(session["state"], {})
        views = []
        for n, variant in enumerate(state.get("order", []), start=1):
            rows = self._rows(ctx.conn, session["id"], n)
            survey = storage.page_answers(ctx.conn, session["id"], f"survey{n}")
            views.append({"n": n, "variant": variant, "label": self.m.conf["variants"][variant]["label"],
                          "viewport_w": next((r["viewport_w"] for r in rows.values() if r["viewport_w"]), None),
                          "tasks": [{"task": t, "row": rows.get(t["id"]),
                                     "answer": storage.loads(rows[t["id"]]["answer"], {}) if t["id"] in rows else {},
                                     "path": storage.loads(rows[t["id"]]["click_path"], []) if t["id"] in rows else [],
                                     "grade": effective_grade(rows[t["id"]]) if t["id"] in rows else None}
                                    for t in self.m.conf["tasks"]],
                          "survey": [(q, Q.display(q, survey.get(q["id"]))) for q in self.m.conf["post"] if q["id"] in survey]})
        final = storage.page_answers(ctx.conn, session["id"], "final")
        ctx.state = state
        final_rows = [(q, Q.display(q, final.get(q["id"]))) for q in self.final_questions(ctx) if q["id"] in final]
        return ctx.render_fragment("modules/ab_detail.html", views=views, final_rows=final_rows,
                                   sid=session["id"], grades=GRADES)

    def save_detail(self, ctx, session, form):
        for n in range(1, len(storage.loads(session["state"], {}).get("order", [])) + 1):
            for t in self.m.conf["tasks"]:
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
                  "observer_note", "click_path"]
        post_cols = list(Q.flatten(c["post"], {}))
        header += [f"post.{k}" for k in post_cols] + ["final.preference_variant", "final.preference_reason"] \
            + [f"final.{k}" for k in Q.flatten(c["final"], {})]
        out = []
        sessions = ctx.conn.execute("SELECT s.*, p.identity FROM sessions s JOIN participants p ON p.id = s.participant_id "
                                    "WHERE s.module_id = ? ORDER BY p.identity", (self.m.id,)).fetchall()
        for s in sessions:
            order = storage.loads(s["state"], {}).get("order", [])
            final = storage.page_answers(ctx.conn, s["id"], "final")
            choice = final.get("_preference", "")
            pref_variant = order[int(choice) - 1] if choice.isdigit() and int(choice) <= len(order) else choice
            final_cols = [pref_variant, final.get("_preference_reason", "")] + list(Q.flatten(c["final"], final).values())
            for n, variant in enumerate(order, start=1):
                rows = self._rows(ctx.conn, s["id"], n)
                post = list(Q.flatten(c["post"], storage.page_answers(ctx.conn, s["id"], f"survey{n}")).values())
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
                                " > ".join(storage.loads(x["click_path"], [])) if x else ""] + post + final_cols)
        return header, out
