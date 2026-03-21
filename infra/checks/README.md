# SPDK Gerrit Checks Plugin

Displays GitHub Actions CI status in Gerrit's **Checks** tab. Each GitHub
Actions job appears as a separate check run with status, duration, and a
direct link to the job in GitHub. Posts Verified +1/−1 labels based on
CI results.

## Architecture

```
Browser (Gerrit)         checks-api (FastAPI)        GitHub Actions
┌──────────────┐        ┌──────────────────┐        ┌──────────────┐
│ spdk-checks  │  GET   │ /checks-api/v1   │ webhook│              │
│ .js plugin   ├───────►│   /runs          │◄───────┤ workflow_run  │
│              │  POST  │   /trigger       │        │ workflow_job  │
│ Checks Tab   ├───────►│   /rerun         ├───────►│              │
│              │        │   /webhook/github│        │ dispatches   │
└──────────────┘        │   /webhook/gerrit│        └──────────────┘
                        │   /runs/register │
                        │   /queue/status  │
                        └──────────────────┘
                          SQLite (WAL mode)
```

- **Frontend**: TypeScript plugin (`frontend/dist/spdk-checks.js`) loaded by
  Gerrit. Registers a `ChecksProvider` that polls the backend every 30 seconds.
- **Backend**: Python FastAPI service. Receives GitHub webhook events, stores
  run/job status in SQLite, serves it to the frontend. Posts Verified labels
  to Gerrit when CI completes. Includes a fair-scheduling queue manager.
- **Proxy**: nginx on the same domain (`review.spdk.io/checks-api/`) avoids
  CORS issues.

## Components

| Path | Description |
|------|-------------|
| `app.py` | FastAPI application with all REST endpoints |
| `config.py` | `ChecksConfig` dataclass, loads from `CHECKS_*` env vars |
| `database.py` | SQLite schema and CRUD operations |
| `github_client.py` | GitHub API client with retry logic |
| `webhook_handler.py` | GitHub webhook processing, HMAC validation, Verified votes |
| `queue_manager.py` | Fair-scheduling queue (owner-based round-robin) |
| `Dockerfile` | Container image (python:3.13-alpine) |
| `frontend/` | TypeScript Gerrit plugin source + built JS |
| `tests/` | pytest backend tests (136 tests) |
| `dev/` | Development environment and E2E tests |

## Setup

### Prerequisites
- Docker and Docker Compose
- GitHub PAT with `repo` and `actions` scopes
- GitHub webhook configured to send `workflow_run` and `workflow_job` events

### Configuration

Copy and edit the environment file:

```bash
cd infra/
cp .env.checks.example .env.checks
```

| Variable | Required | Description |
|----------|----------|-------------|
| `CHECKS_GITHUB_TOKEN` | Yes | GitHub Personal Access Token |
| `CHECKS_GITHUB_WEBHOOK_SECRET` | Yes | Shared secret for webhook HMAC validation |
| `CHECKS_GITHUB_REPO` | No | GitHub repo (default: `spdk/spdk-ci`) |
| `CHECKS_DATABASE_PATH` | No | SQLite path (default: `/app/data/checks.db`) |
| `CHECKS_API_KEY` | No | API key for trigger/rerun/register endpoints |
| `CHECKS_GERRIT_USER` | No | Gerrit username for Verified votes |
| `CHECKS_GERRIT_PASSWORD` | No | Gerrit password for Verified votes |
| `CHECKS_QUEUE_MAX_RUNNING` | No | Max concurrent workflows (default: 3) |
| `CHECKS_QUEUE_POLL_INTERVAL` | No | Queue poll interval in seconds (default: 10) |
| `LOG_LEVEL` | No | Logging level (default: `INFO`) |

### Deployment

The checks-api service is already defined in `infra/docker-compose.yaml`:

```bash
cd infra/
docker compose up -d --build checks-api
```

The plugin JS file is mounted into Gerrit's plugins directory automatically.

### GitHub Webhook

Configure a webhook in GitHub (repo Settings → Webhooks):

- **URL**: `https://review.spdk.io/checks-api/v1/webhook/github`
- **Content type**: `application/json`
- **Secret**: Same as `CHECKS_GITHUB_WEBHOOK_SECRET`
- **Events**: Select `Workflow runs` and `Workflow jobs`

## API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/checks-api/v1/health` | Health check |
| `GET` | `/checks-api/v1/changes/{change}/patchsets/{ps}/runs` | Get CI runs for a change |
| `POST` | `/checks-api/v1/changes/{change}/patchsets/{ps}/trigger` | Trigger a new CI run |
| `POST` | `/checks-api/v1/changes/{change}/patchsets/{ps}/rerun` | Rerun failed jobs |
| `POST` | `/checks-api/v1/webhook/github` | GitHub webhook receiver |
| `POST` | `/checks-api/v1/webhook/gerrit` | Gerrit webhook for queue manager |
| `POST` | `/checks-api/v1/runs/register` | Register Gerrit→GitHub run mapping |
| `GET` | `/checks-api/v1/queue/status` | Queue status (pending, running counts) |

## Verified Votes

When a GitHub Actions workflow completes, the backend automatically posts
a Verified label to Gerrit:

- **Success** → Verified +1 with "Build Successful: all CI jobs passed."
- **Failure** → Verified −1 with "Build Failed: one or more CI jobs failed."
- **Cancelled** → No vote posted.

Requires `CHECKS_GERRIT_USER` and `CHECKS_GERRIT_PASSWORD` to be configured.

## Testing

### Automated Tests

Run all 136 unit/integration tests:

```bash
cd infra/checks/
./run_tests.sh
```

### End-to-End Tests

Run the full E2E suite (builds from clean state, deploys, tests everything):

```bash
cd infra/checks/dev/
./run-e2e-tests.sh           # run 38 tests, then tear down
./run-e2e-tests.sh --keep    # run tests, leave env for inspection
./run-e2e-tests.sh --cleanup # just tear down
```

The E2E tests verify: Gerrit setup, change creation, checks data via REST API,
Verified vote posting, idempotency, edge cases, queue status, frontend plugin
serving, and container health.

### Manual Testing

Start a local Gerrit instance with prepopulated test data:

```bash
cd infra/checks/dev/
./start-dev.sh --seed
```

This starts Gerrit + checks-api + nginx on `http://localhost:9080` and seeds
4 test changes with various CI statuses (completed, running, queued, all-pass).

To test manually:
1. Open http://localhost:9080
2. Create a change: clone `test-project`, commit, push to `refs/for/master`
3. Navigate to the change and click the **Checks** tab
4. Seeded data appears for changes 1–4

Stop the environment:

```bash
./stop-dev.sh
```

## Frontend Development

To modify the plugin:

```bash
cd infra/checks/frontend/
npm install
# Edit src/*.ts files
npm run build    # Rebuilds dist/spdk-checks.js
npm test         # Validates the build
```

The built `dist/spdk-checks.js` is committed to the repo so the server doesn't
need Node.js. Rebuild and commit after making changes.

## Transition Strategy

The plugin runs alongside the existing `summary.yml` workflow initially:
1. **Phase 1**: Both summary.yml and Checks tab show CI status (current)
2. **Phase 2**: Disable Gerrit webhooks for events the plugin handles
3. **Phase 3**: Remove summary.yml once the plugin is stable

## Troubleshooting

**Checks tab shows "No checks"**
- Verify the plugin is loaded: Gerrit → Documentation → Plugins → spdk-checks
- Check checks-api logs: `docker compose logs checks-api`
- Verify webhook delivery in GitHub repo Settings → Webhooks → Recent Deliveries

**Checks tab shows "Failed to fetch CI status"**
- Check nginx proxying: `curl https://review.spdk.io/checks-api/v1/health`
- Verify checks-api is running: `docker compose ps checks-api`

**Webhook events not updating status**
- Check HMAC secret matches between GitHub and `CHECKS_GITHUB_WEBHOOK_SECRET`
- Check checks-api logs for signature validation errors
- Verify webhook is configured for `workflow_run` and `workflow_job` events

**"Run CI" button returns error**
- Verify `CHECKS_GITHUB_TOKEN` has `repo` and `actions:write` scopes
- Check if the change is WIP, private, or not the latest patchset

**Verified vote not posted**
- Verify `CHECKS_GERRIT_USER` and `CHECKS_GERRIT_PASSWORD` are set
- Check that the Verified label exists on the Gerrit project
- Check checks-api logs for "Error posting Verified vote" messages
