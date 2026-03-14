# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
python -m uvicorn app.main:app --reload --port 8081   # Dev server
pytest tests/ -v                                       # All tests
pytest tests/unit/test_jobs.py -v                      # Single test file
pytest tests/ -k "test_name" -v                        # By test name
celery -A app.tasks.celery_app worker --loglevel=info  # Celery worker
docker-compose up                                      # All services (db, redis, app, worker)
```

## Architecture

Layered: `api/v1/routes/` (thin) → `services/` (business logic) → `repositories/` (data access) → `models/` (ORM). Don't skip layers.

```
app/
├── api/v1/
│   ├── routes/          # HTTP endpoints — delegate to services
│   ├── dependencies.py  # Auth (get_current_user), DB session, Supabase client
│   └── __init__.py      # Router registration
├── services/            # Business logic (CSVService, DashboardService, etc.)
├── repositories/        # SQLAlchemy queries
├── models/              # SQLAlchemy ORM models
├── schemas/             # Pydantic request/response validation
├── tasks/               # Celery async tasks (celery_app.py, csv_tasks.py)
├── core/
│   ├── config.py        # Settings from .env via pydantic-settings
│   ├── security.py      # JWT encode/decode, password hashing
│   ├── logging.py       # Structured logging
│   └── errors.py        # Global exception handlers
├── db/
│   ├── session.py       # get_db() dependency, engine
│   └── base.py          # Base model, init_db()
├── templates/emails/    # HTML email templates (Jinja2)
└── main.py              # FastAPI app, middleware stack, router mounting
```

## Auth Flow

1. JWT from Supabase Auth arrives in `Authorization: Bearer <token>` header
2. `get_current_user()` in `api/v1/dependencies.py` validates via `supabase.auth.get_user(token)` — NOT local JWT decode
3. Finds local user by email in PostgreSQL
4. Sets `app.current_user_id` in PostgreSQL session for RLS
5. Returns `User` model instance

## Key Models

- `User` — id, email, name, is_active, subscription info
- `Dataset` — id, user_id, filename, status, created_at
- `DatasetRow` — id, dataset_id, user_id, **raw_data (JSONB)**, revenue, commission, date, product
- `AdSpend` — id, user_id, date, platform, amount, sub_id
- `Subscription` — id, user_id, plan, status, cakto integration
- `CaptureSite` — id, user_id, slug, title, styling fields, is_active

## Dashboard Calculations

KPIs are calculated from `DatasetRow.raw_data` JSONB (original CSV fields):
- Revenue: `raw_data["Valor de Compra(R$)"]`, fallback to `row.revenue`
- Commission: `raw_data["Comissão líquida do afiliado(R$)"]`
- **Profit = Commission - Ad Spend** (NOT Revenue - Cost)
- ROAS = Revenue / Ad Spend

## Critical Rules

- **Data isolation**: ALL queries filter by `user_id`. RLS enforced via `SET LOCAL app.current_user_id`
- **Supabase client**: ONLY for auth validation. All data via SQLAlchemy
- **Database**: PostgreSQL via Supabase, connection in `DATABASE_URL` env var
- **Migrations**: SQL scripts in `migrations/`. Key indices: `user_id`, `date`, `product` (composite)

## Conventions

- Files: `snake_case.py` | Classes: `PascalCase` | Functions: `snake_case` | Constants: `UPPER_SNAKE_CASE`
- Always use type hints
- Pydantic schemas for all request/response validation
- `logger = logging.getLogger(__name__)` at module level
- Raise `HTTPException` with appropriate status codes

## Adding a new endpoint

1. Pydantic schema in `schemas/`
2. Repository method in `repositories/`
3. Service method in `services/`
4. Route in `api/v1/routes/`
5. Register import in `api/v1/routes/__init__.py`
