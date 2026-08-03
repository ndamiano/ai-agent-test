# Backups & restore

The control plane is one box on purpose (`docs/architecture.md`); this is what makes losing that
box survivable. Two mechanisms, one S3-compatible target:

| what | how | when |
|---|---|---|
| `platform.db` + `auth.db` (`/data` on the `maestro-data` volume) | **Litestream** — the `litestream` compose service streams every WAL segment to S3 | continuous |
| `runs/` tree (`/data/runs` — spec, cursor, turn log, game source + `game.git` history) | **restic** — `scripts/backup_runs.sh` on a host cron/timer | nightly |

Deliberately NOT backed up:

- `runtime/games/` (the `maestro-games` volume) — staged bundles are a copy of each run's `game/`
  folder; the restore drill re-stages them from the restored runs.
- `.env` + `src/config/settings.json` — secrets and box config never enter a backup target. Keep
  the canonical copies in the password manager; recreating them is a provisioning step, not a
  restore step.

## Secrets to provision (never committed)

In `.env` (consumed by the `litestream` compose service; template in `.env.example`):

- `LITESTREAM_ACCESS_KEY_ID` / `LITESTREAM_SECRET_ACCESS_KEY` — S3 credentials
- `LITESTREAM_S3_BUCKET` / `LITESTREAM_S3_ENDPOINT` / `LITESTREAM_S3_REGION` — the target

On the host, in `/etc/maestro-backup.env` (root-owned, `chmod 0600`; sourced by the cron line —
write it as `export KEY=value` lines, or a sourced-but-unexported var never reaches restic):

- `RESTIC_REPOSITORY` — `s3:https://<endpoint>/<bucket>/restic`
- `RESTIC_PASSWORD` (or `RESTIC_PASSWORD_FILE`) — encrypts the repo. **Losing it loses the
  backup**; it belongs in the password manager alongside the S3 keys.
- `AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY` — the same S3 credentials, under the names restic
  reads.

The litestream service exits at boot when its vars are missing — a box without replication
credentials announces itself instead of running bare.

## DB replication (Litestream)

`litestream.yml` (repo root, bind-mounted into the service) names the two DBs and their replica
paths (`litestream/platform`, `litestream/auth`). Both stores open their DB in WAL mode —
Litestream replicates the WAL, so that is a precondition, not a preference. Nothing to schedule:
`docker compose up -d` runs it alongside the app. Check it is alive after any deploy:

```bash
docker compose logs --tail 5 litestream        # no restart loop, "replicating" lines
```

## runs/ backup (restic)

`scripts/backup_runs.sh`: init-if-needed, one snapshot of the runs tree (tag `maestro-runs`),
then `forget --keep-daily 7 --keep-weekly 4 --keep-monthly 6 --prune`. Install restic on the host
(`apt install restic`), then schedule it — the unit is documented here, not auto-installed.

Cron (`/etc/cron.d/maestro-backup`):

```
15 3 * * * root . /etc/maestro-backup.env && /opt/maestro/scripts/backup_runs.sh >> /var/log/maestro-backup.log 2>&1
```

Or a systemd timer, if the box prefers it:

```ini
# /etc/systemd/system/maestro-backup.service
[Unit]
Description=Maestro runs/ backup

[Service]
Type=oneshot
EnvironmentFile=/etc/maestro-backup.env
ExecStart=/opt/maestro/scripts/backup_runs.sh

# /etc/systemd/system/maestro-backup.timer
[Unit]
Description=Nightly Maestro runs/ backup

[Timer]
OnCalendar=03:15
Persistent=true

[Install]
WantedBy=timers.target
```

`systemctl enable --now maestro-backup.timer`.

---

## Restore drill (fresh box)

A backup that has never been restored is not a backup — run this drill against a scratch box
before trusting it, and after any change to this machinery. Order matters: DBs and runs land
**before** the app first serves traffic with them.

1. **Provision + configure.** On the new box: `./scripts/provision.sh`, then recreate `.env`
   (from the password manager — includes the `LITESTREAM_*` vars) and `src/config/settings.json`
   in `/opt/maestro`. On the host: `apt install restic` and recreate `/etc/maestro-backup.env`.

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

5. **Restore `runs/` from restic** (snapshot paths are absolute — the `maestro-data` mountpoint,
   which is identical on a stock Docker host, so `--target /` puts them back in place):

   ```bash
   . /etc/maestro-backup.env
   restic snapshots --tag maestro-runs        # confirm what you are about to restore
   sudo -E restic restore latest --tag maestro-runs --target /
   ```

   If the mountpoint differs, restore the subtree into the real one:
   `sudo -E restic restore "latest:<old-mountpoint>/runs" --target "$(docker volume inspect maestro-data -f '{{ .Mountpoint }}')/runs"`.

6. **Fix ownership.** Restores land root-owned; the app runs non-root. The volume root inherited
   the app user's ownership at first mount, so it is the authority:

   ```bash
   DATA_MOUNT=$(sudo docker volume inspect maestro-data -f '{{ .Mountpoint }}')
   sudo chown -R "$(sudo stat -c %u:%g "$DATA_MOUNT")" "$DATA_MOUNT"
   ```

7. **Start:** `docker compose up -d` (app + litestream, now replicating the restored DBs).

8. **Verify — the drill is not done until all four pass:**

   ```bash
   BASE=http://localhost:8000
   curl -fsS $BASE/healthz                                             # API up
   TOKEN=$(curl -fsS $BASE/auth/login -H 'content-type: application/json' \
       -d '{"handle":"<real-handle>","password":"<pw>"}' | python3 -c 'import json,sys; print(json.load(sys.stdin)["token"])')
                                                                       # auth.db: a real account logs in
   curl -fsS $BASE/api/games/ -H "Authorization: Bearer $TOKEN"        # platform.db: the games are back
   ```

   Staged bundles were not backed up — re-stage every restored run that has a playable game:

   ```bash
   docker compose exec -w /app/src app python -c "
   from pathlib import Path
   from maestro.codegen.staging import stage_for_play
   for run_dir in sorted(Path('/data/runs').iterdir()):
       if (run_dir / 'game' / 'index.html').exists():
           stage_for_play(run_dir, run_dir.name); print('staged', run_dir.name)
   "
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
