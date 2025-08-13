# SPDK Continuous Integration

**DISCLAIMER: This repository is an area of very active development.
Outdated documentation and breaking changes might occur.**

The SPDK CI repository contains workflows and actions responsible for
test execution. The actual test scripts (e.g., `autotest.sh`) and test
environment preparation scripts (e.g., `autotest_setup.sh`, `pkgdep.sh`)
are maintained in the SPDK repository.

## Motivation

The goal is to create a unified test execution framework for CI systems
across multiple locations, companies, and hardware, ensuring that a minimal
set of tests can be executed without relying on resources outside of GitHub.

## Test Triggering

Gerrit offers an [API](https://gerrit-review.googlesource.com/Documentation/rest-api.html)
that allows reactions to a multitude of events. This is extremely useful
for interacting with specific changes, reacting to comments, and merges.

The Gerrit [webhooks plugin](https://gerrit.googlesource.com/plugins/webhooks/)
is now used to forward a subset of Gerrit events to `gerrit-webhook-handler.yml`.
This workflow serves as the starting point for further test executions.

## Common SPDK Tests

A subset of SPDK tests is intended to be executed only on GitHub-hosted runners.
The workflow `spdk-common-tests.yml` is the starting point for these tests.
These runners have limited resources and, for the purpose of SPDK tests,
can only suffice as VMs.

## Community CI Workflows

Workflows to be executed on SPDK Community self-hosted runners should be
attached to the webhook handler. These runners might have different resources
and environments; as such, they should be provisioned and attached as self-hosted
runners, outside of any workflow in this repository.

The self-hosted runners can reuse the same setup as common SPDK tests.
Attaching new self-hosted runners to the GitHub repository is a privileged operation,
so please reach out on #spdk-ci if you are interested in participating.

## Actions

Some operations are common enough to be abstracted out and reused across workflows.
A set of actions to interact with Gerrit, allowlist GitHub organization members,
or manage artifacts of the SPDK CI repository, shall be placed in `.github/actions/*`.

## Locally testing actions

install see <https://nektosact.com/installation>

or use [GitHub CLI](https://cli.github.com/)

```bash
gh extension install https://github.com/nektos/gh-act
```

list all actions

```bash
$ gh act --list
INFO[0000] Using docker host 'unix:///var/run/docker.sock', and daemon socket 'unix:///var/run/docker.sock'
Stage  Job ID          Job name        Workflow name                Workflow file               Events
0      source-archive  source-archive  build_qcow2                  build_qcow2.yml             workflow_dispatch
0      common-tests    common-tests    SPDK per-patch tests         gerrit-webhook-handler.yml  workflow_dispatch,repository_dispatch,pull_request
0      get_qcow        get_qcow        per_patch                    per_patch.yml               workflow_call
0      pre-commit      pre-commit      selftest                     selftest.yml                push,pull_request
0      checkout_spdk   checkout_spdk   SPDK per-patch common tests  spdk-common-tests.yml       workflow_dispatch,workflow_call
1      build-qcow2     build-qcow2     build_qcow2                  build_qcow2.yml             workflow_dispatch
1      autorun         autorun         per_patch                    per_patch.yml               workflow_call
1      tests           tests           SPDK per-patch common tests  spdk-common-tests.yml       workflow_dispatch,workflow_call
2      report          report          SPDK per-patch common tests  spdk-common-tests.yml       workflow_dispatch,workflow_call
```

run all common tests

```bash
 $ gh act --job tests --secret GITHUB_TOKEN=$(gh auth token) workflow_dispatch
```

run a workflow using repository_dispatch event (example events provided in `.github/example_events`)

```bash
 $ gh act --job parse_comment --secret GITHUB_TOKEN=$(gh auth token) -e .github/example_events/comment-added.json repository_dispatch
```

for more examples, visit <https://nektosact.com/>
