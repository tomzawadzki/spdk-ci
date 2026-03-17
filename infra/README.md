# SPDK's Backend Intrafstructure

This directory contains scripts and configuration files which are part of SPDK's backend cloud infrastructure,
used for hosting Gerrit Code Review instance and for interacting with Github Actions, which is currently the
continous integration system for SPDK.

**Note:**
This is **not** a complete or authoritative representation of the current backend environment state, and cannot
be used to fully replicate (or set up from scratch) current infrastructure. There is no automated testing in place,
and no automated deployments done. The intention for contents of this directory is to have them backed up in Git
repository and to use as reference only.

Currently there are no plans for a full and public, "end to end" documentation and automation of SPDK's cloud
infrastructure.

## Configuration

The services defined in `docker-compose.yaml` are configured via a single `.env` file.

To set up the environment:
1. Copy the example configuration: `cp .env.example .env`
2. Edit `.env` to match your environment (e.g., set `FORWARDER_GITHUB_TOKEN`, `GERRIT_DIR`).

### Key Environment Variables
- `GERRIT_DIR`: Path to the Gerrit data directory on the host.
- `GERRIT_URL`: The URL of the Gerrit instance.
- `FORWARDER_GITHUB_TOKEN`: A GitHub Personal Access Token used by the forwarder to trigger GitHub Actions.
- `FORWARDER_GITHUB_REPO`: The GitHub repository to trigger actions on (e.g., `spdk/spdk-ci`).
- `FORWARDER_TEST_MODE`: If `true`, the forwarder will log events but not actually send them to GitHub.
- `OUTPUT_DIR`: The directory where the forwarder and mergable_changes scripts write their output files (mapped to `/output` inside containers).

The Python scripts use fail-fast validation for these variables, meaning they will exit immediately with a clear error message if a required variable is missing or if a variable has an invalid type (e.g., a non-integer for `FORWARDER_QUEUE_PROCESS_INTERVAL`).
