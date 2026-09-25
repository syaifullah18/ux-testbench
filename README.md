# UX Testbench

> Run user research for many products from one small app: discovery surveys and moderated A/B tests of static prototypes, managed from the browser.

![Python](https://img.shields.io/badge/python-3.10%2B-3776AB?style=flat-square&logo=python&logoColor=white)
![Version](https://img.shields.io/badge/version-0.3.0-blue?style=flat-square)
![License](https://img.shields.io/badge/license-MIT-green?style=flat-square)

---

## Why

Product teams usually have more improvement ideas than they can build. A common research sequence helps filter them:

1. **Discover.** Ask users where they get stuck before choosing solutions.
2. **Filter.** Turn the top pain points into prototypes and test them against the current design, before development.
3. **Refine.** Re-run the same tasks on the built version.

UX Testbench covers steps 1 and 2, and step 3 whenever the built version can be served as HTML. One deployment hosts any number of projects. Each project has its own URL, home screen, passcodes, branding, language and database.

## How it is used

| Who | Where | What they do |
| --- | --- | --- |
| Research lead | `/admin/` (superadmin) | Creates, imports, duplicates and deletes projects |
| UI/UX researcher | `/<project>/admin/studio/` | Edits questions and tasks, uploads prototypes, sets passcodes, exports the project |
| UI/UX researcher | `/<project>/admin/` | Reads results per module, grades tasks, downloads CSV |
| Participant | `/<project>/` | Signs in, sees a home screen of activities, completes them |

Nothing needs a code change or a redeploy. Every edit in the Studio is validated against the whole project before it is saved, so a typo cannot break a live study. The previous version of each file is kept and can be restored.

### Participants

After signing in, participants land on a **home screen** listing every activity (module) they are allowed to take, with its status (not started, in progress, done, or locked until a required module is finished) and an estimated duration. Each module is a linear flow. Progress is saved after every step, so participants can leave and resume.

Two module types are included:

| Type | What it does |
| --- | --- |
| `survey` | One or more pages of questions: single choice, multiple choice, scale, matrix and text. Questions can be shown conditionally, even based on answers in another module (for example, a different set of journey stages per role from a profile survey). |
| `ab_test` | Within-subject test of two or more static HTML prototypes. The same timed tasks run on every variant, in counterbalanced order, followed by a survey per variant and an optional preference question. It records time on task, clicks, click path, first click, scroll reversals, viewport width, answers checked against an answer key, and ease (SEQ 1 to 7). |

### Researchers

Each project's admin shows:

- A report per module, with aggregates for every question type. For A/B tests: success rate, median time, first clicks, splits by device and by order, and a configurable **decision rule** comparing each challenger with the baseline.
- A per-participant page to grade observational tasks and add observer notes.
- CSV export per module.
- Participant deletion and a project-wide reset. Other projects are never touched.

---

## Quick start

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env              # set SECRET_KEY and SUPERADMIN_PASSCODE
python -m testbench run --debug   # http://127.0.0.1:5000/admin/
```

1. Open `/admin/` and enter the superadmin passcode.
2. Under **New project**, start from a blank project or from a copy of the bundled [example](projects/example/), a fictional library portal with a profile survey, a journey survey and an A/B test.
3. In the project's **Studio**, set the participant and admin passcodes, add modules, and upload prototypes.
4. Share `/<project>/` and the participant passcode.

## Where projects live

| Location | In git | Editable in the Studio | Use for |
| --- | --- | --- | --- |
| `projects/example/` | yes | no (duplicate it first) | The public demo and reference |
| `DATA_DIR/projects/` (default `instance/projects/`) | no | yes | Every real study |
| Extra `PROJECTS_DIRS` | your choice | no | Studies you keep in a separate private repository |

Real studies therefore never end up in this repository. To keep a study under version control privately, export it as a zip from the Studio, or keep it in a private repository listed in `PROJECTS_DIRS`.

## Configuration

A project is a folder holding `project.yaml` plus one YAML file per module. The Studio edits these same files, so both routes stay interchangeable. The Studio editor includes a syntax cheat sheet; [docs/configuration.md](docs/configuration.md) has the full reference. The essentials:

| Setting | Options |
| --- | --- |
| `identity.mode` | `code` (codes you hand out, matched against `pattern`), `email` (optionally limited to `domains`), `anonymous` (the app issues a resume code) |
| `access` | `passcode` (the project stays closed until a passcode is set) or `open` |
| `locale` | UI language: `en`, `id`. Add more under `testbench/locales/`. |
| module `requires` | Modules that must be finished first. |
| module `audience` | `identity_pattern` (regex on the participant code) and/or `when` (a condition on another module's answer). |
| `ab_test.order` | `rotate` (cyclic Latin square), `code_parity` (by the number in the participant code), `random`, `fixed` |

The command line offers the same basics for people who prefer files:

```bash
python -m testbench check                 # validate every project
python -m testbench new my-study          # create DATA_DIR/projects/my-study
```

### Environment

| Variable | Purpose |
| --- | --- |
| `SECRET_KEY` | Session signing key. Set it, or sessions reset on every restart. |
| `SUPERADMIN_PASSCODE` | Opens `/admin/` and every project's Studio and results. |
| `DATA_DIR` | Databases, the passcode store and Studio projects. Default `instance`. Back it up. |
| `PROJECTS_DIRS` | Read-only project folders, joined with `:` (`;` on Windows). Default `projects`. |
| `<SLUG>_PASSCODE`, `<SLUG>_ADMIN_PASSCODE` | Optional. Overrides the Studio passcode, for deployments managed as code. |
| `MAX_UPLOAD_MB` | Upload limit. Default 50. |
| `SESSION_COOKIE_SECURE` | Set to `1` behind HTTPS. |

---

## Deployment

It's a standard Flask app (`testbench:create_app()`):

```bash
pip install gunicorn
gunicorn -w 2 -b 0.0.0.0:8000 "testbench:create_app()"
```

- Put it behind HTTPS and set `SESSION_COOKIE_SECURE=1`.
- Persist and back up `DATA_DIR`. It holds all studies and all responses.
- SQLite fits research-sized traffic (dozens of concurrent participants).
- Participant pages load Tailwind, fonts and icons from public CDNs, so participants need internet access.

## Security and privacy

- **Passcodes.** Passcodes set in the Studio are stored as salted hashes. Projects without a passcode stay closed, unless `access: open`.
- **Uploads.** Uploads are limited to web file types, zip archives are checked for path traversal and size, and deleting a file still used by a module is refused.
- **Prototypes run with the app's permissions.** Uploaded prototypes are served from the same origin as the app, so the task runner can measure interactions inside them. Their scripts can therefore do anything a logged-in page can. Only give Studio access (superadmin or project admin) to people you would trust to deploy code.
- **What is collected.** No names are collected unless a project asks for them. A/B tests record time, clicks, labels of clicked elements, scroll direction changes and viewport width inside the prototype. Text typed into a prototype is never recorded.
- **Consent and deletion.** Tell participants what is recorded through the `consent` text. Delete a project's data from its admin, or delete the project with its data from the Studio, when the study ends.

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

Register the class in `testbench/modules/__init__.py`, and add a starter file under `testbench/scaffold/templates/<type>.yaml` so the Studio can offer it.

## Development

```bash
pip install -r requirements-dev.txt
python -m pytest -q
```

| Path | Contents |
| --- | --- |
| [testbench/config.py](testbench/config.py) | Project loading, validation, hot reload |
| [testbench/studio.py](testbench/studio.py) | Studio: create, import, edit, upload, export, delete |
| [testbench/web.py](testbench/web.py), [admin.py](testbench/admin.py) | Participant and admin routes |
| [testbench/passcodes.py](testbench/passcodes.py) | Hashed passcode store |
| [testbench/storage.py](testbench/storage.py) | Per-project SQLite schema |
| [testbench/questions.py](testbench/questions.py) | Question engine |
| [testbench/modules/](testbench/modules/) | Module types |
| [testbench/static/task-runner.js](testbench/static/task-runner.js) | A/B task panel and prototype instrumentation |
| [tests/](tests/) | Config, full example flow, isolation, Studio |

## License

[MIT](LICENSE)
