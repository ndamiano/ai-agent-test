# Backups & restore

The control plane is one box on purpose (`docs/architecture.md`); this is what makes losing that
box survivable. Two mechanisms, one S3-compatible bucket, one code path (`tools/s3.py`):

| what | how | when |
|---|---|---|
| `platform.db` + `auth.db` | **DB snapshots** — `tools/db_backup.py`, a control-plane thread: sqlite online-backup → gzip → `db/<name>/latest.db.gz` + a dated daily key | every 15 min, and within ~60s of any account or credit write (`mark_dirty`) |
| `runs/` (spec, cursor, turn log, game source + `game.git` history) | **run archives** — `maestro/codegen/archive.py` uploads each run as one tar.gz | at every settled finalize, plus the `--archive-all` sweep |

Worst-case DB loss is one interval; for account and money rows it is the dirty debounce. The
payment processor is the source of truth for payments, so ledger rows inside that window are
reconcilable. The mechanism survives the planned Postgres migration: the snapshot command swaps
for `pg_dump`, the keys and restore stay the same.

Deliberately NOT backed up:

- `runtime/games/` — staged bundles are a copy of each run's `game/` folder; rehydrating a run
  re-stages it.
- Unsettled runs — a run that never reached a playable finalize has no archive. Its game folder is
  a failed build's leftovers; the DB rows (spec, charge, events) survive via the snapshots.
- `.env` + `src/config/settings.json` — secrets and box config never enter a backup target. Keep
  the canonical copies in the password manager; recreating them is a provisioning step, not a
  restore step.

## Secrets to provision (never committed)

One application key, in the `s3` block of `src/config/settings.json`: `endpoint`, `region`,
`bucket`, `access_key`, `secret_key`. The names are S3's; B2 issues the same pair as
keyID (→ `access_key`) and applicationKey (→ `secret_key`). Both backup mechanisms read it; both idle with a log line
when it is empty — a bucket that is not there must never cost a build anything. Set the bucket's
lifecycle to keep only the last version of each object: `latest` keys are overwritten every
snapshot, and hidden prior versions otherwise accumulate silently.

## Verifying it runs

```bash
docker compose logs app | grep db_backup      # "db backup started", no failures
python -m maestro.codegen.run --archive-all   # once after provisioning: backfill every run
```

The nightly sweep behind the finalize-time uploads:

```
15 3 * * * root docker compose -f /opt/maestro/docker-compose.yml exec -T -w /app/src app python -m maestro.codegen.run --archive-all >> /var/log/maestro-archive.log 2>&1
```

`--evict <run_id>` reclaims the local disk (refusing without a verified remote copy);
`--rehydrate <run_id>` pulls a run back and re-stages it, and play/build/fix do the same on touch.

---

## Restore drill (fresh box)

A backup that has never been restored is not a backup — run this drill against a scratch box
before trusting it, and after any change to this machinery. Order matters: DBs land **before**
the app first serves traffic with them.

1. **Provision + configure.** On the new box: `./scripts/provision.sh`, then recreate `.env` and
   `src/config/settings.json` (including the `s3` block) from the password manager, in
   `/opt/maestro`.

2. **Ship + build.** From the dev box: `PROD_HOST=user@new-box ./scripts/deploy.sh`. It ends with
   the app healthy but EMPTY — that first boot creates the volumes with the right ownership.
   Stop it before it snapshots its empty DBs over the good ones: `docker compose stop app`
   (the first snapshot is minutes away, but do not race it).

3. **Restore both DBs from the bucket** (any S3 client or the app's own; from the host):

   ```bash
   DATA_MOUNT=$(sudo docker volume inspect maestro-data -f '{{ .Mountpoint }}')
   docker compose run --rm --entrypoint sh -w /app/src app -c '
     python - <<EOF
   import gzip
   from pathlib import Path
   from tools import s3
   for name in ("platform", "auth"):
       Path(f"/data/{name}.db").write_bytes(gzip.decompress(s3.get(f"db/{name}/latest.db.gz")))
       print("restored", name)
   EOF'
   sudo chown -R "$(sudo stat -c %u:%g "$DATA_MOUNT")" "$DATA_MOUNT"
   ```

4. **Start:** `docker compose up -d`.

5. **Rehydrate the games from their archives.** The restored `platform.db` knows every run; the
   bucket holds every settled one. Rehydrate re-stages as it lands, so this also rebuilds
   `runtime/games/`:

   ```bash
   docker compose exec -w /app/src app python -c "
   import sqlite3
   from maestro.codegen import archive
   conn = sqlite3.connect('/data/platform.db')
   for (rid,) in conn.execute('SELECT id FROM games'):
       if archive.rehydrate(rid): print('restored', rid)
   "
   ```

   (Games can also be left to rehydrate lazily — play/build/fix pull them on touch — but the
   drill restores eagerly so the verification below means something.)

6. **Verify — the drill is not done until all of these pass:**

   ```bash
   BASE=http://localhost:8000
   curl -fsS $BASE/healthz                                             # API up
   TOKEN=$(curl -fsS $BASE/auth/login -H 'content-type: application/json' \
       -d '{"handle":"<real-handle>","password":"<pw>"}' | python3 -c 'import json,sys; print(json.load(sys.stdin)["token"])')
                                                                       # auth.db: a real account logs in
   curl -fsS $BASE/api/games/ -H "Authorization: Bearer $TOKEN"        # platform.db: the games are back
   ```

   Then log in through the browser and PLAY one of the restored games — the drill ends at a game
   on screen, not at a 200.

---

## Query hygiene (inspecting the live DBs)

Never point `sqlite3` (or anything else) at the live files under `/data` — a stray query takes
locks against the app's connections, and reading the DB without its WAL shows a stale or torn
view. Pull a snapshot and inspect that:

```bash
sqlite3 "file:$(docker volume inspect maestro-data -f '{{ .Mountpoint }}')/platform.db?mode=ro" \
    ".backup /tmp/platform-snap.db" && sqlite3 /tmp/platform-snap.db
```

(`.backup` uses SQLite's online-backup API — the same mechanism the backup thread runs — so the
copy is consistent under concurrent writers.) Or just pull the bucket's `latest` snapshot, which
doubles as a check that the backup is restorable.
