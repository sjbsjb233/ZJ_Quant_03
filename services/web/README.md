# ZJ Quant · Web 监控管理平台 (services/web)

A full-stack data-governance platform on top of the shared `quant-data` core layer, built to the
*Web 监控管理平台 详细设计文档 v0.2*. It makes the data lifecycle (获取 → 入库 → 修订 → 销毁)
visible and operable: batch ledger, source registry, field registry (extras→promoted→core),
raw-row browsing with point-in-time + row lineage, K-line / fundamentals / explorer
visualization, data-quality tracking, async maintenance jobs, and a full audit log.

The UI is the **V3 Terminal** design — a Bloomberg-style, high-density, monospace interface
with light/dark themes.

```
┌──────────────┐   HTTPS    ┌─────────────────────────┐      ┌──────────────────────┐
│   React SPA  │ ─────────► │  FastAPI  (quant_web)   │ ───► │ quant_data (file I/O)│
│ V3 Terminal  │  /api/v1   │  auth · jobs · audit DB │      │ raw/reference/metadata│
└──────────────┘            └─────────────────────────┘      └──────────────────────┘
```

- **Backend** — `services/web/src/quant_web/`: FastAPI wrapping `quant_data`, JWT auth
  (viewer/editor/admin), an in-process job runner (migration/vacuum/compact/snapshot), an
  audit log + event bus, a SQLite/Postgres app DB.
- **Frontend** — `services/web/frontend/`: Vite + React 18 + React Router + React Query,
  KLineChart (K-line) + ECharts (everything else).
- **Core layer** — `packages/quant_data/` (`quant-data`, a separate distribution); this service
  depends on it by name (`quant-data>=0.2.0`).

> `quant-web` declares `quant-data` as a dependency by distribution name. In dev, install the
> packages first (editable) so the name resolves to the local working tree — `scripts/install_dev.sh`
> (invoked by `scripts/setup_env.sh`) handles the ordering for you.

---

## Quick start (one container)

```bash
cd services/web
docker compose run --rm web python -m quant_web.seed_demo   # seed a demo A-share world
docker compose up                                           # serve API + SPA on :8000
# open http://localhost:8000  →  login admin / admin123
```

## Quick start (local dev, hot reload)

From the **repo root**, build the venv and install all packages + services in dependency order:

```bash
bash scripts/setup_env.sh                # packages/* then services/* (editable)
source .venv/bin/activate
```

Backend (terminal 1):

```bash
export QUANT_DATA_ROOT=$PWD/.devdata
export AUDIT_DB_URL="sqlite:///$PWD/.devaudit.db"
export SEED_DEFAULT_USERS=true
python -m quant_web.seed_demo            # seed demo data + users
uvicorn quant_web.main:app --reload --port 8000
```

Frontend (terminal 2) — Vite proxies `/api` to `:8000`:

```bash
cd services/web/frontend
npm install
npm run dev                              # http://localhost:5173
```

Open http://localhost:5173 and log in. Demo users (seeded when `SEED_DEFAULT_USERS=true`):

| user   | password   | role   | can do                                              |
|--------|------------|--------|-----------------------------------------------------|
| admin  | admin123   | admin  | everything incl. field→core migration, vacuum/compact |
| editor | editor123  | editor | invalidate/supersede batches, edit sources/fields   |
| viewer | viewer123  | viewer | read-only (browse, charts, lineage, time-travel)    |

The seed creates a realistic world ending *today* so freshness reads green, plus the
governance artifacts the UI shows off: an invalidated + superseded price batch (row
lineage / K-line revision bands), a failed batch, a stale `running` batch, stale/promotable
`_extras` fields, quality issues, and corporate actions.

---

## What's implemented

**Data management** (P0, fully delivered): Dashboard · Batches (list/detail/invalidate/supersede) ·
Sources (CRUD + field-mapping editor + cross-source diff) · Fields (extras→promoted→core state
machine + **migration dry-run → async job**) · Tables (three view modes + internal columns +
extras-as-columns + **row-lineage drawer**) · Quality · Maintenance (vacuum/compact/snapshot) · Audit.

**Visualization** (P0): K-line workstation (candles + MA/MACD/VOL, corporate-action marks,
batch-revision bands, drill-through to batch/lineage), fundamentals time-series (PIT-enforced),
generic explorer (table/column/aggregation → chart + saved templates).

**Future modules** (predeclared, not built): `/factors`, `/strategies`, `/monitor` return **501**
with their planned ship dates; the API namespaces are reserved. The factor/strategy libraries are
predeclared at `packages/quant_factor` / `packages/quant_strategy`.

### `quant_data` enhancements (design doc §7, all additive — existing tests untouched)

| § | capability | where |
|---|---|---|
| 7.1 | persisted quality issues + acknowledge | `packages/quant_data/src/quant_data/quality_log.py`, `schemas.QUALITY_ISSUES` |
| 7.2 | audit log + event bus | `quant_web/audit/`, `quant_web/events/bus.py` |
| 7.3 | async job system + progress | `quant_web/jobs/` (in-process pool, see note below) |
| 7.4 | table-stats cache | `packages/quant_data/src/quant_data/table_stats.py`, `schemas.TABLE_STATS` |
| 7.5 | full view + single-batch read | `reader.read_full_view` / `reader.read_batch` |
| 7.6 | `_extras` key discovery | `reader.list_extras_keys` |
| 7.7 | field-migration dry-run | `migration.dry_run_promote` / `promote_field_to_core(dry_run=True)` |
| 7.8 | row lineage | `reader.read_row_lineage` |
| 7.9 | K-line marks | `packages/quant_data/src/quant_data/charts.py` |

---

## Tests

```bash
cd services/web && pytest         # 17 passing: quant_web API (TestClient + seeded world)
cd services/web/frontend && npm run build   # type/import-check the SPA
```

## Layout

```
services/web/
  pyproject.toml        quant-web distribution (depends on quant-data)
  src/quant_web/        FastAPI app (auth · db · audit · events · jobs · data/* · charts/* · main.py)
  frontend/             Vite + React SPA
    src/lib/            api client · auth · theme · query · formatters
    src/components/     shell (TerminalShell + chrome) · ui (DataTable · charts · drawers · …)
    src/pages/          one module per page group
  tests/                web API tests
  Dockerfile · docker-compose.yml · .env.example
```

## Implementation notes / deviations from the doc

- **Jobs**: the doc names Dramatiq + Redis; this build uses an **in-process thread-pool runner**
  with job state persisted in the app DB, exposing the identical Job API (submit → poll
  `job_id` → progress/result). No Redis required; swappable for a Dramatiq broker later.
- **Quality issues** are persisted in the `quant_data` metadata layer (where ingest produces them),
  with the Web `acknowledged` fields layered on top — satisfying both §6.4 and §7.1.
- **Auth** uses PBKDF2 (stdlib) for password hashing and python-jose for JWT (HS256).
- API errors follow the unified `{"error": {code, message, details, trace_id}}` shape (§5.1 / App B).
