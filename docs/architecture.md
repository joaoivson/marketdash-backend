## Backend Architecture

This backend follows a layered architecture to keep responsibilities isolated, improve testability, and scale with new features.

### Layers

- **API (Controllers)**: HTTP only. Validates input, calls services, returns responses.
- **Services**: Business rules and orchestration.
- **Repositories**: Data access for PostgreSQL/Supabase.
- **Schemas**: Pydantic input/output contracts.
- **Models**: SQLAlchemy ORM models.
- **Core**: Config, security, logging, error handlers.
- **Utils**: Shared helpers (serialization, parsing).

### Folder Structure

```
app/
  api/
    v1/
      routes/
      dependencies.py
  core/
    config.py
    logging.py
    errors.py
    security.py
  repositories/
  services/
  models/
  schemas/
  utils/
  db/
  main.py
```

### Data Flow

1. **Route** receives the request and validates parameters.
2. **Service** applies business rules and uses repositories.
3. **Repository** executes SQLAlchemy queries.
4. **Response** returns typed data via Pydantic schemas.

### API Versioning

- Current version: `/api/v1`
- Legacy endpoints have been removed. All clients must use `/api/v1`.

### Pagination

List endpoints accept `limit` and `offset` when applicable:

- `GET /api/v1/datasets/latest/rows`
- `GET /api/v1/datasets/all/rows`
- `GET /api/v1/ad_spends`

### Cache (Redis)

The backend supports Redis caching for dataset rows and ad spends.

Environment variables:

```
REDIS_URL=redis://localhost:6379/0
CACHE_TTL_SECONDS=300
```

Cache keys are invalidated on dataset upload and ad spend mutations.

### Postgres Indexes

New composite indexes were added for query performance:

- `dataset_rows (user_id, sub_id1, date)`
- `datasets (user_id, uploaded_at)`
- `ad_spends (user_id, date)`
- `ad_spends (user_id, sub_id, date)`
- `ad_spends (user_id, date, id)`

If you manage migrations manually, apply the SQL:

```
CREATE INDEX IF NOT EXISTS idx_user_sub_id_date ON dataset_rows (user_id, sub_id1, date);
CREATE INDEX IF NOT EXISTS idx_dataset_user_uploaded ON datasets (user_id, uploaded_at);
CREATE INDEX IF NOT EXISTS idx_ad_spend_user_date ON ad_spends (user_id, date);
CREATE INDEX IF NOT EXISTS idx_ad_spend_user_sub_date ON ad_spends (user_id, sub_id, date);
CREATE INDEX IF NOT EXISTS idx_ad_spend_user_date_id ON ad_spends (user_id, date, id);
```

### Cakto Integration

Webhook endpoint: `POST /api/v1/cakto/webhook`

Environment variables:

```
CAKTO_API_BASE=https://api.cakto.com.br
CAKTO_CLIENT_ID=...
CAKTO_CLIENT_SECRET=...
CAKTO_SUBSCRIPTION_PRODUCT_IDS=12345,67890
CAKTO_WEBHOOK_SECRET=...
CAKTO_ENFORCE_SUBSCRIPTION=false
```

### Adding a New Endpoint

1. Create/extend a **repository** for DB access.
2. Add a **service** method with business logic.
3. Add a **route** in `app/api/v1/routes`.
4. Wire the route into `app/api/v1/routes/__init__.py`.
5. Update docs if needed.
