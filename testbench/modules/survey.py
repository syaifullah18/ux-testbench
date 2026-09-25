from flask import request

from .. import questions as Q
from .. import storage
from .base import ADVANCE, ModuleType


class Survey(ModuleType):
    """One or more pages of questions. `questions:` is shorthand for a single page."""
    type_name = "survey"

    def validate(self, raw, scope):
        pages = raw.get("pages")
        if pages is None and "questions" in raw:
            pages = [{"questions": raw["questions"]}]
        if not isinstance(pages, list) or not pages:
            scope.add("survey needs `questions:` or a non-empty `pages:` list")
            return {"pages": []}
        out, all_q = [], []
        for i, page in enumerate(pages):
            qs = Q.normalize((page or {}).get("questions"), scope, f"pages[{i}].questions")
            out.append({"title": str(page.get("title") or ""), "intro": str(page.get("intro") or ""), "questions": qs})
            all_q.extend(qs)
        ids = [q["id"] for q in all_q]
        if len(ids) != len(set(ids)):
            scope.add("question ids must be unique across all pages")
        return {"pages": out, "questions": all_q}

    def check_refs(self, scope):
        Q.check_refs(self.m.conf.get("questions", []), scope, self.m.project, self.m.id, self.question_ids())

    def question_ids(self):
        return {q["id"] for q in self.m.conf.get("questions", [])}

    def steps(self, state):
        return [f"p{i + 1}" for i in range(len(self.m.conf["pages"]))]

    def step_label(self, step, t):
        pages = self.m.conf["pages"]
        page = pages[int(step[1:]) - 1]
        return page["title"] or self.m.title

    def handle(self, ctx, step):
        idx = int(step[1:]) - 1
        page = self.m.conf["pages"][idx]
        prior = {k: v for k, v in storage.module_answers(ctx.conn, ctx.session["id"]).items()
                 if k not in {q["id"] for q in page["questions"]}}
        answers, errors = storage.page_answers(ctx.conn, ctx.session["id"], step), {}
        if request.method == "POST":
            answers, errors = Q.parse(page["questions"], request.form, prior, ctx.lookup)
            if not errors:
                storage.save_page(ctx.conn, ctx.session["id"], step, answers)
                ctx.conn.commit()
                return ADVANCE
        return ctx.render("modules/survey_page.html", page=page, page_no=idx + 1,
                          page_count=len(self.m.conf["pages"]), answers=answers, errors=errors,
                          prior=prior, last=idx == len(self.m.conf["pages"]) - 1)

    # ---- admin

    def _responses(self, ctx, finished_only=True):
        sql = ("SELECT s.id, p.identity FROM sessions s JOIN participants p ON p.id = s.participant_id "
               "WHERE s.module_id = ?" + (" AND s.finished_at IS NOT NULL" if finished_only else "") + " ORDER BY p.identity")
        return [(r["identity"], storage.module_answers(ctx.conn, r["id"]))
                for r in ctx.conn.execute(sql, (self.m.id,)).fetchall()]

    def report(self, ctx):
        responses = self._responses(ctx)
        return ctx.render_fragment("modules/survey_report.html", n=len(responses),
                          summary=Q.summarize(self.m.conf["questions"], responses))

    def detail(self, ctx, session):
        answers = storage.module_answers(ctx.conn, session["id"])
        rows = [(q, Q.display(q, answers.get(q["id"]))) for q in self.m.conf["questions"] if q["id"] in answers]
        return ctx.render_fragment("modules/survey_detail.html", rows=rows)

    def export(self, ctx):
        sessions = ctx.conn.execute(
            "SELECT s.*, p.identity FROM sessions s JOIN participants p ON p.id = s.participant_id "
            "WHERE s.module_id = ? ORDER BY p.identity", (self.m.id,)).fetchall()
        qs = self.m.conf["questions"]
        header = None
        rows = []
        for s in sessions:
            cols = Q.flatten(qs, storage.module_answers(ctx.conn, s["id"]))
            if header is None:
                header = ["participant", "started_at", "finished_at"] + list(cols)
            rows.append([s["identity"], s["started_at"], s["finished_at"] or ""] + list(cols.values()))
        return header or ["participant", "started_at", "finished_at"] + list(Q.flatten(qs, {})), rows
