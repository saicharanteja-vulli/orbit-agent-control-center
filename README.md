# Orbit — AI Agent Control Center

Research → sourced report → your approval → delivery.

Orbit is a student-built AI agent workspace with local Ollama execution, web research tools, per-user task history, and approval-gated file creation and webhook delivery. Built with AI-assisted development and verified with automated tests and live workflow checks.

**Status: working demo / single-server beta.** Not an independently audited production service.

[Temporary live demo](https://lenders-struck-essays-honest.trycloudflare.com) — create a member account to try it. Available only while the host Mac is awake and the tunnel runs; the URL may expire. Never submit sensitive information to a public demo.

**Stack:** React + TypeScript + Vite · FastAPI · SQLite · separate Python worker · Ollama · Docker/Caddy deployment package.

## Start

From this folder:

```sh
./start.sh
```

Open **http://127.0.0.1:8000**. The script starts the API and worker. The current workspace also contains a local Ollama runtime and Qwen3 models; the launch script starts that runtime if needed. On another machine, install Ollama separately and run `ollama pull qwen3:1.7b`.

Prerequisites for a fresh checkout: Python 3.11+ on macOS/Linux and Node.js 20.19+ or 22.12+. First launch installs Python packages and builds the frontend if necessary. Windows requires replacing the worker's Unix file lock; WSL is supported.

## What works

- Three agent profiles: planning, writing, and analysis.
- Real local generation with installed Ollama models, plus explicitly labelled deterministic demo mode.
- Durable task queue and separate worker process.
- Live run activity via authenticated polling, compatible with temporary Cloudflare tunnels.
- Persistent task history, timestamps, results, errors, and reported token counts.
- Search and status filtering for the latest 100 runs.
- Pause/resume the queue and enable/disable individual agents.
- Cancel queued/running/approval-pending runs. A cancelled run cannot commit a later result.
- Retry as a new linked attempt, retaining the previous history.
- Optional human approval before downloading the generated Markdown report.
- Rejecting an export preserves the draft for audit but blocks the download endpoint.
- Worker heartbeat, exclusive single-worker lock, and recovery of interrupted runs.
- Responsive dashboard, keyboard-accessible task dialog, and plain error states.

## Tool-enabled agents (v1.1)

In **New task**, enable tools for an Ollama run. The model can call:

| Tool | Behavior | Approval |
| --- | --- | --- |
| `web_search` | Query public search providers and return up to five source links/snippets. | Read-only, enabled per task |
| `read_webpage` | Read public HTTPS HTML/text/JSON, at most 2 MB / 12,000 extracted characters. | Read-only, enabled per task |
| `save_file` | Create a new `.md`, `.txt`, `.csv`, or `.json` file in a run-specific folder. | Always required |
| `send_webhook` | Send an exact JSON payload to the user-configured automation URL. | Always required; separately enabled per task |

Web search uses DDGS without an API key. Availability depends on the upstream search providers; failures appear in the tool history. Search queries and visited URLs leave your machine. Avoid putting sensitive information into public searches. The local model itself remains in Ollama.

### Connect n8n / Make / another webhook

1. Create an HTTPS webhook in your automation service. Use the URL for the workflow/environment you intend to trigger.
2. Open **Tools** in Orbit and paste the URL into the local password field. The app stores it in the local SQLite database; it is not encrypted at rest. Do not share that database or the URL.
3. Click **Save connection**. Saving validates the public HTTPS address but does not send a test request.
4. In **New task**, enable tools and **Allow webhook proposals**.
5. Ask the agent to prepare the payload. Open the run’s **Tools** tab to inspect the exact destination and full JSON.
6. **Approve this action** queues that exact proposal for one dispatch. **Reject action** resumes the agent with a denial; the same rejected proposal cannot be requested again in that run.

The app accepts only public HTTPS destinations on port 443, without embedded HTTP credentials. Local n8n endpoints, custom headers, private networks, and redirects are not supported by this first connector. Put credentials in your service’s normal secret webhook URL if required, and keep that URL private. Endpoint HTTP 2xx means accepted by the endpoint, not proof that the downstream automation finished.

### Approval and execution guarantees

Tool approvals are separate from optional report-export approval. Disabling report-export approval never disables approval for file creation or webhook sends. Proposals include immutable tool arguments and a digest; the approval must match the displayed digest. The database rejects argument changes. A worker checks the run state and approval again immediately before dispatch.

Approved file downloads are available in the tool card. Files are stored under `data/artifacts/<run-id>/<action-id>/`; the model cannot choose another directory or overwrite existing files. No shell, arbitrary commands, file deletion, broad filesystem access, authenticated browsing, or unrestricted HTTP client is exposed.

Webhook sends carry an `Idempotency-Key` equal to the action ID, but receivers may ignore it. Orbit never automatically resends an action after dispatch. A timeout or worker interruption during a write is marked **unknown**, since the effect may have happened. Verify the destination before approving another send. Changing/disconnecting the webhook invalidates unexecuted proposals; it cannot recall an in-flight request.

Webpage URLs are DNS-validated, rejected if any result is non-public, and pinned to the validated IP while TLS verifies the original hostname. Redirects are revalidated. Web content is passed to the model as untrusted data. The model may still misunderstand it; deterministic permissions and human approval enforce write boundaries. External Markdown images are not automatically loaded in reports.

The agent loop checkpoints messages and tool actions in SQLite, allows at most eight model steps, and resumes after approval. Pending approvals survive a worker restart. Interrupted executing actions are marked unknown and their runs fail rather than replaying the side effect. Cancelling before dispatch blocks the action; cancelling after dispatch cannot undo an effect that already started.

## Local deployment limits

This is a single-user, loopback-only application, not a public multi-tenant service. Host and mutation-Origin checks are not a substitute for authentication on a shared/public deployment. Do not bind it to 0.0.0.0 without authentication, authorization, TLS, and deployment hardening.

Queue pause and agent disable affect future claims, not already-running work. The model has bounded generation and execution limits. Small local models can choose incorrect tools or generate inaccurate content; all actual tool outcomes remain visible in the audit history.

## Architecture

```text
React + TypeScript dashboard
          |
FastAPI API + SSE ---- SQLite (WAL)
                          |
                   Python worker
                          |
                 Ollama local model → allowlisted tools → approval gate for writes
```

SQLite keeps setup simple and persists across restarts. A future multi-worker deployment should migrate the queue and records to PostgreSQL and introduce authenticated users.

## Project layout

- `backend/app.py`: API, local access checks, approvals, exports, event streaming.
- `backend/db.py`: schema, SQLite connections, event persistence.
- `backend/worker.py`: queue claims, local generation, cancellation checkpoints, recovery.
- `frontend/src/main.tsx`: dashboard and interactions.
- `frontend/src/style.css`: responsive styling.
- `tests/test_workflow.py`: automated workflow and failure tests.
- `data/control.db`: local data, created at runtime and ignored by Git.

## Development

```sh
.venv/bin/python -m uvicorn backend.app:app --host 127.0.0.1 --port 8000 --reload
# In a second terminal:
.venv/bin/python -m backend.worker
# In a third terminal:
cd frontend && npm run dev
```

The Vite development server proxies `/api` to the backend. For the single-port production build:

```sh
cd frontend && npm run build
```

## Tests

```sh
.venv/bin/python -m pytest -q
cd frontend && npm run build
```

Tests use isolated temporary databases. They cover persistence, approval enforcement, duplicate decisions, rejected exports, cancellation, retry lineage, crash recovery, input/access validation, SSE replay, model failures, and queue/agent controls.

## Configuration

- `CONTROL_DB`: database path; default `data/control.db` relative to the project.
- `OLLAMA_URL`: provider URL; default `http://127.0.0.1:11434`.

Use a local provider to preserve the local-only data flow. No model or search API keys are required. The webhook URL itself may contain a service-specific secret.

## API

Interactive API documentation: **http://127.0.0.1:8000/docs**.

- `POST /api/runs`: submit task with `task`, `agent`, `mode`, `model`, `approval_required`.
- `GET /api/runs` and `GET /api/runs/{id}`: history and details.
- `GET /api/runs/{id}/stream`: persisted activity stream with Last-Event-ID replay.
- `POST /api/runs/{id}/cancel|retry|approve|reject`: run controls.
- `GET /api/runs/{id}/export`: gated Markdown download.
- `GET /api/health`, `/api/models`, `/api/agents`: runtime information.
- `PATCH /api/agents/{id}`: enable/disable an agent.
- `POST /api/queue/pause|resume`: queue control.

## Next engineering steps

User accounts and authorization, PostgreSQL migration, streaming partial output, richer usage analytics, scheduled jobs, and multi-worker execution are separate extensions. They are not represented as implemented features.

## Verified build

On 14 October 2026: production frontend build passed; 13 backend tests passed; browser submission, approval, cancellation, queue pause/resume, and mobile layout checked. Ollama 0.40.4 with qwen3:0.6b generated a real planning report in approximately four seconds with 338 reported tokens. Approved report download was verified.

## Additional API endpoints

- `GET /api/tools`: available tools and approval requirements.
- `GET /api/integrations/webhook`: connection status and hostname (no secret URL).
- `PUT /api/integrations/webhook`: configure/disconnect with `{"url":"https://…"}` or an empty URL.
- `POST /api/actions/{id}/decision`: `{"approved":true,"digest":"displayed SHA-256"}`.
- `GET /api/actions/{id}/artifact`: download a successfully created file.

`POST /api/runs` accepts `enabled_tools` as an explicit allowlist. Existing runs retain tools disabled. Demo mode rejects enabled tools.

## v1.1 verification

- 45 automated tests pass, covering the original workflow plus immutable tool proposals, digest matching, blocked unapproved dispatch, cancellation, disabled tools, duplicate dispatch, restart recovery, webhook destination changes, unknown outcomes, redirect restrictions, and private-address blocking.
- Production TypeScript/Vite build passes.
- Live Qwen3 1.7B run completed public web search → read official Python documentation → propose `pathlib-notes.md` → pause for approval → save the exact approved content → produce a final report. All three tools succeeded; the stored run recorded 7,519 total reported tokens across its model steps.
- Webhook dispatch is verified with mocked endpoint tests. No real webhook has been configured or triggered. Add your endpoint in Tools to connect your automation.
- Qwen3 0.6B had unreliable native tool proposals; a schema-constrained fallback is included. Qwen3 1.7B is now the default when available.


## Interface redesign

The current direction follows the user's preference for a bright, spacious, premium interface. It replaces boxed panels with open sections, blue actions, a centered headline, an original sculptural orbit illustration, and list-based agent management. Task starters remain editable; approvals and execution APIs are unchanged. Desktop and 390px mobile previews and task-dialog navigation were checked, and the production build passes. The earlier 15-site research is preserved in DESIGN_RESEARCH.md as design history.

The latest visual update introduces sea-glass and copper accents, a split editorial hero, and three independently rotating rings around a stationary logo. Motion can be paused and respects the system reduced-motion preference. Production build and browser pause/resume checks passed.

## Sourced research delivery

Research file proposals require a successfully read source. Before approval, Orbit rejects report URLs outside that read-source set, repairs fully escaped Markdown newlines, and appends a deduplicated source list from successful tool records. Reports use Markdown or text. The approval digest covers the final report including its source list. After approval, the exact saved document becomes the displayed result; no further model rewrite is used. This verifies source provenance, not the factual correctness of every claim. Human review is still needed.

Validation: 48 automated tests pass, including full approval-to-download equality and missing/invented-source failures. A real Ollama run d0339e68-0413-4f05-88c2-5f5bd2d37d5e searched the web, read Python documentation, paused without creating a file, then delivered after explicit user approval. The actual artifact download, approved content, and displayed result matched exactly. No external webhook was sent in this test.

## Authentication and authorization

On first launch, create an administrator account in the browser (username 3–60 letters/digits/underscore/dot/hyphen; password 12–128 characters). That account receives all existing unowned local runs. Setup closes permanently once an account exists. There are no default credentials or public signup. Use Account settings to add accounts, change roles, or disable access.

- **Admin:** manages accounts, shared queue/agent configuration, and the shared webhook; only accesses and approves their own tasks.
- **Member:** creates and manages their own tasks, files, report exports and approvals. Shared webhook use is restricted to administrators.
- **Viewer:** reads only their own task history; cannot submit, retry, cancel, or approve. A downgraded member retains read access to their prior work.

Passwords use salted scrypt. Sessions use random opaque tokens stored hashed in SQLite, HttpOnly SameSite=Strict cookies, an eight-hour absolute expiry, and explicit logout revocation. Role/active changes revoke the affected user's sessions. Mutations require a session-bound CSRF token and same-origin request header. Login attempts are throttled. Run detail, lists, SSE, action decisions, exports, and artifact downloads enforce ownership server-side, including against administrators. Existing filesystem permissions still matter: someone controlling the OS/database can bypass application controls.

This remains a loopback-only local service. Cookies work over local HTTP; set ORBIT_SECURE_COOKIE=1 when using HTTPS. It is not configured for public hosting, password recovery, or MFA. Do not weaken the host restriction to publish it without a deployment review.

Validation: 54 tests passed after adding authentication, covering cross-user isolation, administrator non-bypass, CSRF, session expiry/logout, password/token hashing, login throttling, viewer restrictions, account revocation, duplicate bootstrap, and legacy ownership migration. Frontend production build passed.

## Online deployment candidate

See [deploy/DEPLOYMENT.md](deploy/DEPLOYMENT.md) for the current single-server deployment package, public signup, production safeguards, backup/restore and required server preflight. Local mode remains the default. The earlier local-only notes describe the development setup; production mode must use the supplied HTTPS reverse proxy and private networking. No hosting has been provisioned.

### Private data directory

Set `CONTROL_DB` inside a dedicated Orbit data directory, never directly inside a shared project folder or home directory. Orbit restricts that directory to owner-only access (0700), and its database and SQLite sidecars to 0600. Existing storage is hardened on connection; restored backups are hardened before use. This protects local file access but does not encrypt credentials at rest.
