# Configuration

## gerrit.config

`stack_stacks:` needs Gerrit to compute mergeability, with any value of
`change.mergeabilityComputationBehavior` but the default `NEVER`:

    [change]
        mergeabilityComputationBehavior = API_REF_UPDATED_AND_CHANGE_REINDEX

Without it, every `stack_stacks:` search fails with `'is:mergeable' operator
is not supported on this gerrit host`.

## stacks.config

The plugin adds the menus of `stacks.config` on `refs/meta/config` of
All-Projects to the menu bar, and none without the file.
`infra/gerrit_stacks/stacks.config` in spdk-ci is the copy that review.spdk.io
runs. The file uses the syntax of `gerrit.config`:

    [menu "CI queue"]
        item = Queue status
    [link "Queue status"]
        url = /queue_status.html

- `menu`: a menu, in the order of the file, with an `item` for each of its
  entries, in order. An item names a `link`.
- `link`: opens `url`, in the same tab unless `target` is set, for example
  `target = _blank` for a new one.

To change the menus, upload a change for `refs/meta/config` of All-Projects
and submit it. Gerrit reads the file again for every page that loads the
menus, so they change without a restart or plugin reload, once the browser
drops the copy it keeps for up to 5 minutes. An item with a mistake, such as a
missing link, is left out, and Gerrit logs a warning naming it at most once a
minute.
