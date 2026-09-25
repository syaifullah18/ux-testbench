import re
from flask import request

from .. import questions as Q
from .. import storage
from .base import ADVANCE, ModuleType


def flatten_tree(nodes, parent=None, depth=0):
    result = []
    for node in nodes:
        if not isinstance(node, dict) or not node.get("id") or not node.get("label"):
            continue
        item = {
            "id": str(node["id"]),
            "label": str(node["label"]),
            "parent": parent,
            "depth": depth,
            "is_leaf": not bool(node.get("children"))
        }
        result.append(item)
        if node.get("children"):
            result.extend(flatten_tree(node["children"], item["id"], depth + 1))
    return result


class TreeTest(ModuleType):
    """Information architecture evaluation. Users navigate a text-based tree to find an item."""
    type_name = "tree_test"

    def validate(self, raw, scope):
        out = {"tree": [], "flat_tree": [], "tasks": [], "post": [], "final": [], 
               "title": str(raw.get("title") or "Tree Test"),
               "instructions": str(raw.get("instructions") or "")}
               
        if not isinstance(raw.get("tree"), list) or not raw["tree"]:
            scope.append("tree_test needs a `tree:` list")
            return out
            
        out["tree"] = raw["tree"]
        out["flat_tree"] = flatten_tree(raw["tree"])
        valid_nodes = {n["id"] for n in out["flat_tree"]}
        leaf_nodes = {n["id"] for n in out["flat_tree"] if n["is_leaf"]}
        
        if len(valid_nodes) != len(out["flat_tree"]):
            scope.append("tree node ids must be unique")

        if not isinstance(raw.get("tasks"), list) or not raw["tasks"]:
            scope.append("tree_test needs a `tasks:` list")
            return out
            
        for i, t in enumerate(raw["tasks"]):
            tid = str(t.get("id") or "")
            if not tid or not re.fullmatch(r"[a-z0-9_-]+", tid):
                scope.append(f"tasks[{i}] needs a valid `id:` (alphanumeric/dash/underscore)")
            if not t.get("prompt"):
                scope.append(f"tasks[{i}] needs a `prompt:`")
                
            accept = [str(x) for x in (t.get("accept") or [])]
            if not accept:
                scope.append(f"tasks[{i}] needs an `accept:` list of correct node ids")
            for node_id in accept:
                if node_id not in valid_nodes:
                    scope.append(f"tasks[{i}].accept: node '{node_id}' does not exist in the tree")
                elif node_id not in leaf_nodes:
                    scope.append(f"tasks[{i}].accept: node '{node_id}' is not a leaf node (has children)")
                    
            out["tasks"].append({"id": tid, "prompt": str(t["prompt"]), "accept": accept})

        out["post"] = Q.normalize(raw.get("post", []), scope, "post")
        out["final"] = Q.normalize(raw.get("final", []), scope, "final")
        
        ids = [t["id"] for t in out["tasks"]] + [q["id"] for q in out["post"]] + [q["id"] for q in out["final"]]
        if len(ids) != len(set(ids)):
            scope.append("all task ids and question ids must be unique across the module")
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
            # JS will submit path_taken (list of node IDs), final_node, time_ms, gave_up
            data = request.get_json(silent=True) or {}
            
            storage.save_page(ctx.conn, ctx.session["id"], step, {
                "path_taken": data.get("path_taken", []),
                "final_node": data.get("final_node"),
                "time_ms": data.get("time_ms", 0),
                "gave_up": bool(data.get("gave_up")),
                "success": data.get("final_node") in task["accept"] if not data.get("gave_up") else False
            })
            ctx.conn.commit()
            return ADVANCE

        return ctx.render("modules/tree_test_tasks.html", conf=self.m.conf, task=task, task_idx=idx, total=len(self.m.conf["tasks"]))

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
        from flask import abort
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
        
        task_results = []
        for i, t in enumerate(self.m.conf["tasks"]):
            results = []
            for _, pages, _ in responses:
                page_data = pages.get(f"t{i+1}")
                if page_data:
                    results.append(page_data)
                    
            success_count = sum(1 for r in results if r.get("success"))
            gave_up_count = sum(1 for r in results if r.get("gave_up"))
            avg_time = sum(r.get("time_ms", 0) for r in results) / len(results) if results else 0
            
            task_results.append({
                "task": t, 
                "results": results,
                "success_rate": success_count / len(results) if results else 0,
                "gave_up_rate": gave_up_count / len(results) if results else 0,
                "avg_time_ms": avg_time
            })

        return ctx.render_fragment("modules/tree_test_report.html", n=len(responses),
                                   task_results=task_results,
                                   post_summary=Q.summarize(self.m.conf["post"], flat_responses),
                                   final_summary=Q.summarize(self.m.conf["final"], flat_responses))

    def detail(self, ctx, session):
        answers = storage.bulk_answers_by_page(ctx.conn, [session["id"]]).get(session["id"], {})
        flat = storage.bulk_module_answers(ctx.conn, [session["id"]]).get(session["id"], {})
        
        task_results = []
        for i, t in enumerate(self.m.conf["tasks"]):
            task_results.append({"task": t, "result": answers.get(f"t{i+1}")})
            
        post = [(q, Q.display(q, flat.get(q["id"]))) for q in self.m.conf["post"] if q["id"] in flat]
        final = [(q, Q.display(q, flat.get(q["id"]))) for q in self.m.conf["final"] if q["id"] in flat]
        
        return ctx.render_fragment("modules/tree_test_detail.html", task_results=task_results, post=post, final=final)

    def export(self, ctx):
        responses = self._responses(ctx)
        c = self.m.conf
        
        header = ["participant", "task", "final_node", "success", "gave_up", "time_ms", "path_taken"]
        post_cols = list(Q.flatten(c["post"], {}))
        final_cols = list(Q.flatten(c["final"], {}))
        header += [f"post.{k}" for k in post_cols] + [f"final.{k}" for k in final_cols]
        
        out = []
        for identity, pages, flat in responses:
            post = list(Q.flatten(c["post"], flat).values())
            final = list(Q.flatten(c["final"], flat).values())
            for i, t in enumerate(c["tasks"]):
                result = pages.get(f"t{i+1}", {})
                path_str = " > ".join(result.get("path_taken", []))
                out.append([
                    identity, t["id"],
                    result.get("final_node", ""), 
                    result.get("success", False), 
                    result.get("gave_up", False), 
                    result.get("time_ms", ""),
                    path_str
                ] + post + final)
                
        return header, out

    def export_cols(self, ctx, session_ids):
        if not session_ids:
            return [], {}
        c = self.m.conf
        headers = []
        for i, t in enumerate(c.get("tasks", [])):
            for col in ["final_node", "success", "gave_up", "time_ms", "path_taken"]:
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
                result = pages.get(f"t{i+1}", {})
                for col in ["final_node", "success", "gave_up", "time_ms"]:
                    cols[f"{self.m.id}.{t['id']}.{col}"] = result.get(col, "")
                cols[f"{self.m.id}.{t['id']}.path_taken"] = " > ".join(result.get("path_taken", []))
                    
            for k, v in Q.flatten(c.get("post", []), flat).items():
                cols[f"{self.m.id}.post.{k}"] = v
            for k, v in Q.flatten(c.get("final", []), flat).items():
                cols[f"{self.m.id}.final.{k}"] = v
                
            out[sid] = cols
        return headers, out
