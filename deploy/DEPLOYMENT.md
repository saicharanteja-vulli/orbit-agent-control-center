# Orbit online deployment package

Prepared for a small single-server beta. Nothing has been published or purchased. This package does not contain your local database, accounts, reports, models, or credentials.

## Architecture and scope

Internet → Caddy (HTTPS) → private FastAPI service → SQLite on a persistent volume.
A separate worker shares the volume and calls a private Ollama service. Only Caddy publishes ports. The worker has outbound network access for public research; the API has only the internal network. Ollama has outbound access to download models but no published port. This is a single-host, single-worker design: do not scale replicas or use a network-mounted SQLite volume. For larger deployments, migrate the queue/database and storage before scaling.

Use an Ubuntu/Debian VPS with Docker Engine and Compose v2, a domain, persistent disk, and enough RAM for your selected model. The supplied limits allow Ollama up to 6 GB plus the API/worker/proxy; a machine with at least 8 GB RAM is a starting point, not a capacity promise. Measure actual concurrency, response time, and memory. Server compute, storage, DNS, and bandwidth may cost money.

## Deploy

1. Copy the project to your server. Install Docker Engine and Compose v2 from official Docker instructions. Keep the server patched. Allow inbound 80/443; restrict SSH to your administrative access. Do not expose 8000 or 11434.
2. Copy `.env.example` to `.env`. Set `ORBIT_DOMAIN` to your domain, without `https://` or a path. Point its A record (and AAAA only if supported) to the server. Keep signup disabled (`ORBIT_SIGNUP_ENABLED=0`) while performing preflight checks.
3. Run `docker compose config --quiet`, then `docker compose build`.
4. Initialize the administrator **from the server console**, before exposing the app:

   ```sh
   docker compose run --rm --no-deps api python -m backend.admin create youradmin
   ```

   Enter the password at the hidden prompts. No default password exists. The public setup endpoint is disabled in production. Existing local tasks are not uploaded. If intentionally migrating existing data, back it up and restore it to the persistent volume before account initialization; confirm ownership separately.

5. Start services and install a model:

   ```sh
   docker compose up -d
   docker compose exec ollama ollama pull qwen3:1.7b
   docker compose ps
   docker compose logs --tail=100 api worker caddy
   ```

   The small model is a functional baseline, not a quality guarantee. Read reports before approval. Keep Ollama private. Restrict downloaded models to what the host can run.

6. Open `https://YOUR_DOMAIN`. Caddy obtains and renews its TLS certificate and redirects HTTP. DNS and ports must work for certificate issuance. Do not bypass browser TLS warnings; fix DNS/firewall/configuration.
7. Complete the launch checklist below. Then set `ORBIT_SIGNUP_ENABLED=1` and run `docker compose up -d` to enable public member signup.

Pin the Caddy, Python, Node and Ollama images to verified release digests for your actual release, after pulling and testing them on the server. Current tags are a starting configuration and can change. Rebuild regularly for security updates; do not enable unattended application upgrades without a backup and migration check.

## Launch checklist (must run on the actual server)

- Container images build successfully on the target architecture; all services remain healthy after restart.
- HTTPS certificate is valid; HTTP redirects to HTTPS; only 80/443 are internet-accessible.
- Browser signup creates only a member, login/logout work, and cookies are Secure + HttpOnly + SameSite=Strict.
- Two separate accounts cannot list, read, stream, export, download, cancel, retry, or approve each other's tasks. Admins cannot bypass task ownership.
- Run real web research → inspect cited report → approve → download. Verify no file exists before approval and downloaded content matches the approved content.
- Password change invalidates old sessions; account disabling and role changes revoke sessions.
- Monitor disk, memory, worker heartbeat and failed tasks. Test per-user limits and recovery after restart.
- Test a backup and restore on a separate empty volume before opening signup.
- Replace privacy/operator contact information for your deployment and decide your retention policy. Task prompts and files are server data; users must understand that web queries leave the server. Do not advertise anonymous or end-to-end encrypted storage.

## Built-in limits

- Signup: 3 attempts/IP/hour, 20 attempts globally/hour, 100 accounts by default (`ORBIT_MAX_USERS`). Attempts include collisions. The IP comes from Caddy's overwritten `X-Orbit-Client-IP`; never publish the API directly or forward a user-controlled value from another proxy.
- Login: 20 attempts/IP/minute and 5 failed attempts/username/5 minutes.
- Production tasks: 3 unfinished tasks/account, 20 created/day UTC, 100 total/account, and a shared queue limit of 20. Retries count as new tasks. These are enforced transactionally. An account reaching total capacity needs operator attention; automatic deletion is not implemented.
- Mutating requests: 64 KB maximum. Model/tool budgets and artifact allowlists remain enforced.
- Account limits reduce abuse; they do not prevent distributed abuse or guarantee fairness. Public growth may require verified-email signup, CAPTCHA, more advanced rate limiting, and a fair queue.

## Backup and restore

Backups contain private reports, password hashes, and integration secrets. Store them encrypted outside the VPS and restrict access. Do not place them under a web root or commit them. Stop the API and worker so files and database form a consistent backup:

```sh
mkdir -p backups
chmod 700 backups
docker compose stop api worker
(umask 077; docker compose run --rm -T --no-deps api python -m backend.backup backup > backups/orbit-backup.tar.gz)
docker compose up -d api worker
```

Check command success and archive integrity before relying on a backup. Copy it to encrypted off-server storage using your infrastructure tooling. Never run `docker compose down -v` on your only copy.

For disaster recovery, prepare a **new deployment directory/project name and empty orbit_data volume**, stop API and worker, then:

```sh
docker compose run --rm -T --no-deps api python -m backend.backup restore < backups/orbit-backup.tar.gz
docker compose up -d
```

Restore refuses existing data, rejects unsafe archive paths, checks SQLite integrity, and invalidates sessions. Retain the original volume until you have verified restored artifacts, ownership and task history. Pending approved actions can resume on worker start; review operational state before bringing the worker back up.

## Account recovery

Users can change their password from Account settings while signed in. Forgotten passwords require the operator to verify identity independently, then run:

```sh
docker compose exec api python -m backend.admin reset-password username
```

This revokes all sessions for that account. Do not reset access based solely on a claimed username. Public signup is username/password based; there is no email verification, self-service email recovery or MFA in this release.

## Verification performed here

63 application tests and the frontend production build pass; Compose configuration validates. Backup/restore round-trip and unsafe archive rejection were tested. The public signup screen was visually checked on an isolated local preview. Docker CLI exists on the development Mac, but its engine is not running. Therefore container builds, container networking, certificate issuance, Linux image compatibility and actual-server deployment have **not** been executed here. This is a deployment candidate with a mandatory server preflight, not a claim of a completed or independently security-audited public launch.

References: [Caddy Automatic HTTPS](https://caddyserver.com/docs/automatic-https), [OWASP Session Management](https://cheatsheetseries.owasp.org/cheatsheets/Session_Management_Cheat_Sheet.html), [Docker Engine installation](https://docs.docker.com/engine/install/).
