"""Per-request context handed to module types, so they never touch Flask globals for project,
participant or session lookups, and never build URLs by hand."""
import re

from flask import render_template, session as cookie, url_for
from markupsafe import Markup

from . import questions as Q
from . import storage
from .i18n import translator


class Ctx:
    def __init__(self, project, module=None, participant=None, session=None, admin=False):
        self.project = project
        self.module = module
        self.participant = participant
        self.session = session
        self.admin = admin
        self.conn = storage.connect(project.slug)
        self.t = translator(project.locale)
        self.state = storage.loads(session["state"], {}) if session else {}
        self.steps = module.impl.steps(self.state) if module and session else []

    # ---- answers across modules, for show_if and audience conditions
    def lookup(self, module_id, qid):
        if self.participant is None:
            return None
        s = storage.session_for(self.conn, self.participant["id"], module_id)
        return storage.module_answers(self.conn, s["id"]).get(qid) if s else None

    # ---- urls
    def step_url(self, step):
        return url_for("web.module_step", slug=self.project.slug, mid=self.module.id, step=step)

    def action_url(self, path):
        endpoint = "admin.module_action" if self.admin else "web.module_action"
        return url_for(endpoint, slug=self.project.slug, mid=self.module.id, path=path)

    def home_url(self, **kw):
        return url_for("web.home", slug=self.project.slug, **kw)

    def advance_url(self):
        """Moves the session to its next step (finishing the module after the last one) and
        returns where the participant should go."""
        i = self.steps.index(self.session["step"])
        if i + 1 < len(self.steps):
            nxt = self.steps[i + 1]
            self.conn.execute("UPDATE sessions SET step = ? WHERE id = ?", (nxt, self.session["id"]))
            self.conn.commit()
            return self.step_url(nxt)
        self.conn.execute("UPDATE sessions SET step = 'done', finished_at = ? WHERE id = ?",
                          (storage.now_iso(), self.session["id"]))
        self.conn.commit()
        self._check_notifications()
        return self.home_url(done=self.module.id)
        
    def _check_notifications(self):
        notifs = getattr(self.project, "notifications", {})
        if not notifs or not isinstance(notifs, dict):
            return
        url = notifs.get("webhook")
        threshold = notifs.get("threshold", 10)
        if not url or not isinstance(threshold, int) or threshold <= 0:
            return
            
        completed = self.conn.execute("SELECT COUNT(finished_at) FROM sessions WHERE module_id = ?",
                                      (self.module.id,)).fetchone()[0]
        if completed > 0 and completed % threshold == 0:
            import urllib.request, json
            try:
                data = json.dumps({"event": "module_completed", "project": self.project.slug, "module": self.module.id, "count": completed}).encode("utf-8")
                req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
                urllib.request.urlopen(req, timeout=3)
            except Exception:
                pass # Fire and forget, ignore errors

    # ---- rendering
    def progress(self):
        if not self.module or not self.session or self.session["step"] not in self.steps:
            return None
        step = self.session["step"]
        return {"label": self.module.impl.step_label(step, self.t), "index": self.steps.index(step) + 1,
                "total": len(self.steps), "module": self.module.title}

    def _vars(self, kw):
        return {"ctx": self, "project": self.project, "module": self.module, "t": self.t,
                "participant": self.participant, "progress": self.progress(), "Q": Q, **kw}

    def render(self, template, **kw):
        return render_template(template, **self._vars(kw))

    def render_fragment(self, template, **kw):
        return Markup(render_template(template, **self._vars(kw)))


# ---------------------------------------------------------------- audience and status

def audience_ok(ctx, module):
    aud = module.audience or {}
    pattern = aud.get("identity_pattern")
    if pattern and not re.search(pattern, ctx.participant["identity"]):
        return False
    when = aud.get("when")
    if when:
        return Q.condition_holds(when, Q.resolve(str(when["ref"]), {}, ctx.lookup)
                                 if "." in str(when["ref"]) else ctx.lookup(module.id, when["ref"]))
    return True


def module_status(ctx, module):
    """new, in_progress, done or locked (a required module is not finished yet)."""
    s = storage.session_for(ctx.conn, ctx.participant["id"], module.id)
    if s and s["finished_at"]:
        return "done", s
    for req in module.requires:
        rs = storage.session_for(ctx.conn, ctx.participant["id"], req)
        if not (rs and rs["finished_at"]):
            return "locked", s
    return ("in_progress" if s else "new"), s


def participant_id(slug):
    return (cookie.get("tb_p") or {}).get(slug)


def set_participant(slug, pid):
    data = dict(cookie.get("tb_p") or {})
    if pid is None:
        data.pop(slug, None)
    else:
        data[slug] = pid
    cookie["tb_p"] = data


def is_admin(slug):
    import os
    if os.environ.get("TESTBENCH_MODE", "internal") == "public":
        if slug == "example":
            return True
        from . import auth, users
        user = auth.current_user()
        return users.can(user, slug, "view")
    
    granted = cookie.get("tb_admin") or []
    return "*" in granted or slug in granted


def can_edit(slug):
    import os
    if os.environ.get("TESTBENCH_MODE", "internal") == "public":
        from . import auth, users
        user = auth.current_user()
        return users.can(user, slug, "edit")
    return is_admin(slug)


def is_super():
    import os
    if os.environ.get("TESTBENCH_MODE", "internal") == "public":
        from . import auth
        user = auth.current_user()
        return user is not None and user["is_platform_admin"]
    return "*" in (cookie.get("tb_admin") or [])
