"""Compares a current business flow with a proposed one, each an ordered list of steps.

A step is one prototype page with a goal, played by a role. One participant plays every role in
turn (`roles_played_by: same_participant`); a step ends when its goal fires, so the task runner,
metrics and grading are the A/B test's. Variants may have different numbers of steps, so only
totals (hands-on time, success) are compared statistically; step-level numbers are descriptive.
Structural metrics (steps, handoffs between roles) need no participants at all.
"""
import re

from flask import abort, request, send_from_directory

from .. import storage
from ..heatmap import MOBILE_MAX_W
from .ab_test import (ABTest, GOAL_RE, ORDERS, bootstrap_interval, effective_grade, goal_outcome, median,
                      paired_time_check, rework, sign_test, wilcoxon)

ROLE_RE = re.compile(r"^[A-Za-z0-9_-]{1,40}$")


def handoffs(steps):
    """Role changes between consecutive steps."""
    return sum(1 for a, b in zip(steps, steps[1:]) if a["role"] != b["role"])


def run_success(run, steps):
    """A run succeeds when every step reached its success goal (or was graded a success)."""
    grades = [effective_grade(run["rows"][s["id"]]) for s in steps]
    if any(g is None for g in grades):
        return None
    return all(g in ("success", "assisted") for g in grades)


class JourneyTest(ABTest):
    type_name = "journey_test"
    report_without_data = True      # structural metrics are useful before anyone takes part

    def validate(self, raw, scope):
        project_dir = self.m.project.dir.resolve()
        variants = {}
        raw_variants = raw.get("variants")
        if not isinstance(raw_variants, dict) or len(raw_variants) < 2:
            scope.add("variants must map at least two keys (for example current, proposed) to {label, steps}")
            raw_variants = raw_variants if isinstance(raw_variants, dict) else {}
        for key, v in raw_variants.items():
            key, v = str(key), v or {}
            w = f"variants.{key}"
            if not re.match(r"^[A-Za-z0-9_-]{1,20}$", key):
                scope.add(f"variant key '{key}' must be short letters/digits")
            steps = []
            for i, st in enumerate(v.get("steps") or []):
                sw = f"{w}.steps[{i}]"
                if not isinstance(st, dict) or not st.get("id") or not st.get("file") or not st.get("goal"):
                    scope.add(f"{sw}: needs id, file and goal")
                    continue
                role, goal = str(st.get("role") or "participant"), str(st["goal"])
                if not ROLE_RE.match(role):
                    scope.add(f"{sw}.role: '{role}' must be letters, digits, - or _")
                if not GOAL_RE.match(goal):
                    scope.add(f"{sw}.goal: '{goal}' must be 1 to 80 letters, digits, - _ . or :")
                path = (project_dir / str(st["file"])).resolve()
                if not path.is_file():
                    scope.add(f"{sw}.file '{st['file']}' does not exist under {project_dir.name}/")
                elif project_dir not in path.parents or path.parent == project_dir:
                    scope.add(f"{sw}.file must sit in a folder inside the project")
                elif path.suffix.lower() not in (".html", ".htm"):
                    scope.add(f"{sw}.file must be an HTML page")
                failure = [str(x) for x in (st.get("failure") or [])]
                steps.append({
                    "id": str(st["id"]), "role": role, "path": path,
                    "title": str(st.get("title") or f"{role}: {st['id']}"),
                    "prompt": str(st.get("prompt") or st.get("title") or st["id"]),
                    "fields": [], "accept": {}, "contains": [], "probe": str(st.get("probe") or ""),
                    "goals": {"success": [goal], "failure": failure}, "end_on_goal": True,
                    "first_click": None, "optimal_steps": None,
                })
            if not steps:
                scope.add(f"{w}.steps must list at least one step")
            if len({s["id"] for s in steps}) != len(steps):
                scope.add(f"{w}.steps: step ids must be unique")
            cycle = v.get("cycle_time_hours")
            if cycle is not None and (not isinstance(cycle, (int, float)) or isinstance(cycle, bool) or cycle <= 0):
                scope.add(f"{w}.cycle_time_hours must be a positive number of hours")
                cycle = None
            variants[key] = {"label": str(v.get("label") or key), "steps": steps, "cycle_time_hours": cycle,
                             "path": steps[0]["path"] if steps else None}
        keys = list(variants)
        baseline = str(raw.get("baseline") or (keys[0] if keys else ""))
        if keys and baseline not in variants:
            scope.add(f"baseline '{baseline}' is not a variant")
        order = raw.get("order", "rotate")
        if order not in ORDERS:
            scope.add(f"order must be one of {sorted(ORDERS)}")
        played = raw.get("roles_played_by", "same_participant")
        if played != "same_participant":
            scope.add("roles_played_by: only same_participant is supported for now")
        rule = raw.get("decision_rule") or {}
        time_test = str(rule.get("time_test", "sign"))
        if time_test not in ("sign", "wilcoxon"):
            scope.add("decision_rule.time_test must be sign or wilcoxon")
            time_test = "sign"
        locale = getattr(self.m.project, "locale", None)
        from .. import questions as Q
        return {
            "variants": variants, "baseline": baseline, "order": order, "intro": str(raw.get("intro") or ""),
            "segments": {}, "ease": bool(raw.get("ease_question", False)),
            "post": Q.normalize(raw.get("post_survey") or [], scope, "post_survey", locale=locale),
            "final": Q.normalize(raw.get("final_survey") or [], scope, "final_survey", locale=locale),
            "preference": bool(raw.get("preference", False)),
            "rule": {"min_participants": int(rule.get("planned_participants", rule.get("min_participants", 8))),
                     "time_test": time_test, "survey_questions": [], "min_survey_gain": 0.0},
            # The A/B flow screens count "tasks"; for a journey that is the baseline's steps.
            "tasks": variants[baseline]["steps"] if baseline in variants else [],
            "roles_played_by": played,
        }

    def tasks_for(self, variant):
        return self.m.conf["variants"][variant]["steps"]

    def task_frame_url(self, ctx, n, index):
        return ctx.action_url(f"view/{n}/{index}/")

    def serve_view(self, ctx, n, rest):
        m = re.match(r"^(\d+)/(.*)$", rest)
        steps = self.tasks_for(ctx.state["order"][n - 1])
        if not m or int(m.group(1)) >= len(steps):
            abort(404)
        path, asset = steps[int(m.group(1))]["path"], m.group(2)
        if not asset:
            return send_from_directory(path.parent, path.name, mimetype="text/html", max_age=0)
        from pathlib import Path
        from ..studio import PROTOTYPE_EXT
        if Path(asset).suffix.lower() not in PROTOTYPE_EXT:
            abort(404)
        return send_from_directory(path.parent, asset, max_age=0)

    def admin_action(self, ctx, path):
        m = re.match(r"^preview/([^/]+)/(\d+)/(.*)$", path)
        if m and m.group(1) in self.m.conf["variants"]:
            steps = self.tasks_for(m.group(1))
            if int(m.group(2)) < len(steps):
                p = steps[int(m.group(2))]["path"]
                return send_from_directory(p.parent, m.group(3) or p.name, max_age=0)
        abort(404)

    # ------------------------------------------------------------ report

    def structure(self):
        out = []
        for k, v in self.m.conf["variants"].items():
            roles = []
            for s in v["steps"]:
                if s["role"] not in roles:
                    roles.append(s["role"])
            out.append({"key": k, "label": v["label"], "steps": len(v["steps"]), "handoffs": handoffs(v["steps"]),
                        "roles": roles, "cycle_time_hours": v["cycle_time_hours"],
                        "path": " → ".join(f"{s['role']}: {s['id']}" for s in v["steps"])})
        return out

    def report(self, ctx):
        c = self.m.conf
        rs = self.rules_state(ctx)
        runs = self._finished_runs(ctx, since=rs["since"])
        keys, base = list(c["variants"]), c["baseline"]
        per_variant = {}
        for k in keys:
            steps = self.tasks_for(k)
            vr = [r for r in runs if r["variant"] == k]
            succ = [run_success(r, steps) for r in vr]
            succ = [x for x in succ if x is not None]
            per_variant[k] = {
                "n": len(vr), "median_total": median([r["total"] for r in vr]),
                "success": (sum(succ) / len(succ)) if succ else None,
                "steps": [{"step": s, "n": len(vr),
                           "median_ms": median([r["rows"][s["id"]]["time_ms"] for r in vr]),
                           "success": (lambda g: (sum(1 for x in g if x in ("success", "assisted")) / len(g)) if g else None)(
                               [effective_grade(r["rows"][s["id"]]) for r in vr if effective_grade(r["rows"][s["id"]])]),
                           "back": median([rework(s, r["events"].get(s["id"]))["back"] for r in vr]),
                           "errors": median([rework(s, r["events"].get(s["id"]))["errors"] for r in vr])}
                          for s in steps],
            }
        people = {}
        for r in runs:
            people.setdefault(r["session"]["id"], {})[r["variant"]] = r
        compare = []
        for k in keys:
            if k == base:
                continue
            pairs = [p for p in people.values() if base in p and k in p]
            faster, time_p, verdict, _ = paired_time_check(pairs, base, k, c["rule"]["time_test"])
            saved = [p[base]["total"] - p[k]["total"] for p in pairs]
            wx = wilcoxon(saved)
            sdiff = []
            for p in pairs:
                sb, sk = run_success(p[base], self.tasks_for(base)), run_success(p[k], self.tasks_for(k))
                if sb is not None and sk is not None:
                    sdiff.append(int(sk) - int(sb))
            lo, hi = bootstrap_interval(sdiff)
            med_saved = median(saved)
            cycle = c["variants"][base]["cycle_time_hours"]
            compare.append({
                "variant": k, "n": len(pairs), "faster": faster,
                "sign_p": sign_test(faster, sum(1 for d in saved if d < 0)), "wilcoxon": wx, "time_p": time_p,
                "verdict": verdict, "enough": len(pairs) >= c["rule"]["min_participants"] and not rs["drift"]
                and not (rs["lock"] and rs["show_pilot"] and rs["pilots"]),
                "median_saved_ms": med_saved,
                "cycle_share": (med_saved / (cycle * 3_600_000)) if med_saved is not None and cycle else None,
                "success_interval": (lo, hi),
            })
        return ctx.render_fragment("modules/journey_report.html", c=c, keys=keys, structure=self.structure(),
                                   per_variant=per_variant, compare=compare, rules_state=rs,
                                   slug=self.m.project.slug, mid=self.m.id)

    # ------------------------------------------------------------ export

    def export(self, ctx):
        header = ["participant", "order", "position", "variant", "step_no", "step", "role", "time_s", "gave_up",
                  "grade", "goal_outcome", "pages_visited", "back_nav", "misclicks", "errors", "device"]
        out = []
        sessions = ctx.conn.execute("SELECT s.*, p.identity FROM sessions s JOIN participants p ON p.id = s.participant_id "
                                    "WHERE s.module_id = ? ORDER BY p.identity", (self.m.id,)).fetchall()
        events_by_sid = storage.bulk_task_events(ctx.conn, [s["id"] for s in sessions])
        for s in sessions:
            order = storage.loads(s["state"], {}).get("order", [])
            for n, variant in enumerate(order, start=1):
                rows = self._rows(ctx.conn, s["id"], n)
                evs = events_by_sid.get(s["id"], {}).get(n, {})
                for i, st in enumerate(self.tasks_for(variant), start=1):
                    x = rows.get(st["id"])
                    rw = rework(st, evs.get(st["id"]))
                    vw = x["viewport_w"] if x else None
                    out.append([s["identity"], ">".join(order), n, variant, i, st["id"], st["role"],
                                round(x["time_ms"] / 1000, 1) if x else "", x["gave_up"] if x else "",
                                (effective_grade(x) or "") if x else "", goal_outcome(st, evs.get(st["id"])) or "",
                                rw["pages"], rw["back"], rw["misclicks"], rw["errors"],
                                "" if vw is None else ("mobile" if vw <= MOBILE_MAX_W else "desktop")])
        return header, out

    def export_cols(self, ctx, session_ids):
        if not session_ids:
            return [], {}
        keys = list(self.m.conf["variants"])
        headers = [f"{self.m.id}.{k}.{col}" for k in keys for col in ("total_s", "success")]
        placeholders = ",".join("?" * len(session_ids))
        sessions = ctx.conn.execute(f"SELECT id, state FROM sessions WHERE id IN ({placeholders})", session_ids).fetchall()
        out = {}
        for s in sessions:
            cols = {}
            for n, variant in enumerate(storage.loads(s["state"], {}).get("order", []), start=1):
                rows = self._rows(ctx.conn, s["id"], n)
                steps = self.tasks_for(variant)
                if all(rows.get(st["id"]) and rows[st["id"]]["finished_at"] for st in steps):
                    ok = run_success({"rows": rows}, steps)
                    cols[f"{self.m.id}.{variant}.total_s"] = round(sum(rows[st["id"]]["time_ms"] for st in steps) / 1000, 1)
                    cols[f"{self.m.id}.{variant}.success"] = "" if ok is None else int(ok)
            out[s["id"]] = cols
        return headers, out
