# Alumni Dashboard API

A single FastAPI backend backed by **one shared PostgreSQL database**. All
admin actions (CSV imports, events, speakers, Super Stars, legal-name
review) are written directly to that database, and every alumni dashboard
view reads from the same source of truth - nothing is ever kept only in
memory, frontend state, local storage, or temporary files. The first
organization is `fsu-cci`; the schema (`alumni_organizations`) supports
more organizations later without code changes.

## Roles

There are exactly two roles, stored on `users.role`:

- **admin** - create/update/import/publish/delete shared content
  (`/admin/*`), review legal-name-change requests, view any alumni's
  profile via admin endpoints.
- **alumni** - view published shared content and edit only their own
  profile (`/me/*`).

## Authentication

Login credentials live in the `users` table (bcrypt-hashed passwords -
plaintext is never stored/logged). `POST /login` verifies the password and
returns a JWT (`sub`=username, `role` claim), but **role and profile
linkage are always re-read from the database on every request** (see
`app/deps.get_current_user`), so an admin changing a user's role or
alumni link takes effect immediately without waiting for re-login.

Each alumni login account may be linked to exactly one alumni record via
`users.alumni_id` (nullable - admin accounts typically have none). This
powers `GET/PATCH /me/profile`.

Create/update a user:

```bash
python scripts/create_user.py --username admin --role admin
python scripts/create_user.py --username jdoe --role alumni --alumni-id <alumni-record-uuid>
```

You'll be prompted for a password (never passed as a CLI argument or
logged).

> Architecture note: an earlier iteration of this backend authenticated
> against a backend-only `data/users.csv` file. That file cannot express a
> real foreign key to an alumni record, so authentication has moved back
> to the `users` database table to support `alumni_id`, `created_by_user_id`,
> and `reviewed_by_user_id` relational integrity throughout this feature
> set. `data/users.csv` (if still present on disk) is no longer read by
> the app and can be deleted.

## Architecture

```
app/
├── main.py                    FastAPI app, CORS, static /uploads mount, error handlers
├── config.py                   Settings from environment variables
├── database.py                  SQLAlchemy engine/session/Base
├── security.py                  bcrypt hashing + JWT create/verify
├── deps.py                      Current-user (re-fetched from DB) + organization resolution
├── models/
│   ├── organization.py           organizations
│   ├── user.py                    users (role, alumni_id FK)
│   ├── alumni.py                   alumni, alumni_organizations
│   ├── content.py                   events, speakers, super_stars
│   ├── reference.py                  companies, industries, universities
│   ├── audit.py                       csv_imports, audit_logs
│   ├── legal_name.py                   legal_name_change_requests
│   ├── location_alias.py                location_aliases
│   └── roles.py                          Role/status enums shared across the app
├── schemas/                     Pydantic request/response models
├── services/
│   ├── location_normalization_service.py   Canonical location normalization
│   ├── us_geography.py                       State/metro/borough reference data
│   ├── classification_service.py               Industry/career/seniority inference
│   ├── csv_import_service.py                     CSV import + dedup + audit pipeline
│   ├── location_reprocess_service.py              Shared reprocessing logic
│   ├── audit_service.py                            record_audit_log() helper
│   └── storage_service.py                           Profile photo upload (local/pluggable)
├── seed/seed_data.py             Organization + location alias seed data
└── routers/
    ├── auth_routes.py             POST /login
    ├── alumni_routes.py            GET /alumni-data
    ├── analytics_routes.py          GET /analytics/summary
    ├── admin_routes.py               CSV import, location reprocessing, legal-name review
    ├── content_routes.py              /events, /speakers, /super-stars (+ /admin/* CRUD)
    └── profile_routes.py               /me/profile, /me/profile/photo, /me/legal-name-change-request
alembic/                         Migrations (SQLAlchemy 2.0 models -> Postgres schema)
scripts/
├── seed_organizations.py         Seed fsu-cci / fsu-stars / stars-national + aliases
├── create_user.py                  Create/update a database user (with optional alumni link)
└── normalize_existing_locations.py CLI location reprocessing (dry-run/batch support)
tests/                          pytest suite (auth, authz, content, profile, import, location, analytics)
```

## Database models

`Alumni`, `Organization`, `User`, `Event`, `Speaker`, `SuperStar`,
`Company`, `Industry`, `University`, `CSVImport`, `AuditLog`,
`LegalNameChangeRequest`, `LocationAlias`. `UserRole` is a plain Python
enum (`admin` / `alumni`) used for validation, not a separate table.

## 1. Setup

### 1.1 Create a virtual environment

```bash
python3 -m venv .venv
source .venv/bin/activate      # Windows: .venv\Scripts\activate
```

### 1.2 Install dependencies

```bash
pip install -r requirements.txt
```

### 1.3 Set environment variables

```bash
cp .env.example .env
```

Edit `.env` and set at minimum:

- `DATABASE_URL` - PostgreSQL connection string (see below). SQLite is only
  acceptable for quick local testing; production must use Postgres
  (`app/config.py` refuses to boot in `ENVIRONMENT=production` on SQLite).
- `JWT_SECRET_KEY` - generate with:
  `python -c "import secrets; print(secrets.token_urlsafe(64))"`
- `ALLOWED_ORIGINS` - your Lovable frontend URL(s), comma-separated.
- `PUBLIC_BASE_URL` - the externally-reachable base URL of this API, used
  to build profile photo URLs (e.g. `https://api.yourapp.com`).

### 1.4 Start PostgreSQL

Any local Postgres works. Example with Docker:

```bash
docker run --name alumni-postgres -e POSTGRES_USER=alumni_api \
  -e POSTGRES_PASSWORD=password -e POSTGRES_DB=alumni_dashboard \
  -p 5432:5432 -d postgres:16
```

Then set:

```env
DATABASE_URL=postgresql://alumni_api:password@localhost:5432/alumni_dashboard
```

(No Docker/Postgres available? You can develop against SQLite locally by
setting `DATABASE_URL=sqlite:///./dev.db` - just don't ship that to
production.)

### 1.5 Run migrations

```bash
alembic upgrade head
```

### 1.6 Seed organizations + location aliases

```bash
python scripts/seed_organizations.py
```

This creates `fsu-cci`, `fsu-stars`, `stars-national`, and seeds the
`location_aliases` table (NYC, Brooklyn, Tallahassee, Washington DC,
Atlanta metro, etc.).

### 1.7 Create an administrator

```bash
python scripts/create_user.py --username admin --role admin
```

### 1.8 Import FSU CCI alumni

```bash
curl -X POST http://localhost:8000/admin/import-alumni \
  -H "Authorization: Bearer <your_token>" \
  -F "organization=fsu-cci" \
  -F "file=@/path/to/fsu_cci_alumni.csv"
```

Or use the interactive docs at `/docs` to upload the file from a browser.
Every import is recorded in `csv_imports` (created/updated/skipped/failed
counts + row errors) and logged in `audit_logs`.

### 1.9 Link an alumni login to an alumni record

```bash
python scripts/create_user.py --username jdoe --role alumni --alumni-id <alumni-record-uuid>
```

Find the alumni record's `id` via `GET /alumni-data` (admin token) or a
direct DB query after importing.

### 1.10 Normalize existing locations (if importing pre-existing data directly into the DB)

```bash
python scripts/normalize_existing_locations.py --organization fsu-cci --dry-run
python scripts/normalize_existing_locations.py --organization fsu-cci
```

### 1.11 Start the API

```bash
python run.py
```

Runs at `http://localhost:8000` (interactive docs at `/docs` while
`ENABLE_API_DOCS=true`).

## 2. API reference

### Auth

- `POST /login` - `{ username, password }` -> `{ access_token, token_type, expires_in, user: { username, role } }`

### Alumni data (shared database, paginated + filterable)

- `GET /alumni-data?organization=fsu-cci&page=1&page_size=25` (any authenticated role)
- `GET /analytics/summary?organization=fsu-cci` (any authenticated role)

### Shared content (public reads, admin-only writes)

- `GET /events` / `POST|PATCH|DELETE /admin/events[/{id}]`
- `GET /speakers` / `POST|PATCH|DELETE /admin/speakers[/{id}]`
- `GET /super-stars` / `POST|PATCH|DELETE /admin/super-stars[/{id}]`

All three accept `?organization=<slug>` (defaults to `DEFAULT_ORGANIZATION_SLUG`).
`GET` endpoints only ever return `is_published=true` rows.

### Admin

- `POST /admin/import-alumni` - `organization` (form field) + `file` (CSV)
- `POST /admin/normalize-locations`
- `GET /admin/legal-name-requests`, `POST /admin/legal-name-requests/{id}/approve|reject`

### Alumni self-service profile

- `GET /me/profile` - the caller's own alumni record (404 if the account
  has no linked alumni record)
- `PATCH /me/profile` - only `graduation_date`, `graduation_year`,
  `job_title`, `company`, `job_location`, `city`, `state`, `country`,
  `linkedin_url`, `bio`, `profile_visibility` may be set; any other field
  in the request body (e.g. `role`, `verification_status`, `alumni_id`)
  is rejected with `422`.
- `POST /me/profile/photo` - multipart image upload (`jpeg`/`png`/`webp`,
  size-limited by `MAX_UPLOAD_SIZE_MB`); stores only a generated URL on
  the profile, never a raw filesystem path.
- `POST /me/legal-name-change-request` - `{ requested_legal_name, reason }`,
  reviewed later by an admin.

## 3. Running the automated test suite

```bash
python -m pytest tests/ -v
```

Tests run against a throwaway SQLite database (configured in
`tests/conftest.py`) so they don't require Postgres. Coverage includes:
login success/failure and JWT claims, organization authorization, CSV
import creating DB records that are then visible via `GET /alumni-data`,
admin-created events/speakers/Super Stars appearing in their public GET
endpoints, alumni being blocked from all `/admin/*` write endpoints,
persistence across a simulated process restart (fresh DB session/engine,
no dropped tables), profile view/edit restricted to the caller's own
record (one alumni cannot edit another's profile), profile photo upload
validation, the legal-name-change-request approval workflow, and location
normalization/analytics grouping.

## 4. Connecting the Lovable frontend

Set an environment variable in your Lovable/Vite project:

```env
VITE_API_BASE_URL=https://your-deployed-backend.example.com
```

Frontend routes and the roles that can reach them:

- Alumni: `/dashboard`, `/map`, `/companies`, `/industries`, `/seniority`,
  `/universities`, `/events`, `/speakers`, `/super-stars`, `/profile`
- Admin: all of the above, plus management/import/publish/review screens.

Add the frontend's deployed origin (and any Lovable preview domain you
use) to `ALLOWED_ORIGINS` on the backend.

## 5. Deploying the backend

- `Procfile` runs `gunicorn` with `uvicorn.workers.UvicornWorker` - most
  PaaS providers (Render, Railway, Fly.io, Heroku-style buildpacks) detect
  this automatically.
- Provision a managed PostgreSQL instance and set `DATABASE_URL` to it.
- Set all variables from `.env.example` as real environment
  variables/secrets in your hosting platform - never upload `.env` itself.
- Run `alembic upgrade head` as part of your deploy step (release phase /
  predeploy hook), then `python scripts/seed_organizations.py` once.
- Set `ENVIRONMENT=production` and `ENABLE_API_DOCS=false`. Startup will
  refuse to boot if `JWT_SECRET_KEY` is weak/default, `ALLOWED_ORIGINS` is
  empty, or `DATABASE_URL` still points at SQLite.
- Profile photos: `STORAGE_PROVIDER=local` writes to `UPLOADS_DIR` on the
  server's own disk, which is fine for a single persistent instance but
  is lost on ephemeral/multi-instance deploys. For those, implement an
  additional provider (e.g. S3) in `app/services/storage_service.py`
  behind the same `save_profile_photo()` interface and set
  `STORAGE_PROVIDER` accordingly - no route code needs to change.
- Put the service behind HTTPS; point `ALLOWED_ORIGINS` at your production
  Lovable domain.

## 6. Security notes

- Passwords are bcrypt-hashed (`app/security.py`); plaintext passwords are
  never stored, logged, or returned in any response.
- JWTs carry `sub` (username) and `role`, but authorization always
  re-checks the live database row for the user (`app/deps.get_current_user`)
  - a role or profile-link change takes effect on the very next request,
    not just after the token expires.
- `/admin/*` write routes, and the write endpoints under `/events`,
  `/speakers`, `/super-stars`, require `role == "admin"`.
- `PATCH /me/profile` uses an allow-list schema (`extra="forbid"`) so a
  request containing `role`, `verification_status`, `alumni_id`, etc. is
  rejected outright rather than silently ignored.
- Government ID images are never accepted or stored anywhere in this
  application; legal name changes go through an admin text-review
  workflow (`LegalNameChangeRequest`) instead.
- Profile photo uploads validate content-type, size, and always generate
  a random server-side filename (no user-supplied filename or path is
  ever used) - see `app/services/storage_service.py`.
- Organization slugs passed via `?organization=` are validated against
  the database (404 if unknown).
- CORS is restricted to an explicit origin allow-list (`ALLOWED_ORIGINS`),
  never a wildcard.
- The login endpoint is rate-limited (`LOGIN_RATE_LIMIT`) and always
  performs a bcrypt comparison (even for unknown usernames) to reduce
  timing-based username enumeration.
- Every admin write (CSV import, content CRUD, legal-name review) is
  recorded in `audit_logs` in the same transaction as the change itself.
- Security response headers (`X-Content-Type-Options`, `X-Frame-Options`,
  `Referrer-Policy`) are added to every response.

## 7. Location normalization

See `app/services/location_normalization_service.py` for the full
implementation and doc comments. Key guarantees:

- `location_original` is never overwritten or discarded.
- NYC boroughs (Brooklyn, Queens, Manhattan, The Bronx, Staten Island)
  keep their own `city` value and are never collapsed into "New York
  City" - but they do share `metro_area = "New York City Metropolitan Area"`.
- Ambiguous input (e.g. a bare "Springfield") is preserved verbatim with
  `location_normalization_status = "ambiguous"` rather than guessed.
- `Remote` never invents a city/state/country.
- The `location_aliases` table is checked before generic parsing rules and
  can be extended without code changes.
- No paid geocoding dependency is required (optional, gated behind
  `GEOCODING_ENABLED`, off by default).

## 8. Industry / career category / seniority classification

Imported spreadsheet values always take priority and are tagged
`industry_source/career_category_source/seniority_source = "imported"`.
When a field is blank, `app/services/classification_service.py` may infer
a value from job title/company keywords, tagged `"inferred"`.
`Company`/`Industry`/`University` reference rows are also opportunistically
deduplicated per-organization during CSV import to back the
`/companies`, `/industries`, `/universities` frontend pages.
