# Chess Insights - The Arbiter's Report

> Find out what's actually wrong with your chess. A weakness report for any Lichess player.

Enter a Lichess username. The backend pulls that player's last 30 blitz/rapid games,
runs every one of their moves through Stockfish, and files a shareable report naming
the specific thing costing them games - the line, the phase, the number.

The UI is themed as a judicial case dossier: reports are "filed", each gets a case
number, and the finding is stamped with a single hand-drawn chess-notation glyph
(`?` or `!`). Chess notation is meaningful, so the glyph is always rendered verbatim -
`?` is not `??`.

## How it works

```
  browser                                             Lichess API
     |                                                     ^
     v                                                     |
  Vite SPA  --/api-->  FastAPI  --enqueue-->  Redis (RQ)    |
  :5173                 :8000                    |          |
                           |                     v          |
                           |                  worker  ------+
                           |                     |
                           +----> SQLite <-------+
                                                 |
                                                 v
                                            Stockfish
```

The API never opens the engine; it only enqueues work and serves results. The worker
runs reports one at a time (concurrency 1 by design), which is what makes the queue
position shown in the UI meaningful.

Analysis is incremental. Games are stored per player with their per-move evaluations,
so a repeat report for the same user re-analyzes only the games it hasn't seen and
re-aggregates the rest from the database.

## Prerequisites

| Requirement | Notes |
| --- | --- |
| uv | Manages the backend environment and its Python 3.14 |
| Node 20+ | For the Vite frontend |
| Redis | Backs the RQ job queue |
| Stockfish | UCI binary, invoked by the worker only |

The project requires Python 3.14 (set in `backend/pyproject.toml`), but you don't
need it installed - `uv sync` downloads a managed interpreter when necessary.

On Debian/Ubuntu: `sudo apt install redis-server stockfish`

## Setup

The backend uses [uv](https://docs.astral.sh/uv/). It reads `uv.lock`, so everyone
gets the same resolved dependency set, and it will fetch a managed Python 3.14 if the
system doesn't have one.

```bash
# Backend
cd backend
uv sync --extra dev

# Frontend
cd ../frontend
npm install
```

The `dev` extra adds the test and lint toolchain (pytest, ruff, mypy, pre-commit).
Plain `uv sync` is enough to run the app itself.

## Running

Three processes, each in its own shell.

```bash
# 1. API
cd backend && uv run uvicorn app.main:app --port 8000

# 2. Worker
cd backend && uv run rq worker reports --url "$REDIS_URL"

# 3. Frontend
cd frontend && npm run dev
```

Then open http://localhost:5173. The Vite dev server proxies `/api` to
`http://localhost:8000`, so the frontend uses relative paths and needs no API URL
configured - the same code works behind a production reverse proxy.

Note: nothing in the app loads a `.env` file automatically. Environment variables
must be exported in the shell that starts each process, for example:

```bash
cd backend
export LICHESS_TOKEN="..."
uv run rq worker reports --url "$REDIS_URL"
```

The worker command passes `--url` explicitly because the `rq` CLI reads
`RQ_REDIS_URL`, not the `REDIS_URL` the application itself uses. Without it the
worker would connect to `localhost:6379` regardless of how the API is configured.

## Configuration

All variables are optional; defaults are what the code falls back to.

| Variable | Default | Controls |
| --- | --- | --- |
| `DATABASE_URL` | `sqlite:///./chess_insights.db` | Database URL. SQLite runs in WAL mode since the API and worker both write. |
| `REDIS_URL` | `redis://localhost:6379/0` | Redis connection for the RQ queue. Read by the API; pass it to the worker with `--url`. |
| `CORS_ORIGIN` | `http://localhost:5173` | The single allowed CORS origin. |
| `STOCKFISH_PATH` | `/usr/games/stockfish` | Path to the Stockfish binary. Worker only. |
| `ANALYSIS_DEPTH` | `10` | Stockfish search depth, clamped to 6-14. |
| `LICHESS_TOKEN` | empty (anonymous) | Optional Lichess API token; raises rate limits. |
| `RATELIMIT_STORAGE_URI` | `memory://` | Rate-limit storage. Point at Redis to share limits across API processes. |

Per-IP rate limits are fixed in code: 2 new reports/hour and 10 cache-hit
lookups/hour.

## Tests

```bash
cd backend
uv run pytest -q                     # 123 tests
uv run ruff check app/ tests/
uv run mypy app/

cd ../frontend
npx tsc -b && npm run lint
```

Tests marked `engine` shell out to a real Stockfish binary and honour
`STOCKFISH_PATH`; everything else uses canned evaluations and runs without it.

`pre-commit` hooks (ruff, ruff-format, mypy, bandit, pip-audit, codespell) run on
every commit. Install them once with `uv run pre-commit install`.

## API

| Method | Path | Purpose |
| --- | --- | --- |
| `GET` | `/api/health` | Liveness probe. |
| `POST` | `/api/reports` | File a report for `{"username": "..."}`. Returns `200` if a fresh report already exists (no new games since it was built), `202` if work was queued or an in-progress report was resumed. `404` unknown Lichess user, `429` rate-limited, `502` Lichess unreachable. |
| `GET` | `/api/reports/{id}` | Poll status. Returns status, progress, queue position while queued, the live `analyzed_new`/`total_new` counters, and the payload once done. |
| `GET` | `/api/reports/by-username/{username}` | Latest completed report for a player. Used when opening a shared link with no report id. |
| `GET` | `/api/reports/{id}/og-image` | 1200x630 PNG share card, immutably cached. |
| `GET` | `/api/featured` | Reports flagged as featured, shown on the home page. |
| `GET` | `/report/{username}` | Server-rendered unfurl HTML for link bots (Discord, Slack, Twitter, Facebook, WhatsApp, Telegram, LinkedIn). Returns `404` for non-bot agents so it never intercepts the SPA. |

## Data model

Four SQLModel tables, created on API startup. There are no migrations.

**Player** - `id`, `username` (unique, stored lowercase), `created_at`.

**Game** - `id`, `player_id`, `lichess_id`, `speed`, `played_at`, `color`,
`result`, `opening_name`, `opening_eco`, `moves` (SAN), `analyzed`. Unique on
`(player_id, lichess_id)`, so one row per game per tracked player.

**MoveEval** - `id`, `game_id`, `ply`, `san`, `cp_loss`, `phase`
(`opening`/`middlegame`/`endgame`), `severity` (`ok`/`inaccuracy`/`mistake`/
`blunder`). Only the subject player's own moves are stored.

**Report** - `id`, `player_id`, `created_at`, `status`
(`queued`/`fetching`/`analyzing`/`done`/`failed`), `progress`, `total_new`,
`analyzed_new`, `payload` (JSON, null until done), `rq_job_id`, `error`, `featured`.

Move classification thresholds are centipawn loss: `>=300` blunder, `>=150`
mistake, `>=50` inaccuracy.

## Layout

```
backend/
  app/
    main.py            FastAPI app, CORS, router registration
    models.py          SQLModel tables
    schemas.py         Response models
    db.py              Engine, sessions, queries
    lichess.py         Game fetching and parsing
    queue.py           RQ queue and position lookup
    ratelimit.py       Per-IP limits
    jobs.py            The worker job: fetch, analyze, aggregate
    og.py              Pillow share-card renderer
    analysis/
      engine.py        Stockfish wrapper
      classify.py      Severity and phase classification
      report.py        Payload aggregation, signature-leak rules
    routers/
      reports.py       Report CRUD, polling, OG image
      jobs.py          Featured reports
      preview.py       Bot unfurl route
  tests/

frontend/
  src/
    pages/             Home, Report
    components/        Document card, charts, loading states
    api.ts             Typed client
    main.tsx           Router setup
```

## Status

Phases 1-4 are complete: the analysis pipeline, API, frontend, share cards, and bot
unfurls all work in local development.

Phase 5 (deployment) is not implemented. There are no Dockerfiles, no compose file,
and no `.env.example` yet. Two things to carry into that work:

- nginx must match bot user agents on `^/report/` and proxy them to the backend
  preview route, while serving the SPA to everyone else. See the note at the top of
  `backend/app/routers/preview.py`.
- `/api/featured` returns an empty list until reports are flagged `featured = true`.
  No seeding script is committed.
