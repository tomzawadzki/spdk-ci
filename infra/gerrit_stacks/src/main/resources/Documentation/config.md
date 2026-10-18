# Configuration

## gerrit.config

`stack_stacks:` needs Gerrit to compute mergeability, with any value of
`change.mergeabilityComputationBehavior` but the default `NEVER`:

    [change]
        mergeabilityComputationBehavior = API_REF_UPDATED_AND_CHANGE_REINDEX

Without it, every `stack_stacks:` search fails with `'is:mergeable' operator
is not supported on this gerrit host`.
