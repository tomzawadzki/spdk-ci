# Testing runner detection on a fork

This guide is a hands-on walkthrough for verifying the self-hosted
runner detection and routing logic in `gerrit-webhook-handler.yml` on a
personal GitHub fork. You will register one or two runners, dispatch
the handler workflow, and confirm how each job reacts.

Algorithm details live in the script docstring:
[`.github/scripts/detect_runners.sh`](../.github/scripts/detect_runners.sh).

## Prerequisites

1. Fork `spdk/spdk-ci` and enable Actions (`Settings` > `Actions` >
   `General` > Allow all actions).

2. Create a fine-grained personal access token at
   <https://github.com/settings/personal-access-tokens/new>:
   - Resource owner: your GitHub account.
   - Repository access: "Only select repositories" > your fork.
   - Repository permissions:
     - **Administration: Read-only** (list runners)
     - **Actions: Read-only** (count in-flight workflow runs)

3. Add the token to the fork under `Settings` > `Secrets and variables`
   > `Actions` > `New repository secret`:
   - Name: `RUNNER_STATUS_TOKEN`
   - Value: the PAT from step 2.

4. Register one or more self-hosted runners following GitHub's
   [self-hosted runner
   instructions](https://docs.github.com/en/actions/hosting-your-own-runners/managing-self-hosted-runners/adding-self-hosted-runners).
   Attach a category label when configuring each runner:
   - `shr_generic` - a runner that can run the autorun matrix.
   - `shr_rdma` - a runner capable of NVMe-oF RDMA tests.

   Vendor-specific labels (for example `hpe-rdma-vm`) are allowed
   alongside; the workflow ignores them.

## Triggering a test run

In the fork, go to `Actions` > `SPDK per-patch tests` > `Run workflow`.
Leave `client_payload` empty to dispatch against SPDK master.

Open the `Detect self-hosted runners` job and expand the `Probe runner
categories` step. You should see one line per category on stderr:

```
detect_runners[generic]: online=... idle=... busy=... other_workflows_running=... runners_per_workflow=... reserved_by_others=... available=...
detect_runners[rdma]:    online=... idle=... busy=... ...
```

`available` is what the job emits as `shr_<category>_idle`; downstream
jobs gate on or route to self-hosted runners based on those counts.

## Scenarios

Run each scenario by adjusting which runners are online, dispatching the
workflow, and checking the results listed under "Expect".

### No runners registered

Stop all self-hosted runners (or skip step 4 above).

Expect:

- `detect_runners` outputs `shr_generic_online=0`, `shr_generic_idle=0`,
  `shr_rdma_online=0`, `shr_rdma_idle=0`.
- `NVMe-oF RDMA tests` job is **skipped**; the `Job summary` notes
  "NVMe-oF RDMA tests skipped".
- Every `Common tests` matrix entry runs on `ubuntu-latest`.

Repeat once without `RUNNER_STATUS_TOKEN` set. The step logs
`::warning::RUNNER_STATUS_TOKEN is not set -- self-hosted jobs will skip`
and still emits zeros; the workflow does not fail.

### One online `shr_rdma` runner

Start exactly one runner labeled `shr_rdma`.

Expect:

- `shr_rdma_online=1`, `shr_rdma_idle=1`.
- `NVMe-oF RDMA tests` runs on your runner; its artifact is
  `nvmf-job-rdma`.
- `Common tests` matrix unchanged (all on `ubuntu-latest`).

### One online `shr_generic` runner

Start exactly one runner labeled `shr_generic` (no `shr_rdma`).

Expect:

- `shr_generic_online=1`, `shr_generic_idle=1`.
- The matrix entry tagged `shr_generic_rank: 1` in
  [`spdk-common-tests.yml`](../.github/workflows/spdk-common-tests.yml)
  runs on your runner.
- The entry tagged `shr_generic_rank: 2` falls back to `ubuntu-latest`
  because only one runner is free.

### Two online `shr_generic` runners

Start two runners labeled `shr_generic`.

Expect:

- `shr_generic_online=2`, `shr_generic_idle=2`.
- Both `shr_generic_rank: 1` and `shr_generic_rank: 2` entries run on
  your `shr_generic` runners.

### One `shr_generic` and one `shr_rdma` (mixed)

Start one of each label.

Expect:

- `NVMe-oF RDMA tests` runs on the `shr_rdma` runner.
- `shr_generic_rank: 1` runs on the `shr_generic` runner.
- `shr_generic_rank: 2` falls back to `ubuntu-latest`.

## Notes

- The probe is non-fatal: a missing token, an API error, or zero online
  runners all fall through to zero counts so the workflow never blocks.
- When multiple `gerrit-webhook-handler` runs are in flight, the probe
  deducts a reservation so they cannot overcommit the same pool. The
  stderr line shows `other_workflows_running` and `reserved_by_others`;
  see the script docstring for the exact arithmetic.
