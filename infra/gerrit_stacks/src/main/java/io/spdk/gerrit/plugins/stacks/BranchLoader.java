package io.spdk.gerrit.plugins.stacks;

import static java.util.stream.Collectors.toSet;

import com.google.common.collect.ArrayListMultimap;
import com.google.common.collect.ImmutableSetMultimap;
import com.google.common.collect.ListMultimap;
import com.google.common.flogger.FluentLogger;
import com.google.gerrit.entities.BranchNameKey;
import com.google.gerrit.entities.Change;
import com.google.gerrit.entities.PatchSet;
import com.google.gerrit.entities.RefNames;
import com.google.gerrit.exceptions.StorageException;
import com.google.gerrit.index.IndexConfig;
import com.google.gerrit.index.query.Predicate;
import com.google.gerrit.index.query.QueryParseException;
import com.google.gerrit.server.git.GitRepositoryManager;
import com.google.gerrit.server.query.change.ChangeData;
import com.google.gerrit.server.query.change.ChangePredicates;
import com.google.gerrit.server.query.change.InternalChangeQuery;
import com.google.inject.Inject;
import com.google.inject.Provider;
import com.google.inject.Singleton;
import io.spdk.gerrit.plugins.stacks.StackOperator.Value;
import java.io.IOException;
import java.util.ArrayDeque;
import java.util.ArrayList;
import java.util.HashMap;
import java.util.HashSet;
import java.util.List;
import java.util.Map;
import java.util.Set;
import org.eclipse.jgit.errors.MissingObjectException;
import org.eclipse.jgit.lib.ObjectId;
import org.eclipse.jgit.lib.Ref;
import org.eclipse.jgit.lib.Repository;
import org.eclipse.jgit.revwalk.RevCommit;
import org.eclipse.jgit.revwalk.RevWalk;

/**
 * Builds the graph of the open changes of one branch as one caller sees them, and finds the {@link
 * Value}s of each change.
 */
@Singleton
class BranchLoader {
  private static final FluentLogger logger = FluentLogger.forEnclosingClass();

  /** Commit times are not strictly ordered, so walk this long past the oldest parent. */
  private static final int CLOCK_SKEW_SECONDS = 24 * 60 * 60;

  /** The changes of each {@link Value} in one branch. */
  record Graph(ImmutableSetMultimap<Value, Change.Id> changes) {
    boolean has(Value value, Change.Id id) {
      return changes.containsEntry(value, id);
    }
  }

  private final Provider<InternalChangeQuery> queryProvider;
  private final GitRepositoryManager repoManager;
  private final IndexConfig indexConfig;

  @Inject
  BranchLoader(
      Provider<InternalChangeQuery> queryProvider,
      GitRepositoryManager repoManager,
      IndexConfig indexConfig) {
    this.queryProvider = queryProvider;
    this.repoManager = repoManager;
    this.indexConfig = indexConfig;
  }

  /**
   * @param visible whether the caller can see a change
   * @param criteria what a change needs on its own to be ready
   */
  Graph load(Predicate<ChangeData> visible, Predicate<ChangeData> criteria, BranchNameKey branch) {
    // index.maxLimit is unlimited by default, but a graph of a truncated list gives wrong answers.
    int limit = indexConfig.maxLimit();
    List<ChangeData> open = queryProvider.get().setLimit(limit).byBranchOpen(branch);
    if (open.size() >= limit) {
      throw new StorageException(
          new QueryParseException(
              String.format(
                  "%s of %s has at least index.maxLimit (%d) open changes",
                  branch.shortName(), branch.project().get(), limit)));
    }
    Map<ObjectId, Change.Id> changeByCommit = new HashMap<>();
    for (ChangeData cd : open) {
      PatchSet ps = cd.currentPatchSet();
      if (ps != null && visible.asMatchable().match(cd)) {
        changeByCommit.put(ps.commitId(), cd.getId());
      }
    }
    // is:submittable and is:mergeable come from the index, so ask it once for the whole branch.
    Set<Change.Id> passing =
        queryProvider
            .get()
            .setLimit(limit)
            .query(
                Predicate.and(
                    criteria,
                    ChangePredicates.project(branch.project()),
                    ChangePredicates.ref(branch.branch())))
            .stream()
            .map(ChangeData::getId)
            .collect(toSet());

    try (Repository repo = repoManager.openRepository(branch.project());
        RevWalk rw = new RevWalk(repo)) {
      rw.setRetainBody(false);
      Map<Change.Id, List<Change.Id>> openParents = new HashMap<>();
      Map<Change.Id, List<ObjectId>> otherParents = new HashMap<>();
      Set<Change.Id> broken = new HashSet<>();
      for (Map.Entry<ObjectId, Change.Id> e : changeByCommit.entrySet()) {
        Change.Id id = e.getValue();
        List<Change.Id> openOnes = new ArrayList<>();
        List<ObjectId> others = new ArrayList<>();
        try {
          for (RevCommit parent : rw.parseCommit(e.getKey()).getParents()) {
            Change.Id parentChange = changeByCommit.get(parent);
            if (parentChange != null) {
              openOnes.add(parentChange);
            } else {
              others.add(parent.copy());
            }
          }
        } catch (MissingObjectException ex) {
          logger.atWarning().withCause(ex).log("Commit of change %s is missing", id);
          broken.add(id);
        }
        openParents.put(id, openOnes);
        otherParents.put(id, others);
      }

      Set<ObjectId> candidates = new HashSet<>();
      otherParents.values().forEach(candidates::addAll);
      Set<ObjectId> merged = findMerged(repo, rw, branch, candidates);
      otherParents.forEach(
          (id, parents) -> {
            for (ObjectId parent : parents) {
              if (!merged.contains(parent)) {
                logger.atFine().log(
                    "Change %s has parent %s, neither an open change nor merged",
                    id, parent.name());
                broken.add(id);
              }
            }
          });
      logger.atFine().log(
          "stacks %s: %d open, %d visible, %d passing, %d broken",
          branch, open.size(), changeByCommit.size(), passing.size(), broken.size());
      return compute(openParents, passing, broken);
    } catch (IOException e) {
      throw new StorageException("Cannot read " + branch.project(), e);
    }
  }

  /**
   * Returns the commits among {@code parents} that Gerrit's submit with REBASE_IF_NECESSARY or
   * REBASE_ALWAYS takes as merged: reachable from a branch tip, or a patch set of a change merged
   * into this branch.
   */
  private Set<ObjectId> findMerged(
      Repository repo, RevWalk rw, BranchNameKey branch, Set<ObjectId> parents) throws IOException {
    Set<ObjectId> remaining = new HashSet<>();
    int oldest = Integer.MAX_VALUE;
    for (ObjectId id : parents) {
      try {
        oldest = Math.min(oldest, rw.parseCommit(id).getCommitTime());
        remaining.add(id);
      } catch (MissingObjectException e) {
        // Not in the repository, so not merged.
      }
    }
    Set<ObjectId> merged = new HashSet<>();
    if (remaining.isEmpty()) {
      return merged;
    }
    for (Ref ref : repo.getRefDatabase().getRefsByPrefix(RefNames.REFS_HEADS)) {
      rw.markStart(rw.parseCommit(ref.getObjectId()));
    }
    int cutoff = oldest - CLOCK_SKEW_SECONDS;
    for (RevCommit c = rw.next(); c != null && c.getCommitTime() >= cutoff; c = rw.next()) {
      if (remaining.remove(c)) {
        merged.add(c.copy());
        if (remaining.isEmpty()) {
          break;
        }
      }
    }
    // Submit with REBASE_ALWAYS leaves the children of a change on its old patch set.
    for (ObjectId id : remaining) {
      if (queryProvider.get().byBranchCommit(branch, id.name()).stream()
          .anyMatch(cd -> cd.change().isMerged())) {
        merged.add(id);
      }
    }
    return merged;
  }

  /**
   * @param openParents the open parents of each change
   * @param passing changes that meet the ready criteria on their own
   * @param broken changes with a parent that is neither an open change nor merged
   */
  private static Graph compute(
      Map<Change.Id, List<Change.Id>> openParents, Set<Change.Id> passing, Set<Change.Id> broken) {
    ListMultimap<Change.Id, Change.Id> children = ArrayListMultimap.create();
    openParents.forEach((id, parents) -> parents.forEach(p -> children.put(p, id)));

    ImmutableSetMultimap.Builder<Value, Change.Id> values = ImmutableSetMultimap.builder();
    Set<Change.Id> ready = new HashSet<>();
    for (Change.Id id : topologicalOrder(openParents, children)) {
      if (!ready.containsAll(openParents.get(id))) {
        continue;
      }
      if (passing.contains(id) && !broken.contains(id)) {
        ready.add(id);
        values.put(Value.READY, id);
      } else {
        values.put(Value.BLOCKED, id);
      }
    }
    for (Change.Id id : openParents.keySet()) {
      List<Change.Id> next = children.get(id);
      if (next.isEmpty()) {
        values.put(Value.LEAF, id);
      }
      if (ready.contains(id) && next.stream().noneMatch(ready::contains)) {
        values.put(Value.SUBMIT, id);
      }
    }
    values.putAll(Value.BROKEN, broken);
    return new Graph(values.build());
  }

  /** Kahn's algorithm, parents before children. Iterative, so a deep series fits on the stack. */
  private static List<Change.Id> topologicalOrder(
      Map<Change.Id, List<Change.Id>> openParents, ListMultimap<Change.Id, Change.Id> children) {
    Map<Change.Id, Integer> pending = new HashMap<>();
    ArrayDeque<Change.Id> queue = new ArrayDeque<>();
    openParents.forEach(
        (id, parents) -> {
          pending.put(id, parents.size());
          if (parents.isEmpty()) {
            queue.add(id);
          }
        });
    List<Change.Id> order = new ArrayList<>();
    while (!queue.isEmpty()) {
      Change.Id id = queue.poll();
      order.add(id);
      for (Change.Id child : children.get(id)) {
        if (pending.merge(child, -1, Integer::sum) == 0) {
          queue.add(child);
        }
      }
    }
    return order;
  }
}
