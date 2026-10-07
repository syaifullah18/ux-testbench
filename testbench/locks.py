"""Pre-registered analysis rules.

When a study goes live, the fields that decide an A/B verdict (variants, baseline, order, task
ids, answer keys, goals, first-click targets and the decision rule) are snapshotted into the
study database. From then on Studio and `python -m testbench check` refuse a change to them,
and sessions started before the lock count as pilots, left out of results. Unlocking needs a
reason and is written to the audit log; locking again starts a new analysis period, and only
sessions started after it are counted.
"""
import json

from . import storage

KEY = "lock:"


def snapshot(module):
    """The analysis-defining part of a module's config, or None for types without a verdict."""
    if module.type == "journey_test":
        return _journey_snapshot(module)
    if module.type != "ab_test":
        return None
    c = module.conf
    root = module.project.dir.resolve()
    variants = {}
    for k, v in c["variants"].items():
        try:
            variants[k] = v["path"].relative_to(root).as_posix()
        except ValueError:
            variants[k] = v["path"].name
    return {
        "variants": variants, "baseline": c["baseline"], "order": c["order"],
        "tasks": [{"id": t["id"], "accept": t["accept"], "contains": t["contains"], "goals": t.get("goals"),
                   "end_on_goal": t.get("end_on_goal"), "first_click": t.get("first_click")} for t in c["tasks"]],
        "decision_rule": c["rule"],
    }


def _rel(path, root):
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return path.name


def _journey_snapshot(module):
    """Flows are analysis-defining as a whole: steps, their order, roles, files and goals."""
    c = module.conf
    root = module.project.dir.resolve()
    return {
        "variants": {k: [{"id": s["id"], "role": s["role"], "file": _rel(s["path"], root), "goals": s["goals"]}
                         for s in v["steps"]] for k, v in c["variants"].items()},
        "baseline": c["baseline"], "order": c["order"],
        "tasks": [],
        "decision_rule": c["rule"],
    }


def get(conn, module_id):
    row = conn.execute("SELECT value FROM meta WHERE key = ?", (KEY + module_id,)).fetchone()
    return storage.loads(row["value"], None) if row else None


def all_locks(conn):
    rows = conn.execute("SELECT key, value FROM meta WHERE key LIKE ?", (KEY + "%",)).fetchall()
    return {r["key"][len(KEY):]: storage.loads(r["value"], {}) for r in rows}


def locked_at(conn, module_id):
    lock = get(conn, module_id)
    return lock["at"] if lock else None


def lock_module(conn, module, ip=None):
    """Locks one module unless it already is. Returns True when a new lock was written."""
    fields = snapshot(module)
    if fields is None or get(conn, module.id):
        return False
    lock = {"at": storage.now_iso(), "fields": fields}
    conn.execute("INSERT INTO meta (key, value) VALUES (?, ?)", (KEY + module.id, json.dumps(lock)))
    storage.audit(conn, "lock_rules", ip, {"module": module.id, "fields": fields})
    return True


def lock_all(conn, project, ip=None):
    return [m.id for m in project.modules.values() if lock_module(conn, m, ip)]


def unlock(conn, module_id, reason, ip=None):
    lock = get(conn, module_id)
    if not lock:
        return False
    conn.execute("DELETE FROM meta WHERE key = ?", (KEY + module_id,))
    storage.audit(conn, "unlock_rules", ip, {"module": module_id, "reason": reason, "locked_at": lock["at"],
                                             "fields": lock["fields"]})
    return True


def changed_fields(old, new):
    """Top-level fields that differ, with task-level detail ('tasks.find.accept')."""
    out = []
    for key in ("variants", "baseline", "order", "decision_rule"):
        if old.get(key) != new.get(key):
            out.append(key)
    old_t = {t["id"]: t for t in old.get("tasks", [])}
    new_t = {t["id"]: t for t in new.get("tasks", [])}
    if list(old_t) != list(new_t):
        out.append("tasks (ids or order)")
    for tid in old_t.keys() & new_t.keys():
        for k in old_t[tid]:
            if old_t[tid][k] != new_t[tid].get(k):
                out.append(f"tasks.{tid}.{k}")
    return out


def violations(conn, project):
    """Problems for every locked module the given config would change or remove."""
    problems = []
    for mid, lock in all_locks(conn).items():
        m = project.modules.get(mid) if project else None
        where = f"modules/{mid}.yaml"
        if m is None:
            problems.append(f"{where}: analysis rules are locked since {lock['at']}; unlock the module before removing it")
            continue
        diff = changed_fields(lock["fields"], snapshot(m) or {})
        if diff:
            problems.append(f"{where}: analysis rules are locked since {lock['at']}; unlock the module to change "
                            + ", ".join(sorted(diff)))
    return problems
