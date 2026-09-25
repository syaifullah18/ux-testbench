# Plan: from internal tool to public service

Status: draft, 2026-09-25

## Goal

Today UX Testbench is an internal tool. One operator deploys it, holds `SUPERADMIN_PASSCODE`, and hands out a project passcode to each research team. Nobody can sign up. The `/` page is a bare list of projects, and there is no account, landing page, policy page, or way to recover access.

The goal is a **free, non-commercial public service**. Anyone doing user research (students, NGOs, government teams, small studios) can open the site, read what it does, create an account, and run studies on their own, with no operator in the loop. Nothing is billed: there are no plans, payments, or paywalled features. Self-hosting stays a first-class option, and a self-hosted instance can still run in today's closed "internal" mode.

Participants do **not** get accounts. They keep the lightweight entry they have now (code, email, or anonymous, with an optional study passcode). Asking participants to register would hurt response rates for no research benefit.

## Where we are

| Area | Today | Gap for a public service |
| --- | --- | --- |
| Researcher identity | Per-project admin passcode, plus one global `SUPERADMIN_PASSCODE` from env ([admin.py](../../testbench/admin.py), [passcodes.py](../../testbench/passcodes.py)) | No users, no ownership, no password reset, no audit of who changed what |
| Project creation | Superadmin only (`/admin/projects/new`) | Must be self-service, with per-user quotas |
| Collaboration | Anyone holding the admin passcode | Invite teammates with roles; revoke one person without rotating a shared secret |
| Public pages | `/` lists `listed` projects ([index.html](../../testbench/templates/index.html)) | Landing page, about, docs, privacy, terms, contact, status |
| Prototype hosting | Served same-origin with the app ([ADR 001](../decisions/001-same-origin-prototypes.md)) | **Blocking.** Any sign-up can upload JS that runs on the app's origin |
| Abuse | In-memory login rate limit ([rate_limit.py](../../testbench/rate_limit.py)) | Quotas, reporting, takedown, and phishing risk from hosted HTML |
| Email | None | Needed for verification, password reset, and invitations |
| Frontend | Tailwind Play CDN, Google Fonts, Font Awesome CDN ([base.html](../../testbench/templates/base.html)) | Play CDN is not for production; third-party requests leak participant IPs |
| Legal | Consent checkbox per project | Privacy policy, terms, data retention, account and data deletion (UU PDP No. 27/2022, and GDPR for EU participants) |
| Ops | `gunicorn -w 2`, SQLite per project | Backups, error pages, monitoring, and a rate limiter shared across workers |

## Guiding decisions

1. **Keep the architecture.** SQLite per project ([ADR 003](../decisions/003-sqlite-per-project.md)) and YAML as the source of truth ([ADR 004](../decisions/004-yaml-source-of-truth.md)) still hold at this scale. Accounts, memberships and sessions go into the existing `DATA_DIR/_system.db`. No Postgres.
2. **Two deployment modes, one codebase.** A new `TESTBENCH_MODE=public|internal` setting. `internal` behaves as today: no sign-up, env passcodes work, and `/` is the project list. `public` enables sign-up, the landing page, quotas, and moderation. Every change below is gated by the mode or is harmless in both.
3. **Participants stay accountless.** Only researchers sign in.
4. **Untrusted researchers by default.** In public mode every account holder is treated as a potential attacker toward other tenants and toward participants. This is the one change of mindset that drives most of the work below.
5. **Few new dependencies.** Werkzeug already hashes passwords. Add only what saves real risk: an SMTP client (stdlib `smtplib` is enough), and optionally Flask-Limiter for a shared rate limit store.

## Phase 0: prerequisites (blocking)

These must be finished before any public sign-up opens.

### 0.1 Move prototypes to a separate origin

ADR 001 accepted same-origin serving because only trusted admins could upload. Once anyone can sign up, a researcher can upload a prototype whose script reads another user's session, calls admin endpoints with that user's cookies, or phishes participants on our domain.

- Serve every prototype and uploaded file from a separate registrable domain, for example `testbench-usercontent.org`, not a subdomain of the app (a subdomain shares cookies set on the parent domain).
- Instrument through a script tag instead of reaching into the DOM from the parent. When the usercontent host serves an HTML file, it injects `<script src="/_tb/probe.js">`. The probe records first click, scroll reversals, viewport and click path, and sends events to the parent with `postMessage`. The parent [task-runner.js](../../testbench/static/task-runner.js) checks `event.origin` against the configured origin. ADR 001's objection ("we can't access the DOM cross-origin") goes away because the probe runs inside the prototype.
- Prototype URLs carry a short-lived signed token (participant id, module, variant, expiry) because no app cookie reaches the usercontent domain.
- Add a strict `Content-Security-Policy` to the app origin. The usercontent origin gets `frame-ancestors <app origin>`, so prototypes can't be framed elsewhere, and a banner that reads "Prototype hosted for a research study, not a real service" when opened outside the frame.
- Write ADR 005, superseding 001.
- `internal` mode may keep same-origin serving through a `PROTOTYPE_ORIGIN` setting that defaults to the app origin.

### 0.2 Close the known bugs from the earlier review

The variant scoping fix and the `PROTOTYPE_EXT` allowlist in `ab_test._serve`, the sign-test decision rule, and the `load_dotenv` placement are all still open. The first two are security fixes and a precondition for 0.1.

### 0.3 Treat researcher YAML as untrusted input

- **Regex (ReDoS):** `identity.pattern` and `audience.identity_pattern` are compiled from researcher input. Cap their length, run them with a timeout (the `regex` package supports one), or limit patterns to a safe subset in public mode.
- **Size limits:** cap the YAML file size, the number of modules per project, the questions per module, and the options per question.
- **Templates:** confirm that no researcher string reaches the output unescaped (`|safe`, `Markup`), especially `consent`, `description`, and question `hint`s.

## Phase 1: researcher accounts

### Data model (`_system.db`)

```sql
users        (id, email UNIQUE, name, password_hash, email_verified_at, is_platform_admin,
              locale, created_at, last_login_at, disabled_at)
auth_tokens  (token_hash PRIMARY KEY, user_id, purpose  -- verify | reset | invite | login
              , expires_at, used_at, meta_json)
sessions     (id_hash PRIMARY KEY, user_id, created_at, last_seen_at, ip, user_agent)
memberships  (project_slug, user_id, role  -- owner | editor | viewer
              , created_at, PRIMARY KEY (project_slug, user_id))
invitations  (id, project_slug, email, role, token_hash, invited_by, expires_at, accepted_at)
audit_log    (id, at, user_id, project_slug, action, detail_json, ip)
```

Keep the existing `SCHEMA_VERSION` plus `CREATE TABLE IF NOT EXISTS` approach, with no migration framework.

### Auth flows

| Flow | Route | Notes |
| --- | --- | --- |
| Sign up | `GET/POST /signup` | Email, name, password (min 10 characters, checked against a short common-password list). Sends a verification link. No project creation until the email is verified. |
| Verify email | `GET /verify/<token>` | Single-use; expires after 24 hours |
| Log in | `GET/POST /login` | Rate limited per IP and per account. Generic error message, so account existence doesn't leak. |
| Log out | `POST /logout` | POST with CSRF, not GET (the current admin logout is GET) |
| Forgot / reset password | `/forgot`, `/reset/<token>` | Single-use, 1 hour expiry; resetting invalidates all sessions |
| Account settings | `/account` | Name, email change (re-verify), password, locale, active sessions with "sign out everywhere", export my data, delete account |

- Sessions: keep Flask's signed cookie, but store only a random session id that is looked up in `sessions`. Revocation and "sign out everywhere" then work.
- Set `SESSION_COOKIE_SECURE` automatically in public mode and name the cookie with the `__Host-` prefix.
- Optional later: "Sign in with Google" via OIDC. Not needed for launch.

### Authorization

Replace `is_admin(slug)` in [context.py](../../testbench/context.py) with `can(user, project, action)`:

| Action | owner | editor | viewer | platform admin |
| --- | --- | --- | --- | --- |
| View results, export CSV | yes | yes | yes | yes, audited |
| Edit in Studio, upload files | yes | yes | no | yes, audited |
| Reset data, delete participants | yes | no | no | yes, audited |
| Manage members, passcodes, delete project | yes | no | no | yes |

- `SUPERADMIN_PASSCODE` becomes a bootstrap only. `python -m testbench create-admin you@example.org` creates the first platform admin. In public mode the env passcode login is disabled.
- In internal mode the per-project admin passcodes keep working as today. In public mode they are replaced by memberships. A migration command, `python -m testbench claim <slug> <email>`, assigns an owner to existing projects.
- Projects loaded from read-only `PROJECTS_DIRS` belong to platform admins only.

### Email

Add `testbench/mail.py` using `smtplib` with `SMTP_URL` and `MAIL_FROM` settings. Store templates in `templates/mail/` and translate them with the existing locale files. In dev, when `SMTP_URL` is unset, print emails to the log.

## Phase 2: public pages and navigation

### URL layout

Study slugs currently live at the root (`/<slug>/`), so every new public page is a potential collision. Two options:

- **A. Reserve names** (`login`, `signup`, `about`, `docs`, …) in `RESERVED_SLUGS`. Small change, but each new page means another reserved name, and an existing project could already hold one of them.
- **B. Move studies under `/s/<slug>/`**, with the Studio under `/app/…`, and redirect old `/<slug>/…` URLs permanently.

**Recommendation: B.** Links already sent to participants keep working through the redirect, and the root namespace is ours from then on.

```
/                     landing page (public mode) or project list (internal mode)
/about /docs/<page>   product pages; docs rendered from docs/*.md
/privacy /terms       policy pages
/explore              opt-in gallery of public studies (moderated, off by default)
/login /signup /forgot /reset/<t> /verify/<t>
/app/                 researcher dashboard: my projects, invitations, create/import
/app/p/<slug>/…       per-project admin, results and Studio (today's /<slug>/admin/…)
/app/admin/           platform admin: users, projects, reports, audit log
/s/<slug>/…           participant flow (today's /<slug>/…)
/report               report abuse (a study or prototype)
```

### Landing page (`/`)

- A hero that says what the tool is in one sentence ("Run surveys, A/B prototype tests, card sorts, tree tests and first-click tests, free and open source"), with two calls to action: **Create a free account** and **Try the demo study**.
- The demo study is `projects/example/`, seeded by `python -m testbench demo` and read-only, so visitors see both the participant view and a results dashboard with synthetic data.
- One section per method (survey, A/B, card sort, tree test, first click), each with a screenshot and one line on when to use it.
- "How it works" in three steps: write or import a study, share the link, read the results with statistics you can trust. Link to [docs/statistics.md](../statistics.md).
- A trust section: MIT licensed, self-hostable, participant data stays per project, no tracking or ads.
- A footer with About, Docs, Privacy, Terms, source code, contact, and a language switch (en/id).

### Researcher dashboard (`/app/`)

- A project list with the role, participant count, last activity, and status (draft / live / closed).
- A "New project" wizard that reuses the existing blank / copy / import logic in [studio.py](../../testbench/studio.py). New projects start as draft, unlisted, and passcode-protected.
- An empty state that points to the docs and the example template.

### Project lifecycle

Add a `status` field: `draft` (only members can open the participant view, for piloting), `live`, or `closed` (participants see a "study has ended" page and results stay readable). This replaces the implicit "live as soon as it exists" behaviour, and it is also what the moderation tools act on.

## Phase 3: abuse, privacy and legal

### Quotas (public mode, configurable)

| Resource | Default |
| --- | --- |
| Projects per user | 10 |
| Upload size per file / per project | 10 MB / 100 MB |
| Participants per project | 2,000 |
| Unverified account | cannot create projects |
| Sign-ups per IP per day | 5 |

### Moderation

- A `/report` form and a "Report this study" link in the participant footer.
- Platform admin tools: disable a user, take a project offline (403 with a neutral message), view the audit log.
- Check uploaded prototype HTML for password or credit-card inputs posting off-site, and flag it for review. This is a hint for moderators, not a guarantee.
- Studies default to unlisted. `/explore` shows only studies a platform admin has approved.

### Privacy

- Self-host Tailwind (a build step, see Phase 4), fonts, and icons, so participant pages make **no third-party requests**. Participant IPs then never reach Google or Cloudflare.
- Retention: `closed` projects older than N months (configurable, default 12) warn their owners by email and are then deleted. Participant rows are never kept past project deletion.
- Account deletion: delete the user, transfer or delete sole-owned projects (ask during the flow), and anonymise audit log rows.
- Export: account data as JSON; project data already exports as CSV and zip.
- Mark IP addresses in `sessions` and `audit_log` as personal data, and truncate them after 30 days.

### Legal pages

Non-commercial doesn't remove obligations. We need:

- A **Privacy policy** covering who the controller is (the operator of the instance), the processor role toward researchers' participant data, what is stored, retention, and contact.
- **Terms of use**: acceptable use (no phishing, no collecting sensitive data without ethics approval, no minors without guardian consent), no warranty, and the right to take studies down.
- A **Researcher responsibilities** notice shown when creating a project: you are the controller for your participants' data, write your own consent text, and follow your institution's ethics process.

These need review by someone qualified in Indonesian data protection law (UU PDP) before launch. This plan does not provide that.

## Phase 4: production hardening

- **Frontend build:** replace the Tailwind Play CDN with a compiled, purged CSS file (Tailwind standalone CLI; no Node needed at runtime). If we adopt daisyUI, this is where it fits.
- **Rate limiting:** the in-memory limiter resets per gunicorn worker and on restart. Move it to a table in `_system.db`, or use Flask-Limiter with a SQLite or Redis backend.
- **SQLite:** enable WAL mode and `busy_timeout` on every connection. Cap the size of the connection pool per worker.
- **Registry:** add a TTL to `Registry.refresh()`, so each request doesn't stat every project folder once there are hundreds.
- **Error pages:** friendly 404/403/500 pages in both locales. No stack traces in production.
- **Observability:** structured logs, a request id, optional Sentry through an env DSN, and `/health` extended with disk space and DB writability.
- **Backups:** a nightly `sqlite3 .backup` of every `.db` plus a tarball of `DATA_DIR/projects`, with a documented restore.
- **Packaging:** a Dockerfile and a `docker-compose.yml` with the app, a reverse proxy (Caddy for automatic HTTPS), and the usercontent host. Document the two-domain setup.
- **Security review:** run `/security-review` on the auth and serving changes, and look at the CSP and cookie settings with an external scanner (Mozilla Observatory) before launch.

## Phase 5: launch

1. A private beta with 5 to 10 invited research teams, with sign-up closed and invitations only (`SIGNUP_MODE=invite`).
2. Fix what the beta finds. Watch storage growth and abuse reports.
3. Open sign-up with email verification and quotas on.
4. Announce with the demo study as the first thing people see.

## Sequencing and effort

| Phase | Depends on | Rough effort (1 developer) |
| --- | --- | --- |
| 0. Prerequisites (separate origin, open bugs, YAML limits) | none | 1.5 to 2 weeks |
| 1. Accounts, roles, email | none (can run alongside 0) | 2 weeks |
| 2. URL move, landing page, dashboard, lifecycle | 1 | 1.5 weeks |
| 3. Quotas, moderation, privacy, legal pages | 1, 2 | 1 week, plus legal review time |
| 4. Hardening and packaging | 0 to 3 | 1 week |
| 5. Beta and launch | all | 2 to 4 weeks elapsed |

That is about 7 to 8 weeks of engineering before a public beta. Phases 0 and 1 are the critical path. Phase 0.1 is the only item that must never be skipped.

## Tests to add

- Auth: sign-up, verification, login lockout, reset token reuse, session revocation, and CSRF on logout.
- Authorization: a matrix test that tries every admin and Studio route as anonymous, viewer, editor, owner, a non-member, and a platform admin.
- Isolation: a user of project A can't read, export, or edit project B by guessing slugs, member ids, or file paths.
- Usercontent: an expired or foreign token is rejected, app cookies are absent on that origin, and the probe's postMessage from the wrong origin is ignored.
- Legacy redirects: `/<slug>/…` resolves to `/s/<slug>/…`.
- Internal mode: the existing test suite passes unchanged with `TESTBENCH_MODE=internal`.

## Open questions

- **Who operates the public instance,** and on which domains (the app plus the usercontent domain)? The privacy policy names this operator.
- **Password or magic-link login?** This plan assumes password plus email verification. Magic links remove password handling but make email delivery a hard dependency for every login.
- **Should `/explore` exist at launch,** or only later once moderation is proven?
- **Participant data location:** is hosting outside Indonesia acceptable for the expected users, given UU PDP's cross-border transfer rules?
