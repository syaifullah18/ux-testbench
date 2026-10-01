# UX Testbench

<!-- [REQUIRED] -->
> Multi-method user research platform hosting concurrent usability studies, dynamic surveys, interactive prototypes, and statistically honest evaluation.

<!-- [REQUIRED] -->
![Language](https://img.shields.io/badge/language-Python-3776AB?style=flat-square&logo=python&logoColor=white)
![Version](https://img.shields.io/badge/version-0.3.0-blue?style=flat-square)
![Framework](https://img.shields.io/badge/framework-Flask-000000?style=flat-square&logo=flask&logoColor=white)
![Tests](https://img.shields.io/badge/tests-179%2B%20passing-brightgreen?style=flat-square)
![License](https://img.shields.io/badge/license-MIT-green?style=flat-square)

---

## Why UX Testbench

Product and design teams usually have more improvement hypotheses than engineering bandwidth to build them. A disciplined, sequential user research workflow helps filter them early:

```
1. Discover (Pain Points) ──▶ 2. Filter (A/B Prototypes) ──▶ 3. Refine (Live Builds)
```

1. **Discover:** Ask users where they get stuck and map mental models before committing to solutions.
2. **Filter:** Transform top pain points into interactive prototypes or design mockups, benchmarking them against the baseline before writing production code.
3. **Refine:** Re-run the identical tasks on the built product to verify that usability gains survive development.

Most commercial user research platforms force teams into rigid single-method tests, charge exorbitant per-seat or per-participant fees, and apply misleading statistical tests (like standard $t$-tests) to tiny sample sizes ($n = 5\text{--}15$), creating false confidence. 

**UX Testbench** provides a unified, self-hostable workspace where one single deployment manages unlimited concurrent research studies. Each study operates in complete isolation with its own URL, passcodes, branding, language, and database.

---

<!-- [GUIDED] -->
## Features

### Multi-Method Research Suite
Five native testing methodologies built directly into the core engine:

- **A/B Prototype Testing (`ab_test`)**: Run within-subject comparative tests across interactive HTML prototypes or responsive media assets (mockup images, PDFs, audio, video). Features automated task timers, counterbalanced variant rotation (cyclic Latin square, code parity, or random), click path recording, scroll reversals, viewport width tracking, answer key validation, and System Ease Questionnaires (SEQ 1–7).
- **First Click Testing (`first_click`)**: Evaluate initial interface orientation and navigation instinct. Includes an interactive visual canvas where researchers draw target zones directly on interface screenshots with 8 resize handles, real-time coordinate synchronization, and percentage-based responsive scaling.
- **Card Sorting (`card_sort`)**: Uncover mental models and taxonomic groupings. Supports Open, Closed, and Hybrid card sorts with smooth drag-and-drop participant organization, custom category creation, and similarity matrix analytics.
- **Tree Testing (`tree_test`)**: Validate information architecture and menu hierarchies without visual design distractions. Parsed through clean two-space indented hierarchy syntax with directness, time-on-task, and success path scoring.
- **Dynamic Surveys (`survey`)**: Design pre-task screeners or deep post-study evaluations with single choice, multi-select, rating scales, matrix grids, and free text. Features dynamic conditional branching (`show_if`) and cross-module answer piping (`options_from`).

### Human-Centered Researcher Studio
- **5-Stage Study Lifecycle**: Seamlessly guides researchers through **Overview** (real-time participant funnels and 14-day activity sparklines), **Build** (visual module sequence builder with drag-and-drop reordering and prototype asset management), **Launch** (automated pre-flight readiness validation, shareable links, and instant QR codes), **Results** (scannable metric dashboards, session drilldowns, and observer grading), and **Settings & Data** (access controls and isolated data management).
- **Zero-Friction Prototype Ingestion**: Upload prototype ZIP archives or drop raw assets directly into the browser. Automatic ZIP extraction, entry-file validation, and file tree management keep assets organized.
- **Hot Validation**: Every change made in the Studio is validated in real time against the study's schema before saving, ensuring live studies cannot be broken by typos.

### Statistically Honest Evaluation
- **Non-Parametric Sign Test**: Replaces deceptive $t$-tests with a two-tailed Sign Test that evaluates whether Variant B outperformed Variant A while discarding extreme outlier runtimes.
- **Bootstrapped 95% Confidence Intervals**: Simulates thousands of resampling iterations to calculate realistic confidence bounds on task durations and satisfaction scores without assuming normal distribution.
- **Transparent 3-State Verdicts**: Delivers explicit, mathematically grounded outcomes:
  - `Clear Winner`: Statistically significant gain meeting your configured threshold.
  - `No Meaningful Difference`: Confirmed parity or sub-threshold difference.
  - `Not Enough Data`: Overlapping confidence intervals warning you when sample sizes are too noisy to make a call.

### Enterprise Security & Architecture
- **Dual-Domain Isolation**: Strictly segregates researcher-uploaded prototype execution (`USERCONTENT_DOMAIN`) from authenticated session cookies (`APP_DOMAIN`), preventing malicious prototype scripts from accessing administrative credentials.
- **Role-Scoped Navigation**: Enforces strict privilege boundaries. Study Admins are locked exclusively to their assigned project with global navigation stripped away, while Super Admins maintain platform diagnostics.
- **Flexible Participant Access**: Authenticate participants via pre-issued access codes (`code`), corporate email domains (`email`), or self-service anonymous sessions (`anonymous`).

---

## Researcher Workflow & Lifecycle

Every study in UX Testbench follows a structured 5-stage lifecycle:

```
Overview  ──▶  Build  ──▶  Launch  ──▶  Results  ──▶  Settings & Data
(Health)      (Modules)  (Pre-flight)  (Analytics)    (Access & Safety)
```

| Stage | Path | Key Capabilities |
|---|---|---|
| **1. Overview** | `/<slug>/admin/` | Visual pulse of study health: participant recruitment funnels, completion percentages, 14-day activity sparklines, and status tracker (`Draft` → `Pilot` → `Live` → `Analysis` → `Complete`). |
| **2. Build** | `/<slug>/admin/build/` | Drag-and-drop module sequence builder, visual module creation modal, accordion editor, and prototype asset browser (`/prototypes/`) with ZIP extraction. |
| **3. Launch** | `/<slug>/admin/launch/` | Pre-flight validation checklist (validates passcodes, modules, and prototype links), live/draft/closed toggle, shareable study URLs, and mobile QR code generator. |
| **4. Results** | `/<slug>/admin/results/` | Scannable metric cards, completion funnels, individual participant response logs (`/participants/`), observer grading notes, and filtered CSV exports (`/export/`). |
| **5. Settings & Data** | `/<slug>/admin/settings/` | Study passcodes, participant identity modes, language settings, and isolated safety actions (participant reset, study deletion) that never touch other studies. |

---

## User Roles & Access Control

| Role | Primary Entrypoint | Capabilities & Scoping |
|---|---|---|
| **Platform Super Admin** | `/admin/` (or `/app/admin/`) | Manages all studies, creates/clones/imports projects, monitors instance health and disk quotas at `/admin/instance/`, and accesses global documentation at `/admin/help/`. |
| **Study Admin / Researcher** | `/<slug>/admin/` | Scoped strictly to their assigned project. Can edit modules, upload prototypes, monitor live participants, grade qualitative responses, and export data. Global navigation is hidden. |
| **Participant** | `/<slug>/` (or `/s/<slug>/`) | Enters via access code, corporate email, or anonymous link. Lands on a personalized home screen listing available activities, durations, and dependencies. Progress saves after each step. |

---

<!-- [REQUIRED] -->
## Getting Started

### Prerequisites

- **Python**: Version `3.10` or higher
- **Git**: For version control and cloning
- **Docker & Docker Compose** *(Optional)*: Recommended for production hosting with automatic HTTPS

### Local Installation

**1. Clone the repository**
```bash
git clone https://github.com/syaifullah18/ux-testbench.git
cd ux-testbench
```

**2. Create and activate a virtual environment**
```bash
# On Linux/macOS:
python3 -m venv .venv
source .venv/bin/activate

# On Windows (PowerShell):
python -m venv .venv
.venv\Scripts\Activate.ps1
```

**3. Install dependencies**
```bash
pip install -r requirements.txt -r requirements-dev.txt
```

**4. Configure environment variables**
```bash
cp .env.example .env
```
Open `.env` and set your initial administrative credentials:
```ini
SECRET_KEY=generate_a_random_32_byte_hex_string
SUPERADMIN_PASSCODE=change_this_to_a_secure_passcode
```

**5. Seed demonstration data (optional)**
Populate the bundled example study with synthetic participants, sessions, and task results:
```bash
python -m testbench demo --n 14
```

**6. Start the development server**
```bash
python -m testbench run --debug
```
Open your browser at <http://127.0.0.1:5000/admin/> and log in with your `SUPERADMIN_PASSCODE`.

---

<!-- [REQUIRED] -->
## Usage & CLI Reference

UX Testbench includes a built-in CLI for project scaffolding, validation, operational maintenance, and test automation:

| Command | Description |
|---|---|
| `python -m testbench run [--host HOST] [--port PORT] [--debug]` | Starts the local web server (defaults to `127.0.0.1:5000`). |
| `python -m testbench check` | Validates YAML schemas, dependencies, and prototype links across all projects. |
| `python -m testbench new <slug> [--name NAME]` | Scaffolds a new study folder from template in `instance/projects/<slug>/`. |
| `python -m testbench demo [-n N]` | Seeds $N$ synthetic participants into the example study (default 30). |
| `python -m testbench create-admin <email>` | Creates a platform administrator account with email verification (public mode). |
| `python -m testbench claim <slug> <email>` | Assigns an owner to an existing project in public mode. |
| `python -m testbench retention [--ip-days N] [--close-after M]` | Maintenance cron job: prunes stale IPs, flushes spent tokens, and lists expired studies. |
| `pytest -q` | Executes the complete automated test suite (179+ tests). |

### Example CLI Workflows

```bash
# Validate all study configurations
python -m testbench check

# Scaffold a new mobile usability test
python -m testbench new checkout-v2 --name "Mobile Checkout Redesign"

# Run automated tests
pytest -q
```

---

## Operating Modes & Production Deployment

UX Testbench supports two operational architectures from a single codebase:

### 1. Internal / Single-Tenant Mode (Default)
Ideal for in-house UX teams and corporate research labs. One operator, study passcodes, no user registration required.

Run locally or with Gunicorn behind a reverse proxy:
```bash
pip install gunicorn
gunicorn -w 2 -b 127.0.0.1:8000 "testbench:create_app()"
```

### 2. Public Multi-Tenant SaaS Mode (`TESTBENCH_MODE=public`)
Converts the application into a multi-tenant platform with self-service signups, team memberships (Owner, Editor, Viewer), invitation workflows, usage quotas, abuse reports, and platform administration.

Deploy with Docker Compose and Caddy (handles automated SSL and dual-domain routing):
```bash
# Configure production domains in .env
TESTBENCH_MODE=public
APP_DOMAIN=testbench.yourdomain.com
USERCONTENT_DOMAIN=testbench-usercontent.yourdomain.com
ACME_EMAIL=ops@yourdomain.com

# Start the stack
docker compose up -d

# Create initial platform admin
docker compose exec app python -m testbench create-admin admin@yourdomain.com
```

Read **[docs/operations.md](docs/operations.md)** for complete operational runbooks, backup configurations, and DNS instructions.

---

## Project Structure & Storage Architecture

Studies live inside decoupled directories on the filesystem. Each study consists of a `project.yaml` root descriptor and individual module YAML definitions:

```
projects/
└── example/                     # Bundled read-only reference study
    ├── project.yaml             # Study metadata, identity mode, and theme
    ├── modules/
    │   ├── profile.yaml         # Survey screener
    │   ├── info-arch.yaml       # Tree testing navigation hierarchy
    │   ├── events-ab.yaml       # A/B prototype testing module
    │   └── journey.yaml         # Post-task journey survey
    └── prototypes/              # HTML prototypes, CSS, JS, and image assets
```

### Storage Locations

| Location | Tracked in Git | Editable in Studio | Purpose |
|---|---|---|---|
| `projects/example/` | Yes | Read-only (duplicate first) | Public demonstration, onboarding, and reference. |
| `DATA_DIR/projects/` *(default `instance/projects/`)* | No | Yes | Active studies created via Studio or CLI. |
| Extra `PROJECTS_DIRS` | Configurable | Read-only | Studies maintained in separate private Git repositories. |

Each study maintains its own SQLite database (`<slug>.db` in `DATA_DIR`), guaranteeing complete data isolation. Deleting or resetting one study never affects another.

---

## Configuration Reference

A study is defined by `project.yaml`. The Studio provides a built-in visual editor; raw file editing is fully supported. See **[docs/configuration.md](docs/configuration.md)** for the complete schema.

### Core `project.yaml` Settings

| Setting | Type | Description |
|---|---|---|
| `name` | string | Display name of the study. |
| `status` | string | Study lifecycle phase: `draft`, `pilot`, `live`, `analysis`, or `closed`. |
| `identity.mode` | string | `code` (pre-issued alphanumeric codes), `email` (optionally restricted to `domains`), or `anonymous` (auto-issued resume code). |
| `access` | string | `passcode` (protected by study password) or `open` (direct access). |
| `locale` | string | Interface language (`en`, `id`). Expandable under `testbench/locales/`. |
| `brand.primary` | hex color | Brand accent color applied to participant header and buttons (e.g. `#151A23`). |
| `module.requires` | list | Prerequisite module IDs that must be completed before this module unlocks. |
| `module.audience` | object | Audience filters using `identity_pattern` (regex) or `when` (conditional logic). |
| `ab_test.order` | string | Variant rotation: `rotate` (cyclic Latin square), `code_parity`, `random`, or `fixed`. |

### Environment Variables

| Variable | Default | Purpose |
|---|---|---|
| `SECRET_KEY` | Auto-generated | Flask session cookie signing key. Must be fixed in production. |
| `SUPERADMIN_PASSCODE` | None | Master passcode unlocking `/admin/` and all project dashboards. |
| `DATA_DIR` | `instance` | Filesystem path storing SQLite databases, uploaded prototypes, and Studio files. |
| `PROJECTS_DIRS` | `projects` | Read-only project directory paths joined with `:` (`;` on Windows). |
| `TESTBENCH_MODE` | `internal` | Set to `public` to enable user accounts, quotas, and team invitations. |
| `APP_DOMAIN` | None | Primary hostname serving the application and authenticated cookies. |
| `USERCONTENT_DOMAIN` | None | Isolated registrable domain serving untrusted prototype JavaScript. |
| `MAX_UPLOAD_MB` | `50` | Maximum file size for prototype ZIP archives and asset uploads. |
| `LOCAL_ASSETS` | `0` | Set to `1` to serve CSS, fonts, and icons locally without third-party CDN requests. |
| `SESSION_COOKIE_SECURE`| `0` | Set to `1` to enforce HTTPS-only cookies in production. |

---

## Security, Privacy & Isolation

- **Dual-Domain Sandboxing**: Prototypes run on `USERCONTENT_DOMAIN` while the administrative dashboard runs on `APP_DOMAIN`. Because cookies are bound to the registrable domain, prototype scripts cannot access session cookies or administrative endpoints.
- **Upload Hardening**: File uploads are restricted to web asset extensions (`.html`, `.css`, `.js`, `.png`, `.jpg`, `.pdf`, `.mp4`, etc.). ZIP archives are sanitized against directory traversal attacks (`../`), and deleting files actively referenced by modules is blocked.
- **Passcode Hashing**: Study and administrator passcodes are hashed using salted cryptographic algorithms before storage.
- **Participant Privacy**: No personally identifiable information (PII) is recorded unless explicitly requested in a survey question. A/B testing tracks interactions, click paths, element labels, and scroll reversals, but never records text typed into input fields.
- **Automated Data Retention**: The `python -m testbench retention` command clears participant IP addresses and expired tokens, identifying closed studies eligible for archival.

---

## Extending UX Testbench

Custom research modules can be created by subclassing `ModuleType` in `testbench/modules/base.py`:

```python
from testbench.modules.base import ModuleType, ADVANCE

class CustomModule(ModuleType):
    def validate(self, raw, scope):
        """Validate module YAML configuration and report errors via scope.add()."""
        ...

    def steps(self, state):
        """Define the sequence of participant steps."""
        ...

    def handle(self, ctx, step):
        """Process participant HTTP requests and return ADVANCE upon completion."""
        ...

    def report(self, ctx):
        """Render researcher analytics and visual dashboards."""
        ...

    def export(self, ctx):
        """Yield structured rows for CSV export."""
        ...
```

Register the module class in `testbench/modules/__init__.py` and provide a starter template under `testbench/scaffold/templates/`.

---

## Repository Map

| Directory / File | Description |
|---|---|
| `testbench/` | Core application package and application factory (`create_app`). |
| ├── `admin.py` | Admin routes: Overview, Build, Launch, Results, Settings, and Instance. |
| ├── `studio.py` | Visual Studio backend: YAML serialization, module builder, asset manager. |
| ├── `web.py` | Participant testing flow, module router, and session state manager. |
| ├── `modules/` | Implementations of `ab_test`, `first_click`, `card_sort`, `tree_test`, and `survey`. |
| ├── `questions.py` | Dynamic survey engine, conditional branching, and response piping. |
| ├── `storage.py` | Per-study SQLite schema, participant session tracking, and queries. |
| ├── `context.py` | Template context injection, role checking (`is_super`, `is_admin`, `can_edit`). |
| ├── `static/` | CSS design tokens, task runner (`task-runner.js`), QR code generator. |
| └── `templates/` | Jinja2 templates for admin studio, participant views, and module embeds. |
| `projects/` | Read-only bundled reference studies (`projects/example/`). |
| `docs/` | In-depth technical documentation (`configuration.md`, `operations.md`, `statistics.md`). |
| `scripts/` | Automation utilities (`seed_demo.py`, `backup.sh`, `build-assets.sh`). |
| `tests/` | Comprehensive test suite (unit tests, integration flows, and security tests). |

---

<!-- [EXTENSIBLE] -->
## Documentation & Architecture

- **[docs/configuration.md](docs/configuration.md)** — Complete reference for `project.yaml`, access modes, and module syntax.
- **[docs/operations.md](docs/operations.md)** — Production operations runbook, Docker Compose orchestration, and dual-domain security architecture.
- **[docs/statistics.md](docs/statistics.md)** — Mathematical foundation for the Sign Test, 95% bootstrap confidence intervals, and three-state verdicts.
- **[CONTRIBUTING.md](CONTRIBUTING.md)** — Code style conventions, development workflow, and testing requirements.

---

<!-- [OPTIONAL] -->
## License

This project is licensed under the MIT License — see the [LICENSE](LICENSE) file for details.
