# Backups & restore

The control plane is one box on purpose (`docs/architecture.md`); this is what makes losing that
box survivable. Two mechanisms, one S3-compatible target:

| what | how | when |
|---|---|---|
| `platform.db` + `auth.db` (`/data` on the `maestro-data` volume) | **Litestream** — the `litestream` compose service streams every WAL segment to S3 | continuous |
| `runs/` (spec, cursor, turn log, game source + `game.git` history) | **run archives** — `maestro/codegen/archive.py` uploads each run as one tar.gz through `tools/s3.py` | at every settled finalize, plus the `--archive-all` sweep |

Deliberately NOT backed up:

- `runtime/games/` (the `maestro-games` volume) — staged bundles are a copy of each run's `game/`
  folder; rehydrating a run re-stages it.
- Unsettled runs — a run that never reached a playable finalize has no archive. Its game folder is
  a failed build's leftovers; the DB rows (spec, charge, events) survive via Litestream.
- `.env` + `src/config/settings.json` — secrets and box config never enter a backup target. Keep
  the canonical copies in the password manager; recreating them is a provisioning step, not a
  restore step.

## Secrets to provision (never committed)

In `.env` (consumed by the `litestream` compose service; template in `.env.example`):

- `LITESTREAM_ACCESS_KEY_ID` / `LITESTREAM_SECRET_ACCESS_KEY` — S3 credentials
- `LITESTREAM_S3_BUCKET` / `LITESTREAM_S3_ENDPOINT` / `LITESTREAM_S3_REGION` — the target

In `src/config/settings.json`, the `s3` block (endpoint, region, bucket, access_key, secret_key) —
what the run archiver reads. A separate application key scoped to the same bucket keeps the two
consumers' blast radii apart.

The litestream service exits at boot when its vars are missing — a box without replication
credentials announces itself instead of running bare. The archiver logs and stands aside when its
block is empty: losing the archive is never a reason to lose a build.

## DB replication (Litestream)

`litestream.yml` (repo root, bind-mounted into the service) names the two DBs and their replica
paths (`litestream/platform`, `litestream/auth`). Both stores open their DB in WAL mode —
Litestream replicates the WAL, so that is a precondition, not a preference. Nothing to schedule:
`docker compose up -d` runs it alongside the app. Check it is alive after any deploy:

```bash
docker compose logs --tail 5 litestream        # no restart loop, "replicating" lines
```

## runs/ archives

Every settled finalize (error gate clean, no stage pending) uploads the whole run dir to
`runs/<run_id>.tar.gz`. Two commands cover everything the automatic path does not:

```bash
python -m maestro.codegen.run --archive-all       # upload every run the bucket lacks
```

Run it once after provisioning the bucket (the backfill for runs that predate archiving), and
nightly as the sweep that catches a finalize whose upload failed:

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

1. **Provision + configure.** On the new box: `./scripts/provision.sh`, then recreate `.env`
   (from the password manager — includes the `LITESTREAM_*` vars) and `src/config/settings.json`
   (including the `s3` block) in `/opt/maestro`.

2. **Ship + build.** From the dev box: `PROD_HOST=user@new-box ./scripts/deploy.sh`. It ends with
   the app healthy but EMPTY — that first boot is what creates the volumes with the right
   ownership.

3. **Stop the stack and clear the empty DBs** (Litestream restore refuses to overwrite a file):

   ```bash
   docker compose stop app litestream
   docker compose run --rm --entrypoint sh litestream -c 'rm -f /data/platform.db* /data/auth.db*'
   ```

4. **Restore both DBs from Litestream — pinning the generation.** That first boot already
   replicated the fresh EMPTY DBs as a new generation, so a bare `restore` (which takes the
   latest) would hand the empty one back. List the generations and pick the old box's — the
   created/updated timestamps make it unambiguous:

   ```bash
   docker compose run --rm litestream generations /data/platform.db
   docker compose run --rm litestream restore -generation <old-box-generation> /data/platform.db
   docker compose run --rm litestream generations /data/auth.db
   docker compose run --rm litestream restore -generation <old-box-generation> /data/auth.db
   ```

5. **Fix DB ownership** (the litestream container restores as root; the app runs non-root — the
   volume root inherited the app user's ownership at first mount, so it is the authority):

   ```bash
   DATA_MOUNT=$(sudo docker volume inspect maestro-data -f '{{ .Mountpoint }}')
   sudo chown -R "$(sudo stat -c %u:%g "$DATA_MOUNT")" "$DATA_MOUNT"
   ```

6. **Start:** `docker compose up -d` (app + litestream, now replicating the restored DBs).

7. **Rehydrate the games from their archives.** The restored `platform.db` knows every run; the
   bucket holds every settled one. Rehydrate re-stages as it lands, so this step also rebuilds
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

8. **Verify — the drill is not done until all of these pass:**

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

(`.backup` uses SQLite's online-backup API, so the copy is consistent under concurrent writers.)
The same via Litestream, which doubles as a check that the replica is restorable:

```bash
docker compose run --rm -v /tmp:/restore litestream restore -o /restore/platform-snap.db /data/platform.db
```
