# SPDK Gerrit Checks Plugin

Displays GitHub Actions CI status in Gerrit's **Checks** tab. Each GitHub
Actions job appears as a separate check run with status, duration, and a
direct link to the job in GitHub.

## Architecture

```
Browser (Gerrit)         checks-api (FastAPI)        GitHub Actions
┌──────────────┐        ┌──────────────────┐        ┌──────────────┐
│ spdk-checks  │  GET   │ /checks-api/v1   │ webhook│              │
│ .js plugin   ├───────►│   /runs          │◄───────┤ workflow_run  │
│              │  POST  │   /trigger       │        │ workflow_job  │
│ Checks Tab   ├───────►│   /rerun         ├───────►│              │
│              │        │   /webhook/github│        │ dispatches   │
└──────────────┘        │   /runs/register │        └──────────────┘
                        └──────────────────┘
                          SQLite (WAL mode)
```

- **Frontend**: TypeScript plugin (`frontend/dist/spdk-checks.js`) loaded by
  Gerrit. Registers a `ChecksProvider` that polls the backend every 30 seconds.
- **Backend**: Python FastAPI service. Receives GitHub webhook events, stores
  run/job status in SQLite, serves it to the frontend.
- **Proxy**: nginx on the same domain (`review.spdk.io/checks-api/`) avoids
  CORS issues.

## Components

| Path | Description |
|------|-------------|
| `app.py` | FastAPI application with all REST endpoints |
| `config.py` | `ChecksConfig` dataclass, loads from `CHECKS_*` env vars |
| `database.py` | SQLite schema and CRUD operations |
| `github_client.py` | GitHub API client with retry logic |
| `webhook_handler.py` | GitHub webhook processing + HMAC validation |
| `Dockerfile` | Container image (python:3.13-alpine) |
| `frontend/` | TypeScript Gerrit plugin source + built JS |
| `tests/` | pytest backend tests (55 tests) |
| `dev/` | Development environment for manual testing |

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
| `POST` | `/checks-api/v1/runs/register` | Register Gerrit→GitHub run mapping |

## Testing

### Automated Tests

```bash
cd infra/checks/
./run_tests.sh
```

Or run backend and frontend tests separately:

```bash
# Backend (55 tests)
python3 -m pytest tests/ -v

# Frontend (13 assertions)
cd frontend/ && npm test
```

### Manual Testing

Start a local Gerrit instance with prepopulated test data:

```bash
cd infra/checks/dev/
./start-dev.sh --seed
```

This starts Gerrit + checks-api + nginx on `http://localhost:8080` and seeds
4 test changes with various CI statuses (completed, running, queued, all-pass).

To test manually:
1. Open http://localhost:8080
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
