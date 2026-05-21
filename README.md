# ZJ Quant · 多因子量化交易系统 · Web 监控管理平台

A full-stack data-governance platform on top of the `quant_data` data layer, built to the
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

- **Backend** — FastAPI wrapping `quant_data`, JWT auth (viewer/editor/admin), an in-process
  job runner (migration / vacuum / compact / snapshot), an audit log + event bus, and a
  SQLite/Postgres app DB. See [`quant_web/`](quant_web).
- **Frontend** — Vite + React 18 + React Router + React Query, KLineChart (K-line) + ECharts
  (everything else). See [`frontend/`](frontend).
- **Data layer** — the pre-existing `quant_data` package, extended with 7 additive capabilities
  (§7 of the design doc). See [`quant_data/`](quant_data).

---

## Quick start (one container)

```bash
docker compose run --rm web python -m quant_web.seed_demo   # seed a demo A-share world
docker compose up                                           # serve API + SPA on :8000
# open http://localhost:8000  →  login admin / admin123
```

## Quick start (local dev, hot reload)

Backend (terminal 1):

```bash
python3.13 -m venv .venv && source .venv/bin/activate
pip install -e ".[web]"

export QUANT_DATA_ROOT=$PWD/.devdata
export AUDIT_DB_URL="sqlite:///$PWD/.devaudit.db"
python -m quant_web.seed_demo            # seed demo data + users (idempotent-ish)
uvicorn quant_web.main:app --reload --port 8000
```

Frontend (terminal 2) — Vite proxies `/api` to `:8000`:

```bash
cd frontend
npm install
npm run dev                              # http://localhost:5173
```

Open http://localhost:5173 and log in. Demo users (seeded on first launch):

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

**Data management** (P0, fully delivered):

- **总览 Dashboard** — KPI ticker, data-asset health table (freshness), governance hotspots,
  source priorities, recent-activity feed.
- **批次台账 Batches** — filterable/paginated ledger, detail (overview / written-rows preview /
  quality / lineage / parameters), invalidate (with reason) + supersede.
- **数据源 Sources** — card grid, inline priority edit, create/edit with a per-table field-mapping
  editor, cross-source diff.
- **字段注册表 Fields** — registry list + detail drawer, the extras→promoted→core state machine,
  **migration dry-run → async job with live progress**, time-travel query, stale-extras tab.
- **表浏览 Tables** — picker + the v0.2 core browser: three view modes (当前生效 / 全量含作废 /
  单批次), internal-column toggle, extras-as-columns, and the **row-lineage drawer**.
- **数据质量 Quality** — issues list, 30-day timeseries, by-table/by-check aggregation, acknowledge.
- **维护操作 Maintenance** — vacuum / compact (preview + typed-phrase confirm + job progress),
  metadata snapshots.
- **审计日志 Audit** — who/what/when with before→after detail.

**Visualization** (P0): K-line workstation (candles + MA/MACD/VOL, corporate-action marks,
batch-revision bands, drill-through to batch/lineage), fundamentals time-series (PIT-enforced),
generic explorer (table/column/aggregation → chart + saved templates).

**Future modules** (predeclared, not built): `/factors`, `/strategies`, `/monitor` exist in the
nav and return **501** with their planned ship dates; the API namespaces are reserved.

### `quant_data` enhancements (design doc §7, all additive — existing tests untouched)

| § | capability | where |
|---|---|---|
| 7.1 | persisted quality issues + acknowledge | `quant_data/quality_log.py`, `schemas.QUALITY_ISSUES` |
| 7.2 | audit log + event bus | `quant_web/audit/`, `quant_web/events/bus.py` |
| 7.3 | async job system + progress | `quant_web/jobs/` (in-process pool, see note below) |
| 7.4 | table-stats cache | `quant_data/table_stats.py`, `schemas.TABLE_STATS` |
| 7.5 | full view + single-batch read | `reader.read_full_view` / `reader.read_batch` |
| 7.6 | `_extras` key discovery | `reader.list_extras_keys` |
| 7.7 | field-migration dry-run | `migration.dry_run_promote` / `promote_field_to_core(dry_run=True)` |
| 7.8 | row lineage | `reader.read_row_lineage` |
| 7.9 | K-line marks | `quant_data/charts.py` |

---

## Tests

```bash
pytest                       # 186 passing: quant_data (169) + quant_web API (17)
pytest tests/test_web_api.py # web layer only
cd frontend && npm run build # type/import-check the SPA
```

## Project layout

```
quant_data/        data layer (+ §7 additions: quality_log, table_stats, charts)
quant_web/         FastAPI app
  auth/ db/ audit/ events/ jobs/ data/ (dashboard,batches,sources,fields,tables,quality,maintenance,charts/)
  stubs.py         501 placeholders · seed_demo.py · seed_users.py · main.py
frontend/          Vite + React SPA
  src/lib/         api client, auth, theme, formatters
  src/components/  shell (TerminalShell + chrome) · ui (DataTable, charts, drawers, …)
  src/pages/       one module per page group
tests/             pytest (data layer + web API)
Dockerfile · docker-compose.yml · .env.example
```

## Implementation notes / deviations from the doc

- **Jobs**: the doc names Dramatiq + Redis; this build uses an **in-process thread-pool runner**
  with job state persisted in the app DB, exposing the identical Job API (submit → poll
  `job_id` → progress/result). It needs no Redis and is swappable for a Dramatiq broker later.
- **Quality issues** are persisted in the `quant_data` metadata layer (where ingest produces them),
  with the Web `acknowledged` fields layered on top — satisfying both §6.4 and §7.1.
- **Auth** uses PBKDF2 (stdlib) for password hashing and python-jose for JWT (HS256).
- API errors follow the unified `{"error": {code, message, details, trace_id}}` shape (§5.1 / App B).
```
