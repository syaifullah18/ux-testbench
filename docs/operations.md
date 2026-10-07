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
GOOGLE_CLIENT_ID=…           # sign in with Google; see "Signing in with Google and GitHub"
GOOGLE_CLIENT_SECRET=…
GITHUB_CLIENT_ID=…           # and/or GitHub
GITHUB_CLIENT_SECRET=…
PASSWORD_LOGIN=off            # no passwords stored; needs a provider above
SMTP_URL=smtps://user:pass@smtp.example.org:465       # optional with PASSWORD_LOGIN=off
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

With `PASSWORD_LOGIN=off`, `create-admin` asks for no password: it marks the address as a platform
admin, and that person signs in with Google or GitHub using the same address.

## Signing in with Google and GitHub

Researchers can sign in with a Google or GitHub account instead of a password. A provider is
offered once both of its variables are set. Register this instance with each provider:

- **Google:** Google Cloud console → APIs & Services → Credentials → Create OAuth client ID,
  type *Web application*. Authorized redirect URI: `https://<APP_DOMAIN>/login/google/callback`.
  The consent screen needs only the `openid`, `email` and `profile` scopes, which need no review.
- **GitHub:** Settings → Developer settings → OAuth Apps → New OAuth App. Authorization callback
  URL: `https://<APP_DOMAIN>/login/github/callback`.

How accounts are matched:

- An account is found by the provider's own account id, so it survives a change of address at the
  provider.
- The first time, it is matched to an existing account by **verified** email address. An address
  the provider has not verified is refused.
- If the matching account had a password but had never verified its address, that password and
  its sessions are removed: the provider has just proved the address belongs to someone else.
- A new address creates an account only when `SIGNUP_MODE` allows it (`invite` needs an
  invitation sent to that address).
- Researchers link or unlink a provider at `/account`. The last way to sign in cannot be unlinked.

Moving an existing instance off passwords: set the provider variables and keep
`PASSWORD_LOGIN=on` for a while. Each person who signs in through Google or GitHub with the same
address is linked automatically. Then set `PASSWORD_LOGIN=off`. Anyone not linked yet signs in
the same way and is linked then; their old password stops being offered.

## Environment variables

| Variable | Default | Meaning |
| --- | --- | --- |
| `TESTBENCH_MODE` | `internal` | `public` enables accounts, the landing page, quotas and moderation |
| `SIGNUP_MODE` | `open` | `open`, `invite` (private beta) or `closed` |
| `SECRET_KEY` | random | Signs session cookies. A new value logs everyone out. |
| `DATA_DIR` | `./instance` | SQLite files and the Studio's projects folder |
| `DATABASE_URL` | unset | `postgresql://…` stores each study in its own PostgreSQL schema instead of a SQLite file. See [PostgreSQL and object storage](#postgresql-and-object-storage). |
| `DB_POOL_MAX` | 5 | Connections per app worker. A request uses one, however many studies it touches. |
| `STORAGE_BACKEND` | `local` | `s3` keeps the durable copy of every Studio study, and exports, in a bucket |
| `S3_BUCKET`, `S3_ENDPOINT_URL`, `S3_REGION` | unset | The bucket. Leave `S3_ENDPOINT_URL` unset for AWS; set it for Cloudflare R2 or MinIO. |
| `S3_ACCESS_KEY_ID`, `S3_SECRET_ACCESS_KEY` | unset | Credentials with read, write, list and delete on the bucket |
| `S3_PREFIX` | `testbench` | Key prefix, so one bucket can hold several instances |
| `LOCAL_ASSETS` | off | `1` serves CSS, fonts and icons from this instance, so participant pages make no third-party requests |
| `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET` | unset | Offer "Continue with Google". See [Signing in with Google and GitHub](#signing-in-with-google-and-github). |
| `GITHUB_CLIENT_ID`, `GITHUB_CLIENT_SECRET` | unset | Offer "Continue with GitHub" |
| `PASSWORD_LOGIN` | `on` | `off` removes email and password sign-in, sign-up, verification and reset. Needs a provider. |
| `SMTP_URL`, `MAIL_FROM` | unset | Without `SMTP_URL`, emails are written to the log instead of sent. Password verification and reset then do not work for real users; invitations still give the owner a link to share. |
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

`scripts/backup.sh` copies SQLite files. On PostgreSQL, back up the database with the platform's
own backups or `pg_dump`; with object storage, the bucket already holds every study folder.

## PostgreSQL and object storage

Both are optional and independent: SQLite and local files remain the default, and either can be
switched on without the other. Install `requirements-postgres.txt` and `requirements-s3.txt`
(the Docker image already includes both).

**PostgreSQL.** Set `DATABASE_URL`. Each study gets its own schema (`study_<slug>`) and the
platform tables a `testbench` schema; the app creates them on first use, so the database user
needs `CREATE` on the database. Studies stay isolated the way separate files were: a study's
queries run with only its own schema on the `search_path`.

**Object storage.** Set `STORAGE_BACKEND=s3` and the `S3_*` variables. The bucket becomes the
durable copy of every study made in the Studio, YAML, prototypes and uploads, and the local
`DATA_DIR/projects` folder a working copy of it. Every change in the Studio is mirrored to the
bucket; at start, the app downloads whatever the volume is missing, so a container on an empty
volume comes back with every study. Prototypes are still served from the local copy, on the
app's own domain. Exports are written under `<S3_PREFIX>/exports/` and handed out as links that
expire after 15 minutes; add a lifecycle rule that deletes that prefix after a day.

Run **one app replica**, or give every replica the same `DATA_DIR` volume: a replica sees an
edit made on another one only after it restarts.

**Moving an existing instance**, with the app stopped:

```bash
# 1. Database: copies every .db file in DATA_DIR into PostgreSQL. Ids are kept, row counts are
#    checked, and the SQLite files are left untouched. --dry-run lists what would be copied.
DATABASE_URL=postgresql://… python -m testbench migrate-to-postgres

# 2. Files: uploads every Studio study folder and checks each object against the disk.
STORAGE_BACKEND=s3 S3_BUCKET=… python -m testbench migrate-files-to-s3
```

Start the app with the new settings, check a study's results, and keep the old `DATA_DIR` until
you are sure.

**On Dokploy:**

1. Create a PostgreSQL service in the same project and copy its internal connection URL into the
   app's `DATABASE_URL`.
2. Create an R2 bucket (or a MinIO service) and an API token scoped to it. Set
   `STORAGE_BACKEND=s3`, `S3_BUCKET`, `S3_ENDPOINT_URL=https://<account-id>.r2.cloudflarestorage.com`,
   `S3_REGION=auto` and the two keys.
3. Keep a volume mounted at `/data`. It holds the working copy of the studies; the bucket makes
   losing it recoverable, not irrelevant.
4. Keep the app at one replica.
5. Redeploy, then open `/health`: it reports `"database": "postgres"` and `"database_ok": true`.

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
   deleted by its owner from `/account`, or by an operator with
   `python -m testbench delete-user <email>`.

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
- [ ] Sign-in works end to end: Google and/or GitHub with the production callback URLs, or, while
      `PASSWORD_LOGIN=on`, `SMTP_URL` delivers verification and reset emails.
- [ ] A restore from backup has been tested.
- [ ] `LOCAL_ASSETS=1`, so participant pages make no third-party requests.
- [ ] Quotas match what the host can actually hold.
- [ ] A private beta has run with `SIGNUP_MODE=invite` and the abuse reporting path was exercised
      at least once, deliberately.
