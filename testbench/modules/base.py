"""Base class for module types. A module type turns one YAML file into a participant flow
(a list of steps), an admin report, a per-participant detail view, and a CSV export.

To add a type: subclass ModuleType, set `type_name`, and register it in modules/__init__.py.
"""

ADVANCE = object()  # returned by handle() when the step is complete and the flow should move on


class ModuleType:
    type_name = ""

    def __init__(self, module):
        self.m = module

    # ---- configuration
    def validate(self, raw, scope):
        """Return the normalised type-specific config. Report problems with scope.add()."""
        return {}

    def check_refs(self, scope):
        """Called after every module of the project is loaded, to check cross-module refs."""

    def question_ids(self):
        """Ids other modules may reference in show_if or audience conditions."""
        return set()

    # ---- participant flow
    def on_start(self, ctx):
        """Initial session state (for example a counterbalanced variant order)."""
        return {}

    def steps(self, state):
        raise NotImplementedError

    def step_label(self, step, t):
        return self.m.title

    def handle(self, ctx, step):
        """Render or process one step. Return a response, or ADVANCE when it is complete."""
        raise NotImplementedError

    def action(self, ctx, path):
        """Extra participant routes under /<project>/m/<module>/x/<path> (APIs, prototypes)."""
        return None

    # ---- admin
    def admin_action(self, ctx, path):
        return None

    def report(self, ctx):
        raise NotImplementedError

    def detail(self, ctx, session):
        return ""

    def save_detail(self, ctx, session, form):
        """Persist admin edits posted from the participant detail page."""

    def export(self, ctx):
        """Return (header, rows) for the module's CSV export."""
        return [], []
