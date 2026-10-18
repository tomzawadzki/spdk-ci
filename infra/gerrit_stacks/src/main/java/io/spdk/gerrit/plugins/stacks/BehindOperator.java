package io.spdk.gerrit.plugins.stacks;

import com.google.gerrit.entities.BranchNameKey;
import com.google.gerrit.entities.PatchSet;
import com.google.gerrit.exceptions.StorageException;
import com.google.gerrit.index.query.PostFilterPredicate;
import com.google.gerrit.index.query.Predicate;
import com.google.gerrit.index.query.QueryParseException;
import com.google.gerrit.server.git.GitRepositoryManager;
import com.google.gerrit.server.query.change.ChangeData;
import com.google.gerrit.server.query.change.ChangeQueryBuilder;
import com.google.gerrit.server.query.change.ChangeQueryBuilder.ChangeOperatorFactory;
import com.google.inject.Inject;
import java.io.IOException;
import java.time.Duration;
import java.time.Instant;
import java.util.HashMap;
import java.util.Map;
import java.util.Optional;
import java.util.regex.Matcher;
import java.util.regex.Pattern;
import org.eclipse.jgit.lib.Ref;
import org.eclipse.jgit.lib.Repository;
import org.eclipse.jgit.revwalk.RevWalk;

/**
 * Creates {@code behind_stacks:<N>w}, the open changes whose current patch set was created more
 * than N weeks before the commit time of the tip of their branch.
 */
final class BehindOperator implements ChangeOperatorFactory {
  /** Gerrit exports the operator as {@code <export>_<plugin>}. */
  static final String EXPORT = "behind";

  private static final String NAME = EXPORT + "_stacks";

  private static final Pattern WEEKS = Pattern.compile("([1-9][0-9]{0,3})w");

  private final GitRepositoryManager repoManager;

  @Inject
  BehindOperator(GitRepositoryManager repoManager) {
    this.repoManager = repoManager;
  }

  @Override
  public Predicate<ChangeData> create(ChangeQueryBuilder builder, String value)
      throws QueryParseException {
    SearchOnly.check(builder, NAME);
    Matcher m = WEEKS.matcher(value);
    if (!m.matches()) {
      throw new QueryParseException(
          String.format("%s does not accept %s, use a number of weeks such as 4w", NAME, value));
    }
    Duration behind = Duration.ofDays(7L * Integer.parseInt(m.group(1)));
    // A post filter alone makes Gerrit scan open and closed changes.
    return Predicate.and(builder.statusOpen(), new BehindPredicate(value, behind, repoManager));
  }

  /** Matches the changes behind the tip of their branch. Reads each branch tip once. */
  private static final class BehindPredicate extends PostFilterPredicate<ChangeData> {
    private final Duration behind;
    private final GitRepositoryManager repoManager;
    private final Map<BranchNameKey, Optional<Instant>> tips = new HashMap<>();

    BehindPredicate(String value, Duration behind, GitRepositoryManager repoManager) {
      super(NAME, value);
      this.behind = behind;
      this.repoManager = repoManager;
    }

    @Override
    public boolean match(ChangeData cd) {
      PatchSet ps = cd.currentPatchSet();
      Optional<Instant> tip = tip(cd.change().getDest());
      return ps != null && tip.isPresent() && ps.createdOn().plus(behind).isBefore(tip.get());
    }

    private synchronized Optional<Instant> tip(BranchNameKey branch) {
      return tips.computeIfAbsent(branch, this::readTip);
    }

    private Optional<Instant> readTip(BranchNameKey branch) {
      try (Repository repo = repoManager.openRepository(branch.project());
          RevWalk rw = new RevWalk(repo)) {
        Ref ref = repo.exactRef(branch.branch());
        if (ref == null) {
          return Optional.empty();
        }
        return Optional.of(
            Instant.ofEpochSecond(rw.parseCommit(ref.getObjectId()).getCommitTime()));
      } catch (IOException e) {
        throw new StorageException("Cannot read " + branch, e);
      }
    }

    @Override
    public int getCost() {
      // Cheaper than stack_stacks:, it reads one ref per branch.
      return 2;
    }
  }
}
