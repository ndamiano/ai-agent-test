#!/usr/bin/env python3
"""Read the platform DB from a terminal — named queries for the numbers we ask for repeatedly,
plus raw SQL for everything else.

Stdlib only, one file, no imports from src/: it has to run over ssh on the prod droplet where
nothing but the container is installed.

    scripts/db.py cost                    # GPU minutes + served model per game
    scripts/db.py builds                  # per-build duration, steps, status
    scripts/db.py models                  # jobs and hours grouped by what actually served them
    scripts/db.py failures                # failure rate by queue
    scripts/db.py sql "select ..."        # anything else
    scripts/db.py backfill                # one-shot repair of the two dead columns
    scripts/db.py prune [--before DATE]   # drop finished jobs' stored bodies, then VACUUM

`--db` points at another file; on prod that is the path inside the container's data volume.
`--since` takes a date (default 7 days back) and applies to every named query.
"""

import argparse
import json
import sqlite3
import sys
import time
from pathlib import Path

DEFAULT_DB = Path(__file__).resolve().parent.parent / "data" / "platform.db"

QUERIES = {
    "cost": """
        SELECT substr(g.id,1,8) AS game, g.status,
               g.credits_spent AS credits,
               round(g.seconds_used/60.0, 1) AS gpu_min,
               round(100.0*g.seconds_used/nullif(g.seconds_granted,0), 1) AS pct_budget,
               (SELECT count(*) FROM builds b WHERE b.game_id=g.id) AS builds,
               (SELECT group_concat(DISTINCT j.model) FROM jobs j
                 WHERE j.game_id=g.id AND j.queue='llm') AS served
          FROM games g
         WHERE g.created_at > :since AND g.seconds_used > 0
         ORDER BY g.seconds_used DESC
    """,
    "builds": """
        SELECT substr(b.id,1,8) AS build, substr(b.game_id,1,8) AS game, b.kind, b.status,
               b.steps, round(b.seconds_used/60.0, 1) AS gpu_min,
               round((b.finished_at - b.started_at)/60.0, 1) AS wall_min
          FROM builds b
         WHERE b.queued_at > :since
         ORDER BY b.queued_at DESC
    """,
    "models": """
        SELECT j.model AS served, j.queue, j.gpu_type, count(*) AS jobs,
               round(sum(j.exec_seconds)/3600.0, 2) AS hours,
               round(avg(j.exec_seconds), 1) AS avg_sec
          FROM jobs j
         WHERE j.created_at > :since AND j.exec_seconds > 0
         GROUP BY j.model, j.queue, j.gpu_type
         ORDER BY hours DESC
    """,
    "failures": """
        SELECT queue, count(*) AS jobs,
               sum(status='failed') AS failed,
               round(100.0*sum(status='failed')/count(*), 1) AS pct
          FROM jobs
         WHERE created_at > :since AND status IN ('done','failed')
         GROUP BY queue
    """,
}


def _rows(conn, sql, params=()):
    cur = conn.execute(sql, params)
    return [c[0] for c in cur.description], cur.fetchall()


def _print(cols, rows):
    if not rows:
        print("(no rows)")
        return
    cells = [[("" if v is None else str(v)) for v in r] for r in rows]
    width = [max(len(c), *(len(row[i]) for row in cells)) for i, c in enumerate(cols)]
    print("  ".join(c.ljust(width[i]) for i, c in enumerate(cols)))
    print("  ".join("-" * w for w in width))
    for row in cells:
        print("  ".join(row[i].ljust(width[i]) for i in range(len(cols))))
    print(f"\n{len(rows)} rows")


def backfill(conn):
    """Repair the two columns that were declared and never written. Both are recoverable from
    rows we already have: the served model is inside each llm result blob, and a build's cost is
    the sum of its jobs. Idempotent — re-running lands the same values."""
    served = 0
    for job_id, result in conn.execute(
            "SELECT id, result FROM jobs WHERE result IS NOT NULL AND queue = 'llm'"):
        try:
            name = (json.loads(result) or {}).get("model")
        except (json.JSONDecodeError, TypeError):
            continue
        if isinstance(name, str) and name:
            conn.execute("UPDATE jobs SET model = ? WHERE id = ?",
                         (name.rsplit("/", 1)[-1], job_id))
            served += 1

    built = conn.execute("""
        UPDATE builds SET seconds_used = COALESCE((
            SELECT sum(j.exec_seconds) FROM jobs j
             WHERE j.build_id = builds.id AND j.status = 'done' AND j.exec_seconds IS NOT NULL
        ), 0)
    """).rowcount
    conn.commit()
    print(f"backfilled: {served} job model names, {built} build seconds_used totals")


def prune(conn, before=None):
    """Drop the stored request and reply of FINISHED jobs. A build turn's payload is the whole
    transcript so far, so a 117-turn build stores the same conversation 117 times inside itself
    (measured 2026-07-31: 951 MB of jobs.payload). Builds from here on archive each turn to their
    run dir before clearing the row; this is the one-shot for everything that came before.

    Only done/failed rows: a pending or claimed job still needs its payload to be run."""
    where = "status IN ('done','failed')"
    params = ()
    if before:
        where += " AND created_at < ?"
        params = (time.mktime(time.strptime(before, "%Y-%m-%d")),)

    (jobs, payload_bytes, result_bytes), = conn.execute(
        f"SELECT count(*), COALESCE(sum(length(payload)),0), COALESCE(sum(length(result)),0) "
        f"FROM jobs WHERE {where} AND (payload IS NOT NULL OR result IS NOT NULL)",
        params).fetchall()
    on_disk = _db_bytes(conn)

    conn.execute(f"UPDATE jobs SET payload = NULL, result = NULL WHERE {where}", params)
    conn.commit()
    # sqlite keeps the freed pages on the free list; without VACUUM the file never shrinks.
    conn.execute("VACUUM")

    print(f"cleared {jobs} job bodies: {_mb(payload_bytes)} of payloads "
          f"+ {_mb(result_bytes)} of results")
    print(f"database: {_mb(on_disk)} -> {_mb(_db_bytes(conn))}")


def _mb(n):
    return f"{n / 1048576:.1f} MB"


def _db_bytes(conn):
    (pages, size), = conn.execute("SELECT * FROM pragma_page_count(), pragma_page_size()").fetchall()
    return pages * size


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", choices=[*QUERIES, "sql", "backfill", "prune"])
    ap.add_argument("query", nargs="?", help="SQL text, for the `sql` command")
    ap.add_argument("--db", type=Path, default=DEFAULT_DB)
    ap.add_argument("--since", default=None, metavar="YYYY-MM-DD",
                    help="named queries only (default: 7 days ago)")
    ap.add_argument("--before", default=None, metavar="YYYY-MM-DD",
                    help="prune only: clear bodies older than this (default: all finished jobs)")
    args = ap.parse_args(argv)

    if not args.db.exists():
        sys.exit(f"no such database: {args.db}")
    conn = sqlite3.connect(args.db)

    if args.command == "backfill":
        return backfill(conn)
    if args.command == "prune":
        return prune(conn, args.before)
    if args.command == "sql":
        if not args.query:
            sys.exit("sql needs a query: scripts/db.py sql \"select ...\"")
        return _print(*_rows(conn, args.query))

    since = (time.mktime(time.strptime(args.since, "%Y-%m-%d")) if args.since
             else time.time() - 7 * 86400)
    _print(*_rows(conn, QUERIES[args.command], {"since": since}))


if __name__ == "__main__":
    main()
