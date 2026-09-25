import re
from flask import abort, request, send_from_directory

from .. import questions as Q
from .. import storage
from .base import ADVANCE, ModuleType


class FirstClick(ModuleType):
    """Shows an image, asks a question, and captures the first click coordinates."""
    type_name = "first_click"

    def validate(self, raw, scope):
        out = {"tasks": [], "post": [], "final": []}
        if not isinstance(raw.get("tasks"), list) or not raw["tasks"]:
            scope.add("first_click needs a `tasks:` list")
            return out
        for i, t in enumerate(raw["tasks"]):
            tid = str(t.get("id") or "")
            if not tid or not re.fullmatch(r"[a-z0-9_-]+", tid):
                scope.add(f"tasks[{i}] needs a valid `id:` (alphanumeric/dash/underscore)")
            if not t.get("prompt"):
                scope.add(f"tasks[{i}] needs a `prompt:`")
            if not t.get("image"):
                scope.add(f"tasks[{i}] needs an `image:` (URL or relative path)")
            out["tasks"].append({"id": tid, "prompt": str(t["prompt"]), "image": str(t["image"])})

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
            # JS will submit x, y, width, height, time_ms
            x = request.form.get("x", type=int)
            y = request.form.get("y", type=int)
            w = request.form.get("w", type=int)
            h = request.form.get("h", type=int)
            time_ms = request.form.get("time_ms", type=int) or 0
            
            storage.save_page(ctx.conn, ctx.session["id"], step, {
                "x": x, "y": y, "w": w, "h": h, "time_ms": time_ms
            })
            ctx.conn.commit()
            return ADVANCE

        return ctx.render("modules/first_click_task.html", task=task)

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
        # Allow serving assets if image path doesn't start with http
        if ".." not in path:
            return send_from_directory(ctx.project.dir, path, max_age=0)
        abort(404)

    admin_action = action

    # ---- admin

    def _responses(self, ctx):
        sql = ("SELECT s.id, p.identity FROM sessions s JOIN participants p ON p.id = s.participant_id "
               "WHERE s.module_id = ? AND s.finished_at IS NOT NULL ORDER BY p.identity")
        sessions = ctx.conn.execute(sql, (self.m.id,)).fetchall()
        answers_by_sid = storage.bulk_module_answers(ctx.conn, [r["id"] for r in sessions])
        answers_by_page = storage.bulk_answers_by_page(ctx.conn, [r["id"] for r in sessions])
        return [(r["identity"], answers_by_page.get(r["id"], {}), answers_by_sid.get(r["id"], {})) for r in sessions]

    def report(self, ctx):
        responses = self._responses(ctx)
        flat_responses = [(i, flat) for i, _, flat in responses]
        
        # Aggregate clicks for each task
        task_clicks = []
        for i, t in enumerate(self.m.conf["tasks"]):
            clicks = []
            for _, pages, _ in responses:
                page_data = pages.get(f"t{i+1}")
                if page_data and page_data.get("x") is not None:
                    clicks.append(page_data)
            task_clicks.append({"task": t, "clicks": clicks})

        return ctx.render_fragment("modules/first_click_report.html", n=len(responses),
                                   task_clicks=task_clicks,
                                   post_summary=Q.summarize(self.m.conf["post"], flat_responses),
                                   final_summary=Q.summarize(self.m.conf["final"], flat_responses))

    def detail(self, ctx, session):
        answers = storage.bulk_answers_by_page(ctx.conn, [session["id"]]).get(session["id"], {})
        flat = storage.bulk_module_answers(ctx.conn, [session["id"]]).get(session["id"], {})
        
        task_clicks = []
        for i, t in enumerate(self.m.conf["tasks"]):
            task_clicks.append({"task": t, "click": answers.get(f"t{i+1}")})
            
        post = [(q, Q.display(q, flat.get(q["id"]))) for q in self.m.conf["post"] if q["id"] in flat]
        final = [(q, Q.display(q, flat.get(q["id"]))) for q in self.m.conf["final"] if q["id"] in flat]
        
        return ctx.render_fragment("modules/first_click_detail.html", task_clicks=task_clicks, post=post, final=final)

    def export(self, ctx):
        responses = self._responses(ctx)
        c = self.m.conf
        
        header = ["participant", "task", "x", "y", "w", "h", "time_ms"]
        post_cols = list(Q.flatten(c["post"], {}))
        final_cols = list(Q.flatten(c["final"], {}))
        header += [f"post.{k}" for k in post_cols] + [f"final.{k}" for k in final_cols]
        
        out = []
        for identity, pages, flat in responses:
            post = list(Q.flatten(c["post"], flat).values())
            final = list(Q.flatten(c["final"], flat).values())
            for i, t in enumerate(c["tasks"]):
                click = pages.get(f"t{i+1}", {})
                out.append([
                    identity, t["id"],
                    click.get("x", ""), click.get("y", ""), click.get("w", ""), click.get("h", ""), click.get("time_ms", "")
                ] + post + final)
                
        return header, out

    def export_cols(self, ctx, session_ids):
        if not session_ids:
            return [], {}
        c = self.m.conf
        headers = []
        for i, t in enumerate(c.get("tasks", [])):
            for col in ["x", "y", "w", "h", "time_ms"]:
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
                    
            for k, v in Q.flatten(c.get("post", []), flat).items():
                cols[f"{self.m.id}.post.{k}"] = v
            for k, v in Q.flatten(c.get("final", []), flat).items():
                cols[f"{self.m.id}.final.{k}"] = v
                
            out[sid] = cols
        return headers, out
