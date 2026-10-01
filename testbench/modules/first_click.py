import re
from pathlib import Path
from flask import abort, request, send_from_directory

from .. import heatmap
from .. import questions as Q
from .. import storage
from .base import ADVANCE, ModuleType

CLICK_MODES = ("first", "all")
ALLOWED_IMAGE_EXT = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".avif", ".ico", ".bmp", ".tif", ".tiff"}


class FirstClick(ModuleType):
    """Shows an image, asks a question, and captures where the participant clicks: the first
    click only (`clicks: first`, the default), or every click until Continue (`clicks: all`).
    Either way each click is also written to the clicks table, which the heatmap reads."""
    type_name = "first_click"

    def validate(self, raw, scope):
        out = {"tasks": [], "post": [], "final": []}
        default_mode = raw.get("clicks", "first")
        if default_mode not in CLICK_MODES:
            scope.add(f"clicks must be one of {list(CLICK_MODES)}")
            default_mode = "first"
        if not isinstance(raw.get("tasks"), list) or not raw["tasks"]:
            scope.add("first_click needs a `tasks:` list")
            return out
        for i, t in enumerate(raw["tasks"]):
            if not isinstance(t, dict):
                scope.add(f"tasks[{i}] must be a mapping")
                continue
            tid = str(t.get("id") or "")
            if not tid or not re.fullmatch(r"[a-z0-9_-]+", tid):
                scope.add(f"tasks[{i}] needs a valid `id:` (alphanumeric/dash/underscore)")
            if not t.get("prompt"):
                scope.add(f"tasks[{i}] needs a `prompt:`")
            img = str(t.get("image") or "")
            if not img:
                scope.add(f"tasks[{i}] needs an `image:` (URL or relative path)")
            elif "://" not in img and Path(img.split("?")[0]).suffix.lower() not in ALLOWED_IMAGE_EXT:
                scope.add(f"tasks[{i}].image must be an image file (.png, .jpg, .svg, .webp, etc.)")
            mode = t.get("clicks", default_mode)
            if mode not in CLICK_MODES:
                scope.add(f"tasks[{i}].clicks must be one of {list(CLICK_MODES)}")
                mode = default_mode
            out["tasks"].append({"id": tid, "prompt": str(t.get("prompt") or ""), "image": img, "clicks": mode})

        out["post"] = Q.normalize(raw.get("post", []), scope, "post")
        out["final"] = Q.normalize(raw.get("final", []), scope, "final")
        
        ids = [t["id"] for t in out["tasks"]] + [q["id"] for q in out["post"]] + [q["id"] for q in out["final"]]
        if len(ids) != len(set(ids)):
            scope.add("all task ids and question ids must be unique across the module")
        return out

    def check_refs(self, scope):
        Q.check_refs(self.m.conf.get("post", []) + self.m.conf.get("final", []), scope, self.m.project, self.m.id, self.question_ids())

    def question_ids(self):
        return {q["id"] for q in self.m.conf.get("post", []) + self.m.conf.get("final", [])}

    def steps(self, state):
        res = [f"t{i+1}" for i in range(len(self.m.conf.get("tasks", [])))]
        if self.m.conf.get("post"):
            res += [f"post{i+1}" for i in range(len(self.m.conf.get("tasks", [])))]
        if self.m.conf.get("final"):
            res.append("final")
        return res

    def step_label(self, step, t):
        if step == "final":
            return t("module.final_questions")
        if step.startswith("post"):
            return t("module.follow_up")
        idx = int(step[1:]) - 1
        return self.m.conf["tasks"][idx]["prompt"]

    def handle(self, ctx, step):
        if step == "final":
            return self._handle_survey(ctx, "final", self.m.conf["final"])
        if step.startswith("post"):
            idx = int(step[4:]) - 1
            return self._handle_survey(ctx, f"post{idx+1}", self.m.conf["post"])
        
        idx = int(step[1:]) - 1
        task = self.m.conf["tasks"][idx]
        if request.method == "POST":
            self._save_task(ctx, step, task)
            return ADVANCE

        return ctx.render("modules/first_click_task.html", task=task, clicks_max=storage.CLICKS_MAX)

    def _save_task(self, ctx, step, task):
        """The answer page keeps its original shape (the first click, in natural image pixels)
        so older reports and exports read it unchanged; every click also goes to the clicks
        table with the viewport it was made on."""
        form = request.form
        w, h = form.get("w", type=int), form.get("h", type=int)
        viewport = {"viewport_w": form.get("viewport_w", type=int), "viewport_h": form.get("viewport_h", type=int)}
        if task.get("clicks") == "all":
            raw = storage.loads(form.get("clicks"), [])
            raw = raw if isinstance(raw, list) else []
            points = [{"seq": i, "x": c.get("x"), "y": c.get("y"), "t_ms": c.get("t_ms"), "doc_w": w, "doc_h": h,
                       **viewport} for i, c in enumerate(raw[:storage.CLICKS_MAX]) if isinstance(c, dict)]
        else:
            points = [{"seq": 0, "x": form.get("x", type=int), "y": form.get("y", type=int),
                       "t_ms": form.get("time_ms", type=int) or 0, "doc_w": w, "doc_h": h, **viewport}]
        clean = [c for c in (storage.clean_click(p) for p in points) if c]
        first = clean[0] if clean else {}
        storage.save_page(ctx.conn, ctx.session["id"], step, {
            "x": first.get("x"), "y": first.get("y"), "w": w, "h": h, "time_ms": first.get("t_ms", 0),
            "clicks": len(clean)})
        storage.save_clicks(ctx.conn, ctx.session["id"], step, clean, replace=True)
        ctx.conn.commit()

    def _handle_survey(self, ctx, page_id, questions):
        answers, errors = storage.page_answers(ctx.conn, ctx.session["id"], page_id), {}
        prior = storage.module_answers(ctx.conn, ctx.session["id"])
        if request.method == "POST":
            answers, errors = Q.parse(questions, request.form, prior, ctx.lookup)
            if not errors:
                storage.save_page(ctx.conn, ctx.session["id"], page_id, answers)
                ctx.conn.commit()
                return ADVANCE
        return ctx.render("modules/survey_page.html", page={"title": "", "intro": "", "questions": questions}, 
                          page_no=1, page_count=1, answers=answers, errors=errors, prior=prior, last=True)

    def action(self, ctx, path):
        # Only serve image assets configured in this module's tasks
        from pathlib import Path
        clean_path = Path(path).as_posix().lstrip("/")
        if ".." in path or clean_path.startswith("../"):
            abort(404)
        allowed_images = {
            Path(t["image"]).as_posix().lstrip("/")
            for t in self.m.conf.get("tasks", [])
            if t.get("image") and "://" not in t["image"]
        }
        if clean_path not in allowed_images:
            abort(404)
        if Path(clean_path).suffix.lower() not in ALLOWED_IMAGE_EXT:
            abort(404)
        if (ctx.project.dir / clean_path).is_file():
            return send_from_directory(ctx.project.dir, clean_path, max_age=0)
        if (ctx.project.dir / "prototypes" / clean_path).is_file():
            return send_from_directory(ctx.project.dir / "prototypes", clean_path, max_age=0)
        abort(404)

    admin_action = action

    # ---- admin

    def _responses(self, ctx):
        sql = ("SELECT s.id, p.identity FROM sessions s JOIN participants p ON p.id = s.participant_id "
               "WHERE s.module_id = ? AND s.finished_at IS NOT NULL ORDER BY p.identity")
        sessions = ctx.conn.execute(sql, (self.m.id,)).fetchall()
        answers_by_sid = storage.bulk_module_answers(ctx.conn, [r["id"] for r in sessions])
        answers_by_page = storage.bulk_answers_by_page(ctx.conn, [r["id"] for r in sessions])
        return [(r["id"], r["identity"], answers_by_page.get(r["id"], {}), answers_by_sid.get(r["id"], {}))
                for r in sessions]

    def _clicks(self, ctx, responses):
        """{surface: [click, ...]} for finished sessions. A session answered before the clicks
        table existed has only its answer page; its one click is read from there, so an old
        study shows up in the heatmap without anyone editing it."""
        by_surface = storage.bulk_clicks(ctx.conn, [sid for sid, *_ in responses])
        have = {(c["session_id"], surface) for surface, cs in by_surface.items() for c in cs}
        for sid, _, pages, _ in responses:
            for i in range(len(self.m.conf["tasks"])):
                surface, page = f"t{i+1}", pages.get(f"t{i+1}") or {}
                if (sid, surface) in have or page.get("x") is None or not page.get("w") or not page.get("h"):
                    continue
                by_surface.setdefault(surface, []).append({
                    "session_id": sid, "seq": 0, "x": page["x"], "y": page["y"], "doc_w": page["w"],
                    "doc_h": page["h"], "viewport_w": None, "viewport_h": None,
                    "t_ms": page.get("time_ms") or 0, "dead": 0})
        return by_surface

    def report(self, ctx):
        responses = self._responses(ctx)
        flat_responses = [(i, flat) for _, i, _, flat in responses]
        by_surface = self._clicks(ctx, responses)
        panels = []
        for i, t in enumerate(self.m.conf["tasks"]):
            clicks = by_surface.get(f"t{i+1}", [])
            first = [c for c in clicks if c["seq"] == 0]
            times = [c["t_ms"] for c in first]
            panels.append({"task": t, "id": f"{self.m.id}-{t['id']}", "title": t["prompt"], "data": heatmap.panel(clicks),
                           "n_clicks": len(clicks), "n_participants": len({c["session_id"] for c in clicks}),
                           "mean_first_ms": sum(times) / len(times) if times else None})
        return ctx.render_fragment("modules/first_click_report.html", n=len(responses), panels=panels,
                                   min_n=heatmap.HEATMAP_MIN_N, mobile_max=heatmap.MOBILE_MAX_W,
                                   post_summary=Q.summarize(self.m.conf["post"], flat_responses),
                                   final_summary=Q.summarize(self.m.conf["final"], flat_responses))

    def detail(self, ctx, session):
        answers = storage.bulk_answers_by_page(ctx.conn, [session["id"]]).get(session["id"], {})
        flat = storage.bulk_module_answers(ctx.conn, [session["id"]]).get(session["id"], {})
        
        clicks = storage.bulk_clicks(ctx.conn, [session["id"]])
        task_clicks = []
        for i, t in enumerate(self.m.conf["tasks"]):
            task_clicks.append({"task": t, "click": answers.get(f"t{i+1}"), "all": clicks.get(f"t{i+1}", [])})
            
        post = [(q, Q.display(q, flat.get(q["id"]))) for q in self.m.conf["post"] if q["id"] in flat]
        final = [(q, Q.display(q, flat.get(q["id"]))) for q in self.m.conf["final"] if q["id"] in flat]
        
        return ctx.render_fragment("modules/first_click_detail.html", task_clicks=task_clicks, post=post, final=final)

    def export(self, ctx):
        """One row per task, as before; a `clicks: all` task gets one row per click instead,
        with a `click` ordinal column that only appears when some task needs it."""
        responses = self._responses(ctx)
        c = self.m.conf
        multi = any(t["clicks"] == "all" for t in c["tasks"])
        by_surface = storage.bulk_clicks(ctx.conn, [sid for sid, *_ in responses]) if multi else {}

        header = ["participant", "task"] + (["click"] if multi else []) + ["x", "y", "w", "h", "time_ms"]
        post_cols = list(Q.flatten(c["post"], {}))
        final_cols = list(Q.flatten(c["final"], {}))
        header += [f"post.{k}" for k in post_cols] + [f"final.{k}" for k in final_cols]

        out = []
        for sid, identity, pages, flat in responses:
            post = list(Q.flatten(c["post"], flat).values())
            final = list(Q.flatten(c["final"], flat).values())
            for i, t in enumerate(c["tasks"]):
                click = pages.get(f"t{i+1}", {})
                if t["clicks"] == "all":
                    mine = [k for k in by_surface.get(f"t{i+1}", []) if k["session_id"] == sid]
                    for k in mine:
                        out.append([identity, t["id"], k["seq"] + 1, k["x"], k["y"], k["doc_w"], k["doc_h"],
                                    k["t_ms"]] + post + final)
                    if mine:
                        continue
                out.append([identity, t["id"]] + ([""] if multi else []) + [
                    click.get("x", ""), click.get("y", ""), click.get("w", ""), click.get("h", ""),
                    click.get("time_ms", "")] + post + final)

        return header, out

    def export_cols(self, ctx, session_ids):
        if not session_ids:
            return [], {}
        c = self.m.conf
        headers = []
        for i, t in enumerate(c.get("tasks", [])):
            for col in ["x", "y", "w", "h", "time_ms", "clicks"]:
                headers.append(f"{self.m.id}.{t['id']}.{col}")
                
        for k in Q.flatten(c.get("post", []), {}):
            headers.append(f"{self.m.id}.post.{k}")
        for k in Q.flatten(c.get("final", []), {}):
            headers.append(f"{self.m.id}.final.{k}")
            
        answers_by_page = storage.bulk_answers_by_page(ctx.conn, session_ids)
        flat_answers = storage.bulk_module_answers(ctx.conn, session_ids)
        
        out = {}
        for sid in session_ids:
            cols = {}
            pages = answers_by_page.get(sid, {})
            flat = flat_answers.get(sid, {})
            
            for i, t in enumerate(c.get("tasks", [])):
                click = pages.get(f"t{i+1}", {})
                for col in ["x", "y", "w", "h", "time_ms"]:
                    cols[f"{self.m.id}.{t['id']}.{col}"] = click.get(col, "")
                # Pages saved before multi-click existed hold one click when they hold any.
                count = click.get("clicks", 1 if click.get("x") is not None else 0)
                cols[f"{self.m.id}.{t['id']}.clicks"] = count if click else ""
                    
            for k, v in Q.flatten(c.get("post", []), flat).items():
                cols[f"{self.m.id}.post.{k}"] = v
            for k, v in Q.flatten(c.get("final", []), flat).items():
                cols[f"{self.m.id}.final.{k}"] = v
                
            out[sid] = cols
        return headers, out
