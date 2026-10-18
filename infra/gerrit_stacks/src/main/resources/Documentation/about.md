# stacks

Search operators for series of open changes and for changes that fell behind
their branch.

## stack_stacks:

`stack_stacks:<value>` matches an open change by its place in its series. A
series is a chain of open changes, each built on the one below it.

| Value | Matches |
|---|---|
| `leaf` | the last change of a series, no open change has it as a parent |
| `ready` | passes, has no broken link, and all its open parents are ready |
| `submit` | ready, and no child is ready. Submitting it submits its open ancestors too. |
| `blocked` | not ready, while all its open parents are ready |
| `broken` | has a broken link, so it has to be rebased before it can be submitted |

A change can have several values, as with `is:`.

The graph holds the open changes of a branch that the caller can see, at
their current patch set, linked by their Git parents. Topics, hashtags and
older patch sets play no part. A parent that is not such a change must be
merged the way submit with REBASE_IF_NECESSARY or REBASE_ALWAYS counts it:
reachable from a branch, or a patch set of a change merged into the branch,
where REBASE_ALWAYS leaves the children of a change. Any
other parent, such as an outdated patch set, an abandoned or a private change,
is a broken link, and Gerrit refuses to submit the change until it is rebased.

A change passes if it is `is:submittable is:mergeable -is:wip`, which needs
the setting in [config.md](config.md).

| Graph (`+` passes) | ready | submit | blocked | broken |
|---|---|---|---|---|
| `A+ <- B+ <- C <- D+` | A, B | B | C | |
| `A+ <- B+`, with `C` and `D+` both on B | A, B, D | D | C | |
| `A <- B+` | | | A | |
| `M+` merging `X+` and `Y` | X | X | Y | |
| `A+ <- B`, B on an old patch set of A | A | A | B | B |

Other terms of a search filter only the matching changes, not their series.
The negation is the plain complement, so `-stack_stacks:leaf` matches closed
changes too. Submit requirements and watch filters cannot use the operator.

## behind_stacks:

`behind_stacks:<N>w` matches the open changes whose current patch set was
created more than N weeks before the commit time of the tip of their branch.
The branch tip and not the current time is the reference, so nothing gets
outdated on a quiet branch. Submit requirements and watch filters cannot use
the operator.

## Limits

A search builds the graph of each branch it visits from all its open changes.
If `index.maxLimit` is set and a branch has that many open changes, the search
fails with a message such as
`master of spdk/spdk has at least index.maxLimit (500) open changes`.

A request can use `stack_stacks:` at most 6 times, since each OR branch that
holds it scans all open changes again. A dashboard sends each section as its
own request.
