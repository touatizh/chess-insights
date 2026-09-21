"""Pre-generate the homepage's featured reports (§8 Phase 5).

``Report.featured`` is read by ``/api/featured`` but nothing in the request path
ever sets it -- featured reports are seeded out of band, by this module:

    uv run python -m app.seed touatizh drnykterstein

Each username is analyzed through the normal pipeline (:func:`run_report_job`),
then its report is flagged ``featured``. The job function talks to the database
directly and imports neither redis nor rq, so seeding needs no broker and no
worker process -- but it *does* need Stockfish, and it does hit the real Lichess
API. Expect a few minutes per user for a cold run, and export ``LICHESS_TOKEN``
first to avoid anonymous rate limits.

Re-running is safe: a username that already has a done report is flagged in
place rather than re-analyzed.
"""

from __future__ import annotations

import sys

from app.db import (
    create_report,
    get_or_create_player,
    init_db,
    latest_done_report,
    session_scope,
)
from app.jobs import run_report_job
from app.models import Report


def seed_username(username: str) -> int:
    """Ensure ``username`` has a done report and flag it featured.

    Returns the report id. Reuses an existing done report when present, so a
    re-run after a partial failure doesn't re-analyze what already succeeded.
    """
    with session_scope() as session:
        player_id, _ = get_or_create_player(username, session)
        existing = latest_done_report(session, player_id)
        if existing is not None and existing.id is not None:
            report_id = existing.id
            reused = True
        else:
            report = create_report(session, player_id)
            if report.id is None:  # pragma: no cover - defensive
                raise RuntimeError(f"report row for {username} has no id")
            report_id = report.id
            reused = False

    if not reused:
        # Synchronous: this is the same function the RQ worker calls.
        run_report_job(report_id, username)

    with session_scope() as session:
        analyzed = session.get(Report, report_id)
        if analyzed is None:  # pragma: no cover - defensive
            raise RuntimeError(f"report {report_id} vanished while seeding {username}")
        if analyzed.status != "done":
            raise RuntimeError(f"report for {username} ended as {analyzed.status}, not done")
        analyzed.featured = True
        session.add(analyzed)
        session.commit()

    return report_id


def main(argv: list[str] | None = None) -> int:
    """Seed every username given on the command line.

    One bad username (typo, Lichess outage) must not discard the work already
    done for the others, so failures are collected and reported at the end.
    """
    names = list(argv if argv is not None else sys.argv[1:])
    if not names:
        print("usage: python -m app.seed <lichess-username> [...]", file=sys.stderr)
        return 2

    init_db()
    failed: list[tuple[str, str]] = []

    for name in names:
        print(f"seeding {name} ...", flush=True)
        try:
            report_id = seed_username(name)
        except Exception as exc:  # noqa: BLE001 - report and continue to the next user
            print(f"  failed: {exc}", file=sys.stderr, flush=True)
            failed.append((name, str(exc)))
        else:
            print(f"  featured report {report_id}", flush=True)

    seeded = len(names) - len(failed)
    print(f"\nseeded {seeded}/{len(names)}")
    for name, err in failed:
        print(f"  {name}: {err}", file=sys.stderr)

    return 1 if failed else 0


if __name__ == "__main__":  # pragma: no cover - module entrypoint
    raise SystemExit(main())
