# Operations runbook

For whoever runs a public UX Testbench instance. A self-hosted internal instance needs almost none
of this: leave `TESTBENCH_MODE` unset and the app behaves as it always has, with one operator and a
passcode per study.

## The two domains

A public instance needs two separate registrable domains, not a domain and a subdomain:

| Domain | Serves | Why separate |
| --- | --- | --- |
| `APP_DOMAIN` | The application, the Studio, results | Holds the session cookie |
| `USERCONTENT_DOMAIN` | Researcher-uploaded prototypes and files | Prototype JavaScript must never reach an app cookie |

A subdomain would share cookies set on the parent domain, which defeats the point. Any researcher
can upload a prototype, and its scripts run in the participant's browser; the separate origin is
what keeps that from becoming a way to read another account's session. See
[decision 001](decisions/001-same-origin-prototypes.md) for the history of that trade-off.

## First deployment

```bash
git clone <this repo> && cd ux-testbench
cp .env.example .env
```

Fill in `.env`:

```bash
SECRET_KEY=$(python -c "import secrets; print(secrets.token_hex(32))")
TESTBENCH_MODE=public
SIGNUP_MODE=invite            # open the gates only after the beta
APP_DOMAIN=testbench.example.org
USERCONTENT_DOMAIN=testbench-usercontent.example      # a different registrable domain
ACME_EMAIL=ops@example.org
SMTP_URL=smtps://user:pass@smtp.example.org:465
MAIL_FROM="UX Testbench <noreply@testbench.example.org>"
LOCAL_ASSETS=1                # serve CSS, fonts and icons ourselves
```

Then:

```bash
./scripts/build-assets.sh                 # needed once for LOCAL_ASSETS=1
docker compose up -d
docker compose exec app python -m testbench create-admin you@example.org
```

Point both domains' DNS at the host before starting Caddy, or certificate issuance fails.

## Environment variables

| Variable | Default | Meaning |
| --- | --- | --- |
| `TESTBENCH_MODE` | `internal` | `public` enables accounts, the landing page, quotas and moderation |
| `SIGNUP_MODE` | `open` | `open`, `invite` (private beta) or `closed` |
| `SECRET_KEY` | random | Signs session cookies. A new value logs everyone out. |
| `DATA_DIR` | `./instance` | SQLite files and the Studio's projects folder |
| `LOCAL_ASSETS` | off | `1` serves CSS, fonts and icons from this instance, so participant pages make no third-party requests |
| `SMTP_URL`, `MAIL_FROM` | unset | Without `SMTP_URL`, emails are written to the log instead of sent. Verification and invitations then do not work for real users. |
| `SESSION_COOKIE_SECURE` | off | Forced on in public mode |
| `MAX_UPLOAD_MB` | 50 | Hard request limit, before the per-account quota |
| `LIMIT_PROJECTS_PER_USER` | 10 | 0 means no limit |
| `LIMIT_PARTICIPANTS_PER_PROJECT` | 2000 | New participants are turned away past this; anyone who started can finish |
| `LIMIT_SIGNUPS_PER_IP_PER_DAY` | 5 | |
| `LIMIT_UPLOAD_MB`, `LIMIT_PROJECT_MB` | 10, 100 | Per file, and per project in total |

Quotas apply only in public mode. `/app/admin/` shows the values actually in force.

## Daily and nightly jobs

```bash
# Backups: SQLite is copied with `.backup`, so a live write is never caught half-finished.
DATA_DIR=/srv/testbench/instance BACKUP_DIR=/srv/backups ./scripts/backup.sh

# Housekeeping: clears IP addresses past the retention window, deletes spent one-time tokens,
# and lists closed studies that are old enough to delete. It never deletes study data itself.
docker compose exec app python -m testbench retention
```

`docker compose` already runs the backup loop in its own container. If you deploy without
Compose, put both in cron.

Restoring: stop the app, copy the `.db` files back into `DATA_DIR`, untar `projects.tar.gz`, start
it again. Test a restore before you need one — an untested backup is a guess.

## Moderation

`/app/admin/` is the platform admin area, separate from a study's own admin pages.

- **Reports** arrive from `/report`, which anyone including a participant can use. Each one is
  `open` until a platform admin marks it `reviewed`, `actioned` or `dismissed`.
- **Taking a study offline** shows participants a neutral notice and keeps the results readable to
  its owners. The reason is internal and never shown to participants. Use it while investigating,
  before deciding anything permanent.
- **Disabling an account** signs it out everywhere immediately and blocks new logins.
- **`/explore`** lists only studies a platform admin has approved. A researcher marking a study
  `listed` is their opt-in, not publication.
- Every action is written to the audit log at `/app/admin/audit`.

What to look for in an uploaded prototype: forms asking for passwords, payment details or
government ID; content that imitates a real company's login page; anything that posts to an
off-site URL. A prototype is meant to be a mock, not a working service.

## Responding to an incident

1. **A phishing prototype:** take the study offline, disable the account, then look at the audit
   log for anything else the same account created.
2. **A leaked `SECRET_KEY`:** set a new one and restart. Everyone is signed out, which is the point.
3. **Disk full:** `/health` returns 503 below 100 MB free. Check `DATA_DIR` for uploaded
   prototypes, and old backups in `BACKUP_DIR`.
4. **A study's YAML is broken:** the app keeps the last working configuration running and shows
   the error at `/app/admin/`. `python -m testbench check` prints every problem in one pass.
5. **Someone asks for their data to be deleted:** participant rows live in that study's own
   `.db`. A project admin deletes one participant from the participants screen. An account is
   deleted by its owner from `/account`.

## Monitoring

`/health` returns JSON and a 503 when something is actually wrong:

```json
{"ok": true, "projects": 3, "config_ok": true, "data_dir_writable": true,
 "disk_free_mb": 18240, "disk_ok": true}
```

Every response carries an `X-Request-ID`, echoed from the proxy when it sets one, and the same id
appears in the log line for an unhandled error. When someone reports a problem, ask for the
reference shown on the error page.

## Before opening sign-ups

The checklist in [docs/plans/saas-readiness.md](plans/saas-readiness.md) covers the whole path.
The items that block opening `SIGNUP_MODE=open`:

- [ ] Prototypes are served from `USERCONTENT_DOMAIN`, and app cookies are absent there.
- [ ] The privacy policy and terms have been reviewed by someone qualified in Indonesian data
      protection law (UU PDP No. 27/2022), and name the operator.
- [ ] `SMTP_URL` works: verification, reset and invitation emails arrive.
- [ ] A restore from backup has been tested.
- [ ] `LOCAL_ASSETS=1`, so participant pages make no third-party requests.
- [ ] Quotas match what the host can actually hold.
- [ ] A private beta has run with `SIGNUP_MODE=invite` and the abuse reporting path was exercised
      at least once, deliberately.
