"""Question engine shared by every module type that asks questions.

Supported types: single, multi, scale, matrix, text. Any question can carry
`show_if: {ref: <question or module.question>, in: [...] | equals: x | not_in: [...]}`.
Answers are stored as {question_id: value}; a matrix stores {row_value: int | "na"}.
"""
from statistics import mean

from .config import MAX_OPTIONS_PER_QUESTION, MAX_QUESTIONS_PER_MODULE, MAX_ROWS_PER_MATRIX

TYPES = {"single", "multi", "scale", "matrix", "text"}
NA = "na"


def normalize_options(raw, scope, where):
    """Accepts a list of strings, [value, label] pairs, {value, label} dicts, or a mapping."""
    out = []
    if isinstance(raw, dict):
        raw = [{"value": k, "label": v} for k, v in raw.items()]
    if not isinstance(raw, list) or not raw:
        scope.add(f"{where}: needs a non-empty options list")
        return out
    if len(raw) > MAX_OPTIONS_PER_QUESTION:
        scope.add(f"{where}: has {len(raw)} options, maximum is {MAX_OPTIONS_PER_QUESTION}")
        return out
    for item in raw:
        if isinstance(item, dict):
            value, label = item.get("value"), item.get("label", item.get("value"))
        elif isinstance(item, (list, tuple)) and len(item) == 2:
            value, label = item
        else:
            value = label = item
        if value is None:
            scope.add(f"{where}: option without a value")
            continue
        out.append((str(value), str(label)))
    values = [v for v, _ in out]
    if len(values) != len(set(values)):
        scope.add(f"{where}: option values must be unique")
    return out


def scale_labels(q, points, scope, where):
    labels = q.get("labels")
    if labels is None:
        return [""] * points
    labels = [str(x) for x in labels]
    if len(labels) == 2 and points > 2:
        return [labels[0]] + [""] * (points - 2) + [labels[1]]
    if len(labels) != points:
        scope.add(f"{where}: labels needs 2 (ends) or {points} entries")
        return [""] * points
    return labels


def normalize(questions, scope, where="questions"):
    if not isinstance(questions, list):
        scope.add(f"{where} must be a list")
        return []
    if len(questions) > MAX_QUESTIONS_PER_MODULE:
        scope.add(f"{where}: has {len(questions)} questions, maximum is {MAX_QUESTIONS_PER_MODULE}")
        return []
    out, seen = [], set()
    for i, raw in enumerate(questions):
        w = f"{where}[{i}]"
        if not isinstance(raw, dict):
            scope.add(f"{w} must be a mapping")
            continue
        qid, qtype = str(raw.get("id", "")), raw.get("type")
        if not qid:
            scope.add(f"{w}: id is required")
            continue
        w = f"{where}.{qid}"
        if qid in seen:
            scope.add(f"{w}: duplicate id")
        seen.add(qid)
        if qtype not in TYPES:
            scope.add(f"{w}: type must be one of {sorted(TYPES)}")
            continue
        q = {"id": qid, "type": qtype, "label": str(raw.get("label", qid)), "hint": str(raw.get("hint") or ""),
             "required": bool(raw.get("required", True)), "show_if": raw.get("show_if")}
        if qtype in ("single", "multi"):
            q["options_from"] = raw.get("options_from")
            q["options"] = [] if q["options_from"] else normalize_options(raw.get("options"), scope, w)
            q["columns"] = int(raw.get("columns", 2 if qtype == "single" else 1))
        if qtype == "multi":
            q["max"] = raw.get("max")
        if qtype in ("scale", "matrix"):
            q["points"] = int(raw.get("points", 5))
            if not 2 <= q["points"] <= 11:
                scope.add(f"{w}: points must be between 2 and 11")
                q["points"] = 5
            q["labels"] = scale_labels(raw, q["points"], scope, w)
        if qtype == "matrix":
            rows = raw.get("rows")
            if isinstance(rows, dict) and len(rows) > MAX_ROWS_PER_MATRIX:
                scope.add(f"{w}: has {len(rows)} rows, maximum is {MAX_ROWS_PER_MATRIX}")
            q["rows"] = normalize_options(raw.get("rows"), scope, f"{w}.rows")
            q["na_label"] = str(raw["na_label"]) if raw.get("na_label") else None
        if qtype == "text":
            q["long"] = bool(raw.get("long", True))
            q["max_length"] = int(raw.get("max_length", 4000))
            q["placeholder"] = str(raw.get("placeholder") or "")
        cond = q["show_if"]
        if cond is not None and (not isinstance(cond, dict) or "ref" not in cond
                                 or not any(k in cond for k in ("in", "equals", "not_in"))):
            scope.add(f"{w}: show_if needs ref plus one of in, equals, not_in")
        out.append(q)
    by_id = {q["id"]: q for q in out}
    for q in out:
        src = q.get("options_from")
        if src:
            target = by_id.get(src)
            if target is None or target["type"] not in ("matrix", "single", "multi"):
                scope.add(f"{where}.{q['id']}: options_from must name a matrix, single or multi question in the same module")
            else:
                q["options"] = target["rows"] if target["type"] == "matrix" else target["options"]
    return out


def check_refs(questions, scope, project, module_id, local_ids):
    for q in questions:
        cond = q.get("show_if")
        if isinstance(cond, dict) and "ref" in cond:
            ref = str(cond["ref"])
            if "." not in ref and ref not in local_ids:
                scope.add(f"{q['id']}: show_if ref '{ref}' is not a question in this module")
            elif "." in ref:
                scope.ref(project, module_id, ref, f"{q['id']}.show_if")


def condition_holds(cond, value):
    values = value if isinstance(value, list) else [value]
    values = [str(v) for v in values if v not in (None, "")]
    if "equals" in cond:
        return str(cond["equals"]) in values
    if "in" in cond:
        return any(v in {str(x) for x in cond["in"]} for v in values)
    if "not_in" in cond:
        return bool(values) and not any(v in {str(x) for x in cond["not_in"]} for v in values)
    return True


def resolve(ref, local, lookup):
    """Local refs read from this module's answers; dotted refs go through lookup(module, qid)."""
    if "." in ref:
        mod, _, qid = ref.rpartition(".")
        return lookup(mod, qid)
    return local.get(ref)


def visible(q, local, lookup):
    cond = q.get("show_if")
    return True if not cond else condition_holds(cond, resolve(str(cond["ref"]), local, lookup))


def client_condition(q, page_ids):
    """Conditions on questions of the same page are toggled in the browser; everything else is
    decided on the server before rendering."""
    cond = q.get("show_if")
    if cond and "." not in str(cond["ref"]) and cond["ref"] in page_ids:
        return cond
    return None


def parse(questions, form, prior, lookup):
    """Validates a submitted page. `prior` holds answers from earlier pages of the same module."""
    data, errors = {}, {}
    for q in questions:
        if not visible(q, {**prior, **data}, lookup):
            continue
        qid, t = q["id"], q["type"]
        err = None
        if t == "single":
            val = form.get(qid, "")
            if val in {v for v, _ in q["options"]}:
                data[qid] = val
            elif q["required"]:
                err = "choose_one"
        elif t == "multi":
            allowed = {v for v, _ in q["options"]}
            vals = [v for v in form.getlist(qid) if v in allowed]
            data[qid] = vals
            if q["required"] and not vals:
                err = "choose_at_least_one"
            elif q.get("max") and len(vals) > int(q["max"]):
                err = "choose_at_most"
        elif t == "scale":
            val = form.get(qid, "")
            if val.isdigit() and 1 <= int(val) <= q["points"]:
                data[qid] = int(val)
            elif q["required"]:
                err = "choose_one"
        elif t == "matrix":
            rows = {}
            for rv, _ in q["rows"]:
                val = form.get(f"{qid}.{rv}", "")
                if val.isdigit() and 1 <= int(val) <= q["points"]:
                    rows[rv] = int(val)
                elif val == NA and q["na_label"]:
                    rows[rv] = NA
                elif q["required"]:
                    err = "answer_every_row"
            data[qid] = rows
        elif t == "text":
            val = form.get(qid, "").strip()[:q["max_length"]]
            data[qid] = val
            if q["required"] and not val:
                err = "write_something"
        if err:
            errors[qid] = err
    return data, errors


def summarize(questions, responses):
    """responses: list of (identity, answers dict). Returns one summary per question."""
    out = []
    picks_from = {}
    for q in questions:
        if q.get("options_from"):
            picks_from.setdefault(q["options_from"], []).append(q["id"])
    for q in questions:
        qid, t = q["id"], q["type"]
        answered = [(who, a[qid]) for who, a in responses if qid in a and a[qid] not in (None, "", [], {})]
        s = {"q": q, "n": len(answered)}
        if t in ("single", "multi"):
            counts = {v: 0 for v, _ in q["options"]}
            for _, val in answered:
                for v in (val if isinstance(val, list) else [val]):
                    if v in counts:
                        counts[v] += 1
            s["counts"] = sorted(((label, counts[v]) for v, label in q["options"]), key=lambda x: -x[1]) \
                if t == "multi" else [(label, counts[v]) for v, label in q["options"]]
        elif t == "scale":
            vals = []
            for _, v in answered:
                try:
                    vals.append(float(v))
                except (ValueError, TypeError):
                    pass
            s["mean"] = mean(vals) if vals else None
            s["dist"] = [sum(1 for v in vals if v == i) for i in range(1, q["points"] + 1)]
        elif t == "matrix":
            pick_counts = {}
            for pid in picks_from.get(qid, []):
                for _, a in responses:
                    for v in a.get(pid) or []:
                        pick_counts[v] = pick_counts.get(v, 0) + 1
            rows = []
            top = q["points"] - 1  # "top two" boxes
            for rv, label in q["rows"]:
                vals = []
                for _, a in answered:
                    if rv in a and a[rv] != NA:
                        try:
                            vals.append(float(a[rv]))
                        except (ValueError, TypeError):
                            pass
                rows.append({"label": label, "n": len(vals),
                             "na": sum(1 for _, a in answered if a.get(rv) == NA),
                             "mean": mean(vals) if vals else None,
                             "top_pct": (sum(1 for v in vals if v >= top) / len(vals)) if vals else None,
                             "picks": pick_counts.get(rv, 0)})
            s["has_picks"] = qid in picks_from
            s["rows"] = sorted(rows, key=lambda r: (-r["picks"], -(r["mean"] or 0))) if s["has_picks"] else rows
        elif t == "text":
            s["texts"] = [(who, v) for who, v in answered]
        out.append(s)
    return out


def flatten(questions, answers):
    """Column name -> cell value, for CSV export."""
    cols = {}
    for q in questions:
        val = answers.get(q["id"])
        if q["type"] == "matrix":
            for rv, _ in q["rows"]:
                cols[f"{q['id']}[{rv}]"] = (val or {}).get(rv, "")
        elif q["type"] == "multi":
            cols[q["id"]] = "|".join(val or [])
        else:
            cols[q["id"]] = "" if val is None else val
    return cols


def display(q, val):
    """Human-readable answer, for admin detail pages."""
    if val in (None, "", [], {}):
        return "-"
    t = q["type"]
    if t in ("single", "multi"):
        labels = dict(q["options"])
        return ", ".join(labels.get(v, v) for v in (val if isinstance(val, list) else [val]))
    if t == "scale":
        label = q["labels"][val - 1] if 0 < val <= len(q["labels"]) else ""
        return f"{val}/{q['points']}" + (f" ({label})" if label else "")
    if t == "matrix":
        rows = dict(q["rows"])
        return "; ".join(f"{rows.get(k, k)}: {q['na_label'] if v == NA else v}" for k, v in val.items())
    return str(val)
