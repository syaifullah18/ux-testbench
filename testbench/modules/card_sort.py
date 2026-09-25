from flask import abort, jsonify, request
from werkzeug.utils import secure_filename

from .. import questions as Q, storage
from .base import ADVANCE, ModuleType


class CardSort(ModuleType):
    """
    Card Sorting module. Supports open, closed, and hybrid sorting.
    Configuration:
    - cards: list of dicts (id, label)
    - categories: optional list of strings (pre-defined categories)
    - allow_new_categories: boolean (default True if categories is empty, else False)
    - pre / post / final surveys
    """
    type_name = "card_sort"

    def validate(self, raw, scope):
        c = {"type": "card_sort", "id": self.m.id, "title": raw.get("title", self.m.id)}
        
        cards = raw.get("cards")
        if not isinstance(cards, list) or not cards:
            scope.add("requires a 'cards' list")
            c["cards"] = []
        else:
            c["cards"] = []
            for i, card in enumerate(cards):
                if isinstance(card, str):
                    c["cards"].append({"id": f"c{i+1}", "label": card})
                elif isinstance(card, dict) and "label" in card:
                    c["cards"].append({"id": str(card.get("id", f"c{i+1}")), "label": str(card["label"])})
                else:
                    scope.add(f"card {i} must be a string or object with a 'label'")
                    
        cats = raw.get("categories", [])
        if not isinstance(cats, list):
            scope.add("'categories' must be a list of strings")
            cats = []
            
        c["categories"] = [str(x) for x in cats]
        c["allow_new_categories"] = bool(raw.get("allow_new_categories", not bool(c["categories"])))
        c["instructions"] = str(raw.get("instructions", "Sort the cards into categories."))
        
        c["pre"] = Q.normalize(raw.get("pre", []), scope.down("pre"))
        c["post"] = Q.normalize(raw.get("post", []), scope.down("post"))
        c["final"] = Q.normalize(raw.get("final", []), scope.down("final"))
        return c

    def check_refs(self, scope):
        c = self.m.conf
        Q.check_refs(c["pre"], scope.down("pre"), self.m.project)
        Q.check_refs(c["post"], scope.down("post"), self.m.project)
        Q.check_refs(c["final"], scope.down("final"), self.m.project)

    def question_ids(self):
        c = self.m.conf
        return Q.gather_ids(c["pre"]) | Q.gather_ids(c["post"]) | Q.gather_ids(c["final"])

    def steps(self, state):
        c = self.m.conf
        res = []
        if c.get("pre"): res.append("pre")
        res.append("sort")
        if c.get("post"): res.append("post")
        if c.get("final"): res.append("final")
        return res

    def step_label(self, step, t):
        if step == "sort": return t("card_sort.sort", "Card Sorting")
        if step in ("pre", "post"): return t(f"module.{step}_survey")
        return t("module.final_questions")

    def handle(self, ctx, step):
        c = self.m.conf
        if step in ("pre", "post", "final"):
            if request.method == "POST":
                errs = Q.validate_answers(c[step], request.form, ctx.conn, ctx.session["id"])
                if not errs:
                    storage.save_page(ctx.conn, ctx.session["id"], step, request.form)
                    return ADVANCE
                ctx.set_errors(errs)
            
            return ctx.render("modules/survey_page.html",
                              page={"title": c.get("title", self.m.id), "questions": c[step]},
                              answers=request.form if request.method == "POST" else storage.page_answers(ctx.conn, ctx.session["id"], step))

        if step == "sort":
            if request.method == "POST":
                # Expecting JSON: { card_id: category_name, ... }
                data = request.json
                if not isinstance(data, dict):
                    abort(400)
                storage.save_page(ctx.conn, ctx.session["id"], step, data)
                return jsonify({"ok": True})
                
            return ctx.render("modules/card_sort.html", conf=c)
            
        abort(404)

    def report(self, ctx):
        c = self.m.conf
        sessions = self._finished_runs(ctx.conn)
        
        # Aggregate logic
        # For each card, count how many times it was put in each category
        # cards[card_id][category_name] = count
        matrix = {card["id"]: {} for card in c["cards"]}
        
        for s in sessions:
            sort_data = storage.page_answers(ctx.conn, s["id"], "sort")
            for card_id, cat_name in sort_data.items():
                if card_id in matrix and cat_name:
                    matrix[card_id][cat_name] = matrix[card_id].get(cat_name, 0) + 1
                    
        return ctx.render_fragment("modules/card_sort_report.html", 
                                   sessions=sessions, 
                                   cards=c["cards"],
                                   matrix=matrix)

    def _finished_runs(self, conn):
        sessions = conn.execute("SELECT id, identity, finished_at FROM sessions WHERE module_id = ? AND finished_at IS NOT NULL ORDER BY finished_at", (self.m.id,)).fetchall()
        return sessions

    def detail(self, ctx, session):
        c = self.m.conf
        sort_data = storage.page_answers(ctx.conn, session["id"], "sort")
        return ctx.render_fragment("modules/card_sort_detail.html", conf=c, sort_data=sort_data)

    def export(self, ctx):
        c = self.m.conf
        sessions = self._finished_runs(ctx.conn)
        header = ["participant", "finished_at"]
        for card in c["cards"]:
            header.append(f"card_{card['id']}")
            
        post = list(Q.flatten(c["post"], {}).keys()) if c.get("post") else []
        final = list(Q.flatten(c["final"], {}).keys()) if c.get("final") else []
        header.extend(post + final)
        
        out = []
        for s in sessions:
            sort_data = storage.page_answers(ctx.conn, s["id"], "sort")
            row = [s["identity"], s["finished_at"]]
            for card in c["cards"]:
                row.append(sort_data.get(card["id"], ""))
                
            if post:
                post_data = list(Q.flatten(c["post"], storage.page_answers(ctx.conn, s["id"], "post")).values())
                row.extend(post_data)
            if final:
                final_data = list(Q.flatten(c["final"], storage.page_answers(ctx.conn, s["id"], "final")).values())
                row.extend(final_data)
                
            out.append(row)
        return header, out

    def export_cols(self, ctx, session_ids):
        if not session_ids:
            return [], {}
        c = self.m.conf
        headers = []
        for card in c.get("cards", []):
            headers.append(f"{self.m.id}.card_{card['id']}")
            
        for k in Q.flatten(c.get("pre", []), {}):
            headers.append(f"{self.m.id}.pre.{k}")
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
            sort_data = pages.get("sort", {})
            
            for card in c.get("cards", []):
                cols[f"{self.m.id}.card_{card['id']}"] = sort_data.get(card["id"], "")
                
            for k, v in Q.flatten(c.get("pre", []), flat).items():
                cols[f"{self.m.id}.pre.{k}"] = v
            for k, v in Q.flatten(c.get("post", []), flat).items():
                cols[f"{self.m.id}.post.{k}"] = v
            for k, v in Q.flatten(c.get("final", []), flat).items():
                cols[f"{self.m.id}.final.{k}"] = v
                
            out[sid] = cols
        return headers, out
