# EduVerse Backend

FastAPI backend for the EduVerse multi-tenant e-learning platform.

## Stack

- FastAPI
- MongoDB
- Motor
- Stripe
- Pydantic
- `uv` for environment and dependency management

## Current Business Model

- Admins and teachers remain tenant-bound.
- Students are global users.
- A student can enroll in courses across tenants.
- Tenant context for student actions is derived from the target resource, such as course, payment, progress, or submission, instead of direct student ownership by a tenant.

## Setup

Install `uv` if needed:

```bash
pip install uv
```

Create and activate the virtual environment:

```bash
uv venv .venv
source .venv/bin/activate
```

Install dependencies:

```bash
uv sync
```

## Run

Start the API locally:

```bash
uvicorn app.main:app --reload
```

Default local API URL:

```text
http://localhost:8000
```

## Important Configuration

Shared backend deployment/product constants are centralized in:

- [app/core/settings.py](./app/core/settings.py)

Important values include:

- `FRONTEND_URL`
- `CORS_ORIGINS`
- `STRIPE_BRAND_BUTTON_COLOR`
- `MAX_SUBSCRIPTION_PLANS`
- `TENANT_ID`

Update environment variables or `settings.py`-backed config instead of duplicating values inside routers/services.

## Main Domains

- Auth and RBAC
- Courses and enrollment
- Student progress
- AI/adaptive learning
- Quiz generation and submissions
- Student performance and leaderboard
- Payments and billing
- Tenant/admin/super-admin operations

## Verification

Compile-check backend modules:

```bash
PYTHONPYCACHEPREFIX=/tmp/pycache python3 -m compileall app scripts
```

Run tests:

```bash
pytest -q
```

If dev extras are needed:

```bash
uv sync --extra dev
```

## Database / Integrity Utilities

Consistency audit:

```bash
python -m scripts.check_db_consistency
```

Repair helper:

```bash
python -m scripts.repair_db_consistency
```

Global-student cleanup helper:

```bash
python -m scripts.cleanup_global_student_model
```

Subscription-plan normalization helper:

```bash
python -m scripts.normalize_default_subscription_plan_limits
```

## Security Notes

- Student identity for protected student actions is derived server-side from the authenticated session.
- Admin and teacher mutations remain tenant-scoped.
- Quiz submissions and course access should rely on ownership/resource checks rather than trusting client-provided IDs.

## Notes

- Run commands from the `backend/` directory so imports resolve correctly.
- `app/main.py` registers startup index creation through the database bootstrap path.
- Stripe return URLs and branding are centralized so billing/checkout changes do not require router-by-router edits.
