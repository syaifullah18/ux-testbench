# UX Testbench

> Run user research for many products from one small app: discovery surveys and moderated A/B tests of static prototypes, each project defined in YAML.

![Python](https://img.shields.io/badge/python-3.10%2B-3776AB?style=flat-square&logo=python&logoColor=white)
![Version](https://img.shields.io/badge/version-0.2.0-blue?style=flat-square)

---

## Why

Product teams usually have more improvement ideas than they can build. A common research sequence helps filter them:

1. **Discover.** Ask users where they get stuck before choosing solutions.
2. **Filter.** Turn the top pain points into prototypes and test them against the current design, before development.
3. **Refine.** Re-run the same tasks on the built version.

UX Testbench covers steps 1 and 2, and step 3 whenever the built version can be served as HTML. One deployment hosts any number of projects. Each project has its own URL, home screen, passcodes, branding, language and database.

## What participants see

Each project lives at `/<slug>/`. After signing in, participants land on a **home screen** listing every activity (module) they are allowed to take, with its status (not started, in progress, done, or locked until a required module is finished) and an estimated duration. Each module is a linear flow. Progress is saved after every step, so participants can leave and resume.

Two module types are included:

| Type | What it does |
| --- | --- |
| `survey` | One or more pages of questions: single choice, multiple choice, scale, matrix and text. Questions can be shown conditionally, even based on answers in another module (for example, a different set of journey stages per role from a profile survey). |
| `ab_test` | Within-subject test of two or more static HTML prototypes. The same timed tasks run on every variant, in counterbalanced order, followed by a survey per variant and an optional preference question. It records time on task, clicks, click path, first click, scroll reversals, viewport width, answers checked against an answer key, and ease (SEQ 1 to 7). |

New types plug in by subclassing one base class (see [Extending](#extending)).

## What researchers get

Each project's admin at `/<slug>/admin/`:

- A report per module, with aggregates for every question type. For A/B tests: success rate, median time, first clicks, splits by device and by order, and a configurable **decision rule** comparing each challenger with the baseline.
- A per-participant page to grade observational tasks and add observer notes.
- CSV export per module.
- Participant deletion and a project-wide reset. Other projects are never touched.

---

## Quick start

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env              # set SECRET_KEY and EXAMPLE_ADMIN_PASSCODE
python -m testbench check         # validate all project YAML
python -m testbench run --debug   # http://127.0.0.1:5000/example/
```

The bundled [example project](projects/example/) is a fictional library portal. It shows a profile survey, a journey survey with conditional matrices, and an A/B test of two events pages.

Create your own project:

```bash
python -m testbench new my-study --name "My Study"
# edit projects/my-study/project.yaml and modules/*.yaml
# set MY_STUDY_PASSCODE and MY_STUDY_ADMIN_PASSCODE, then open /my-study/
```

YAML edits are picked up without a restart. If an edit is invalid, the app keeps serving the last valid version and shows the error on `/admin/`.

## Configuration

Everything about a study lives in its project folder:

```
projects/
  my-study/
    project.yaml          # name, locale, login mode, access, consent, brand, module order
    modules/
      profile.yaml        # one file per module
      journey.yaml
      checkout-ab.yaml
    prototypes/           # static HTML for ab_test variants, plus their css/js/images
```

See [docs/configuration.md](docs/configuration.md) for the full reference. The essentials:

| Setting | Options |
| --- | --- |
| `identity.mode` | `code` (codes you hand out, matched against `pattern`), `email` (optionally limited to `domains`), `anonymous` (the app issues a resume code) |
| `access` | `passcode` (read from `<SLUG>_PASSCODE`; the project stays closed until it is set) or `open` |
| `locale` | UI language: `en`, `id`. Add more under `testbench/locales/`. |
| module `requires` | Modules that must be finished first. |
| module `audience` | `identity_pattern` (regex on the participant code) and/or `when` (a condition on another module's answer). |
| `ab_test.order` | `rotate` (cyclic Latin square), `code_parity` (by the number in the participant code), `random`, `fixed` |

### Environment

| Variable | Purpose |
| --- | --- |
| `SECRET_KEY` | Session signing key. Set it, or sessions reset on every restart. |
| `PROJECTS_DIRS` | Project folders, joined with `:` (`;` on Windows). Default `projects`. |
| `DATA_DIR` | Where each project's `<slug>.db` is created. Default `instance`. |
| `<SLUG>_PASSCODE`, `<SLUG>_ADMIN_PASSCODE` | Per-project passcodes. The slug is upper-cased, with `-` turned into `_`. |
| `SUPERADMIN_PASSCODE` | Optional. Opens every project's admin and the `/admin/` overview. |
| `SESSION_COOKIE_SECURE` | Set to `1` behind HTTPS. |

### Keeping real studies private

This repository only tracks `projects/example/`, and `.gitignore` excludes every other folder under `projects/`. Keep real studies, and any prototypes that contain client content, in a separate private repository:

```bash
PROJECTS_DIRS=projects:../acme-studies python -m testbench run
```

---

## Deployment

It's a standard Flask app (`testbench:create_app()`):

```bash
pip install gunicorn
gunicorn -w 2 -b 0.0.0.0:8000 "testbench:create_app()"
```

- Put it behind HTTPS and set `SESSION_COOKIE_SECURE=1`.
- SQLite fits research-sized traffic (dozens of concurrent participants). Back up `DATA_DIR`.
- Participant pages load Tailwind, fonts and icons from public CDNs, so participants need internet access.

## Privacy

- No names are collected unless a project asks for them. Email login stores the email as the participant identity.
- A/B tests record interaction metadata inside the prototype frame only: time, clicks, labels of clicked elements, scroll direction changes and viewport width. Keystrokes are not recorded outside the answer fields.
- Tell participants what is recorded through the `consent` text. Delete a project's data from its admin, or remove `DATA_DIR/<slug>.db`, when the study ends.

---

## Extending

Module types live in [testbench/modules/](testbench/modules/). Subclass `ModuleType` from [base.py](testbench/modules/base.py) and implement:

| Method | Purpose |
| --- | --- |
| `validate(raw, scope)` | Normalise the YAML. Report problems with `scope.add()`. |
| `steps(state)` and `handle(ctx, step)` | The participant flow. Return `ADVANCE` when a step is complete. |
| `on_start(ctx)` | Initial per-participant state, for example a variant order. |
| `action(ctx, path)` | Extra participant routes, such as APIs or served assets. |
| `report`, `detail`, `save_detail`, `export` | Admin views and CSV. |

Then register the class in `testbench/modules/__init__.py`. Questions can reuse [questions.py](testbench/questions.py) for rendering, validation, conditions and summaries.

## Development

```bash
pip install -r requirements-dev.txt
python -m pytest -q
```

| Path | Contents |
| --- | --- |
| [testbench/config.py](testbench/config.py) | Project loading, validation, hot reload |
| [testbench/web.py](testbench/web.py), [admin.py](testbench/admin.py) | Participant and admin routes |
| [testbench/context.py](testbench/context.py) | Per-request context passed to modules |
| [testbench/storage.py](testbench/storage.py) | Per-project SQLite schema |
| [testbench/questions.py](testbench/questions.py) | Question engine |
| [testbench/modules/](testbench/modules/) | Module types |
| [testbench/static/task-runner.js](testbench/static/task-runner.js) | A/B task panel and prototype instrumentation |
| [tests/](tests/) | Config validation, full example flow, project isolation, ordering |
