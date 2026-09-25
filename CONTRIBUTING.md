# Contributing to UX Testbench

UX Testbench is designed around a plugin architecture for research modules. The core platform handles routing, authentication, project isolation, participant sessions, CSRF protection, and SQLite persistence.

As a contributor, the most impactful way to help is by adding a new **Module Type** (e.g., Tree Testing, 5-Second Test, Card Sorting).

## How to Build a Module Type

Every module inherits from the `ModuleType` base class in `testbench/modules/base.py` and must be registered in `testbench/modules/__init__.py`.

### The Contract

A module must implement the following methods:

1. **`validate(self, raw, scope)`**
   Parses and validates the raw YAML configuration. Returns a cleaned, normalized dictionary. Any errors should be added via `scope.add("Error message")`.

2. **`check_refs(self, scope)`**
   Validates cross-module references (e.g., if a question uses `show_if` pointing to another module).

3. **`question_ids(self)`**
   Returns a set of all question IDs defined in this module.

4. **`steps(self, state)`**
   Returns a list of step strings (e.g., `["task1", "task2", "survey", "final"]`) that define the linear progression through the module.

5. **`step_label(self, step, t)`**
   Returns a human-readable label for a step (used in the progress bar).

6. **`handle(self, ctx, step)`**
   The core execution method. It receives a `Ctx` object and the current step string.
   - For `GET` requests, it should render an HTML template (e.g., `return ctx.render("my_module/step.html")`).
   - For `POST` requests, it should process the submission, save to the database via `storage.save_page()`, and return the `ADVANCE` sentinel object to move to the next step.

7. **`action(self, ctx, path)`**
   Handles custom routes for the module (e.g., serving prototype assets or processing API endpoints). Returns a Flask Response.

### Admin & Reporting

Modules also provide their own reporting logic:

8. **`admin_action(self, ctx, path)`**
   Handles custom admin routes for this module.

9. **`report(self, ctx)`**
   Renders the HTML fragment for the module's aggregate report view (e.g., statistical results, charts). Use `ctx.render_fragment()`.

10. **`detail(self, ctx, session)`**
    Renders the HTML fragment for a single participant's session detail view.

11. **`export(self, ctx)`**
    Returns `(headers, rows)` for the module's dedicated CSV export.

12. **`export_cols(self, ctx, session_ids)`**
    Returns `(headers, dict_mapping_sid_to_cols)` for the platform-wide combined CSV export. Prefix your columns with `f"{self.m.id}."` to avoid collisions.

### Persistence

Use the `storage.py` helper functions:
- `storage.save_page(conn, session_id, step, dict_data)`
- `storage.page_answers(conn, session_id, step)`
- `storage.bulk_module_answers(conn, session_ids)`

**Do not bypass the SQLite connection provided in `ctx.conn`.** The platform manages per-project database isolation automatically.

### Security

- Participant-facing templates inherit from `modules/base.html`.
- Always include `csrf_token()` in `<form>` tags.
- Use `ctx.lookup` for evaluating logic (like `show_if`) safely.

---

## Setting up a Dev Environment

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
pip install pytest pytest-cov
```

Run tests:
```bash
pytest tests/
```
