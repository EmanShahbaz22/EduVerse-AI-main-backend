# EduVerse AI Backend

## Setup
1. Install `uv`:

```bash
pip install uv
```

2. Create and activate a virtual environment:

```bash
uv venv .venv
source .venv/bin/activate
```

On Windows PowerShell use:

```powershell
.venv\Scripts\activate
```

3. Install dependencies:

```bash
uv sync
```

## Run
Start FastAPI:

```bash
uvicorn app.main:app --reload
```

## Security/RBAC Model
1. Tenant data isolation remains strict for account/profile/admin management.
2. Shared marketplace behavior is enabled for students: they may enroll into courses across tenants.
3. Student identity for enrollment and quiz submission is always derived server-side from the auth session.

## Tests (Phase 6)
RBAC/ownership tests are in `tests/` and cover:
1. Quiz submission ownership and IDOR protections.
2. Enrollment ownership + tenant enforcement rules.
3. Admin tenant-bound mutation protections.

Run:

```bash
pytest -q
```

If you install dev dependencies explicitly:

```bash
uv sync --extra dev
```

## API Migration Notes (Phase 7)
See full details in [docs/API_MIGRATION.md](docs/API_MIGRATION.md).

Key contract changes:
1. `POST /quiz-submissions` no longer accepts `studentId` or `tenantId` from client.
2. `GET/DELETE /quiz-submissions/*` now enforce role + ownership rules.
3. `POST /courses/enroll` and `/courses/unenroll` derive student identity for student role; admin/teacher remain tenant-bound.

## Database Consistency Audit
Use the integrity checker to validate document references and tenant consistency:

```bash
python -m scripts.check_db_consistency
```

## Notes
1. Run from repository root (`backend/`) so imports resolve correctly.
2. `app/main.py` registers startup index creation via `ensure_indexes()`.
