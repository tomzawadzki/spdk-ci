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

## Gerrit plugins

`gerrit_stacks` is a Gerrit plugin with search operators for series of changes and outdated changes, see its
`src/main/resources/Documentation/about.md` and `config.md`. Build and test it with `mvn -B package` in that directory,
with JDK 25. The tests start a Gerrit of `gerrit.version` inside the JVM, and the jar is `target/stacks.jar`.

The `SPDK-CI build Gerrit plugin` workflow builds and tests it for the Gerrit tag in `docker-compose.yaml`, on demand
and on pull requests that change the plugin or that tag. Before it uploads the jar as the `stacks-plugin` artifact,
`gerrit_stacks/smoke.sh` runs the jar in that Gerrit image. To install it into the `plugins` directory that
`docker-compose.yaml` mounts:

    gh run download <run-id> -R spdk/spdk-ci -n stacks-plugin -D "${GERRIT_DIR}/plugins"

Gerrit loads the jar within a minute, without a restart, and unloads it within a minute once it is deleted. The plugin
needs the `gerrit.config` setting in its `config.md`. Gerrit's plugin list, `GET /plugins/`, shows the commit the jar
was built from as its version. Before a Gerrit upgrade, check the installed jar on the new image, and after the upgrade
run the workflow again and replace the jar:

    ENGINE=podman gerrit_stacks/smoke.sh docker.io/gerritcodereview/gerrit:<tag> "${GERRIT_DIR}/plugins/stacks.jar"

The menus the plugin adds to the menu bar come from `stacks.config` on `refs/meta/config` of All-Projects, see its
`config.md`. To install `gerrit_stacks/stacks.config`, or a change to it, upload it as a change for `refs/meta/config`
and submit it:

    git init all-projects && cd all-projects
    scp -O -P 29418 <admin>@<host>:hooks/commit-msg .git/hooks/
    git fetch ssh://<admin>@<host>:29418/All-Projects refs/meta/config && git checkout FETCH_HEAD
    cp <spdk-ci>/infra/gerrit_stacks/stacks.config . && git add stacks.config && git commit
    git push ssh://<admin>@<host>:29418/All-Projects HEAD:refs/for/refs/meta/config

The `mergable_changes` page and the outdated changes job need the plugin on the Gerrit they query. Without it, the page
shows what Gerrit answered instead of the changes, and the job skips its scan.
