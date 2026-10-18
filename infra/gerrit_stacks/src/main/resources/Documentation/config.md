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

    [fragment]
        rebase = (-is:mergeable OR stack_stacks:broken OR behind_stacks:4w)
    [menu "Review"]
        item = My series, to do
        item = Queue status
    [dashboard "My series, to do"]
        signedIn = true
        foreach = owner:self
        section = Rebase now
        query = -is:wip -stack_stacks:ready ${rebase}
    [link "Queue status"]
        url = /queue_status.html

- `menu`: a menu, in the order of the file, with an `item` for each of its
  entries, in order. An item names a `dashboard` or a `link`.
- `dashboard`: titled by its name. `foreach` holds the terms every section
  has, and `status:open` is added to it. Each `section` is the title of a
  section and takes the `query` at the same position. The web UI decodes the
  URL twice, so a dashboard name, section title, `foreach` or `query` cannot
  hold `+`, `&` or `%`, and a section title cannot hold `=`.
- `link`: opens `url`, in the same tab unless `target` is set, for example
  `target = _blank` for a new one.
- `signedIn = true` on a dashboard or link keeps it out of the menus of
  anonymous users. A query with a `self` term needs it, since such a section
  fails for anonymous users and blanks the whole dashboard.
- `${name}` in `foreach` or `query` stands for the key `name` of `fragment`,
  and a fragment can use other fragments.

To change the menus, upload a change for `refs/meta/config` of All-Projects
and submit it. Gerrit reads the file again for every page that loads the
menus, so they change without a restart or plugin reload, once the browser
drops the copy it keeps for up to 5 minutes. An item with a mistake, such as a
missing dashboard or an unknown fragment, is left out, and Gerrit logs a
warning naming it at most once a minute.
