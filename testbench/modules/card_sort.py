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
        c = {"type": "card_sort", "id": self.m.id, "title": str(raw.get("title") or self.m.id)}
        
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
                    
        card_ids = [card["id"] for card in c["cards"]]
        if len(card_ids) != len(set(card_ids)):
            scope.add("card ids must be unique")

        cats = raw.get("categories", [])
        if not isinstance(cats, list):
            scope.add("'categories' must be a list of strings")
            cats = []
            
        c["categories"] = [str(x) for x in cats]
        c["allow_new_categories"] = bool(raw.get("allow_new_categories", not bool(c["categories"])))
        c["instructions"] = str(raw.get("instructions", "Sort the cards into categories."))
        
        c["pre"] = Q.normalize(raw.get("pre", []), scope, "pre")
        c["post"] = Q.normalize(raw.get("post", []), scope, "post")
        c["final"] = Q.normalize(raw.get("final", []), scope, "final")

        all_q = c["pre"] + c["post"] + c["final"]
        q_ids = [q["id"] for q in all_q]
        if len(q_ids) != len(set(q_ids)):
            scope.add("all question ids must be unique across the module")

        return c

    def check_refs(self, scope):
        c = self.m.conf
        all_qs = c.get("pre", []) + c.get("post", []) + c.get("final", [])
        Q.check_refs(all_qs, scope, self.m.project, self.m.id, self.question_ids())

    def question_ids(self):
        c = self.m.conf
        return {q["id"] for q in c.get("pre", []) + c.get("post", []) + c.get("final", [])}

    def steps(self, state):
        c = self.m.conf
        res = []
        if c.get("pre"): res.append("pre")
        res.append("sort")
        if c.get("post"): res.append("post")
        if c.get("final"): res.append("final")
        return res

    def step_label(self, step, t):
        if step == "sort": return t("card_sort.sort")
        if step in ("pre", "post"): return t(f"module.{step}_survey")
        return t("module.final_questions")

    def handle(self, ctx, step):
        c = self.m.conf
        if step in ("pre", "post", "final"):
            return self._handle_survey(ctx, step, c.get(step, []))

        if step == "sort":
            if request.method == "POST":
                # Expecting JSON: { card_id: category_name, ... }
                data = request.get_json(silent=True) or request.form.to_dict() or {}
                if not isinstance(data, dict):
                    abort(400)
                storage.save_page(ctx.conn, ctx.session["id"], step, data)
                ctx.conn.commit()
                return ADVANCE
                
            return ctx.render("modules/card_sort.html", conf=c)
            
        abort(404)

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

    def report(self, ctx):
        c = self.m.conf
        sessions = self._finished_runs(ctx.conn)
        n = len(sessions)
        
        predefined_cats = list(c.get("categories", []))
        all_cats_set = set(predefined_cats)
        created_counts = {}
        
        matrix = {card["id"]: {} for card in c["cards"]}
        
        for s in sessions:
            sort_data = storage.page_answers(ctx.conn, s["id"], "sort")
            seen_created_in_session = set()
            for card_id, cat_name in sort_data.items():
                if not cat_name:
                    continue
                all_cats_set.add(cat_name)
                if cat_name not in predefined_cats:
                    seen_created_in_session.add(cat_name)
                if card_id in matrix:
                    matrix[card_id][cat_name] = matrix[card_id].get(cat_name, 0) + 1
            for cat_name in seen_created_in_session:
                created_counts[cat_name] = created_counts.get(cat_name, 0) + 1

        extra_cats = sorted(all_cats_set - set(predefined_cats))
        all_categories = predefined_cats + extra_cats

        card_stats = []
        clear_home_count = 0
        for card in c["cards"]:
            counts = matrix.get(card["id"], {})
            top_cat = None
            max_c = 0
            for cat, count in counts.items():
                if count > max_c:
                    max_c = count
                    top_cat = cat
            agree = (max_c / n) if n > 0 else 0
            if agree >= 0.7:
                clear_home_count += 1
            card_stats.append({
                "id": card["id"],
                "label": card["label"],
                "counts": counts,
                "top_cat": top_cat,
                "max_count": max_c,
                "agree": agree,
                "agree_pct": round(agree * 100),
            })

        card_stats.sort(key=lambda x: x["agree"], reverse=True)
        created_list = sorted(created_counts.items(), key=lambda x: (-x[1], x[0]))

        if n == 0:
            headline = "No responses yet."
        elif clear_home_count == len(card_stats):
            headline = f"All {len(card_stats)} cards have a clear home (at least 70% agreement)."
        elif clear_home_count == 0:
            headline = f"None of the {len(card_stats)} cards reached 70% agreement on a single category."
        else:
            headline = f"{clear_home_count} of {len(card_stats)} cards have a clear home (at least 70% agreement)."

        return ctx.render_fragment(
            "modules/card_sort_report.html",
            sessions=sessions,
            n=n,
            cards=c["cards"],
            card_stats=card_stats,
            all_categories=all_categories,
            created_list=created_list,
            headline=headline,
            matrix=matrix,
        )

    def _finished_runs(self, conn):
        sessions = conn.execute(
            "SELECT s.id, p.identity, s.finished_at FROM sessions s "
            "JOIN participants p ON p.id = s.participant_id "
            "WHERE s.module_id = ? AND s.finished_at IS NOT NULL "
            "ORDER BY s.finished_at",
            (self.m.id,)
        ).fetchall()
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
