# Alumni Dashboard API

A single, multi-organization FastAPI backend for the alumni dashboard
frontend. It serves authentication and alumni network data for **FSU
College of Communication and Information** (`fsu-cci`) today, and is
architected to support **FSU STARS** (`fsu-stars`) and **STARS National**
(`stars-national`) through the same backend/database without any code
changes - only new `organizations` rows and role assignments.

There is intentionally **one** API and **one** database for all
organizations; alumni data is separated by organization membership
(`alumni_organizations`) and access is separated by user role assignment
(`user_organization_roles`), never by spinning up separate services per
spreadsheet/org.

## Authentication

Login credentials live in a **backend-only CSV file** at `data/users.csv`
(never committed - see `.gitignore`; never served by any route), with
columns `username,password_hash,role`. Allowed roles are `admin` and
`alumni`. Passwords are bcrypt-hashed; only the hash is ever written to
disk. `POST /login` reads this file, verifies the password with bcrypt,
and returns a JWT whose claims carry `username` (`sub`) and `role` - every
subsequent request is authorized from those signed claims, not a DB/file
lookup. Both `admin` and `alumni` roles may currently view the alumni
dashboard; `role` is exposed on the token/response so the frontend (and
`/admin/*` routes, which require `role == "admin"`) can act on it.

Add/update a user:

```bash
python scripts/create_user.py --username admin --role admin
python scripts/create_user.py --username jdoe --role alumni
```

You'll be prompted for a password (never passed as a CLI argument or
logged). Organizations (`fsu-cci`, `fsu-stars`, `stars-national`) and
alumni records still live in PostgreSQL and are managed as described
below - CSV auth only replaces *login*, not the alumni data model.

> Note: `app/models/user.py` (`users`, `user_organization_roles`) and
> `scripts/create_admin.py` remain in the codebase from an earlier
> DB-backed auth design and still work against Postgres, but are no
> longer used by `POST /login` - the CSV file is now the source of truth
> for credentials.

## Architecture

```
app/
├── main.py                 FastAPI app, CORS, security headers, error handlers
├── config.py                Settings from environment variables
├── database.py               SQLAlchemy engine/session/Base
├── security.py               bcrypt hashing + JWT create/verify
├── csv_user_store.py          Read/write data/users.csv (backend-only)
├── deps.py                   Current-user (from JWT) + organization resolution
├── models/
│   ├── organization.py        organizations
│   ├── user.py                 users, user_organization_roles
│   ├── alumni.py                alumni, alumni_organizations
│   ├── location_alias.py         location_aliases
│   └── roles.py                   Role/status enums shared across the app
├── schemas/                   Pydantic request/response models
├── services/
│   ├── location_normalization_service.py   Canonical location normalization
│   ├── us_geography.py                       State/metro/borough reference data
│   ├── location_aliases_seed_data.py          Seed + fallback alias table
│   ├── classification_service.py               Industry/career/seniority inference
│   ├── csv_import_service.py                     CSV import + dedup pipeline
│   └── location_reprocess_service.py              Shared reprocessing logic
├── seed/seed_data.py           Organization + location alias seed data
└── routers/
    ├── auth_routes.py           POST /login
    ├── alumni_routes.py          GET /alumni-data
    ├── analytics_routes.py        GET /analytics/summary
    └── admin_routes.py             POST /admin/import-alumni, POST /admin/normalize-locations
alembic/                        Migrations (SQLAlchemy 2.0 models -> Postgres schema)
scripts/
├── seed_organizations.py        Seed fsu-cci / fsu-stars / stars-national + aliases
├── create_admin.py                Create/update a user + grant an org role
└── normalize_existing_locations.py CLI location reprocessing (dry-run/batch support)
tests/                          pytest suite (auth, authz, import, location, analytics)
```

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

You'll be prompted for a password (never passed as a CLI argument or
logged). This writes a bcrypt-hashed row to `data/users.csv`, which is
what `POST /login` authenticates against.

### 1.8 Import FSU CCI alumni

```bash
curl -X POST http://localhost:8000/admin/import-alumni \
  -H "Authorization: Bearer <your_token>" \
  -F "organization=fsu-cci" \
  -F "file=@/path/to/fsu_cci_alumni.csv"
```

Or use the interactive docs at `/docs` to upload the file from a browser.

### 1.9 Normalize existing locations (if importing pre-existing data directly into the DB)

```bash
python scripts/normalize_existing_locations.py --organization fsu-cci --dry-run
python scripts/normalize_existing_locations.py --organization fsu-cci
python scripts/normalize_existing_locations.py --organization fsu-cci --batch-size 100
```

### 1.10 Start the API

```bash
python run.py
```

Runs at `http://localhost:8000` (interactive docs at `/docs` while
`ENABLE_API_DOCS=true`).

## 2. Testing the API manually

### Login

```bash
curl -X POST http://localhost:8000/login \
  -H "Content-Type: application/json" \
  -d '{"username": "admin", "password": "<your-password>"}'
```

### Alumni data

```bash
curl "http://localhost:8000/alumni-data?organization=fsu-cci&page=1&page_size=25" \
  -H "Authorization: Bearer <access_token>"
```

### Analytics

```bash
curl "http://localhost:8000/analytics/summary?organization=fsu-cci" \
  -H "Authorization: Bearer <access_token>"
```

## 3. Running the automated test suite

```bash
python -m pytest tests/ -v
```

Tests run against a throwaway SQLite database (configured in
`tests/conftest.py`) so they don't require Postgres. They cover: login
success/failure, expired tokens, organization authorization (including
that a frontend-supplied org slug alone is never trusted), CSV import +
duplicate prevention, NYC/Brooklyn/state-abbreviation location
normalization, remote/ambiguous/missing locations, analytics city/metro
grouping, and dry-run reprocessing.

## 4. Connecting the Lovable frontend

Set an environment variable in your Lovable/Vite project:

```env
VITE_API_BASE_URL=https://your-deployed-backend.example.com
```

Then call:

- `POST {VITE_API_BASE_URL}/login` with `{ username, password }`
- `GET {VITE_API_BASE_URL}/alumni-data?organization=fsu-cci` with
  `Authorization: Bearer <access_token>`
- `GET {VITE_API_BASE_URL}/analytics/summary?organization=fsu-cci` with
  the same header

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
- Put the service behind HTTPS; point `ALLOWED_ORIGINS` at your production
  Lovable domain.

## 6. Security notes

- Passwords are bcrypt-hashed (`app/security.py`); plaintext passwords are
  never stored or logged, and `data/users.csv` is gitignored and
  `chmod 600`.
- `data/users.csv` is only ever read/written server-side
  (`app/csv_user_store.py`, `scripts/create_user.py`) - no route serves
  its contents, and no response body ever includes a password hash or the
  file path.
- JWTs carry `username` (`sub`) and `role` as signed claims; every request
  is authorized from those claims rather than re-reading the CSV, so a
  token remains valid (with its role) until it expires even if the CSV
  changes - re-login (or wait for expiry) after changing a user's role.
- `/admin/*` routes require the `admin` role.
- Organization slugs passed via `?organization=` are still validated
  against the database (404 if unknown) even though CSV users aren't
  scoped to specific organizations today.
- CORS is restricted to an explicit origin allow-list (`ALLOWED_ORIGINS`),
  never a wildcard.
- The login endpoint is rate-limited (`LOGIN_RATE_LIMIT`) and always
  performs a bcrypt comparison (even for unknown usernames) to reduce
  timing-based username enumeration.
- Security response headers (`X-Content-Type-Options`, `X-Frame-Options`,
  `Referrer-Policy`) are added to every response.

## 7. Location normalization

See `app/services/location_normalization_service.py` for the full
implementation and doc comments. Key guarantees:

- `location_original` is never overwritten or discarded.
- NYC boroughs (Brooklyn, Queens, Manhattan, The Bronx, Staten Island)
  keep their own `city` value and are never collapsed into "New York
  City" - but they do share `metro_area = "New York City Metropolitan Area"`.
  Analytics groups top cities by `(city, state)` and top metro areas by
  `metro_area` separately, so Brooklyn and New York City are never merged
  as the same city.
- Ambiguous input (e.g. a bare "Springfield") is preserved verbatim with
  `location_normalization_status = "ambiguous"` rather than guessed.
- `Remote` never invents a city/state/country.
- The `location_aliases` table is checked before generic parsing rules and
  can be extended without code changes (seed via
  `app/seed/seed_data.py` / `scripts/seed_organizations.py`).
- No paid geocoding dependency is required. Optional lat/lng enrichment is
  gated behind `GEOCODING_ENABLED` and cached in-process; the app fully
  functions with it disabled (the default).

## 8. Industry / career category / seniority classification

Imported spreadsheet values always take priority and are tagged
`industry_source/career_category_source/seniority_source = "imported"`.
When a field is blank, `app/services/classification_service.py` may infer
a value from job title/company keywords, tagged `"inferred"`. Analytics
and any "official" reporting should treat only `imported` and
`manually_assigned` values as confirmed; the frontend may show `inferred`
values as a temporary fallback.
