package io.spdk.gerrit.plugins.stacks;

import com.google.gerrit.index.query.QueryParseException;
import com.google.gerrit.server.mail.send.ProjectWatch;
import com.google.gerrit.server.query.change.ChangeQueryBuilder;
import com.google.gerrit.server.query.change.SubmitRequirementChangeQueryBuilder;

/** Keeps the operators of this plugin to searches. */
final class SearchOnly {
  private SearchOnly() {}

  /**
   * Refuses submit requirements and watch filters. Gerrit evaluates them while indexing or sending
   * email, where the answer goes stale as other changes and the branch move, nothing is shared
   * between changes, and an error fails the indexing or the whole email.
   */
  static void check(ChangeQueryBuilder builder, String name) throws QueryParseException {
    if (builder instanceof SubmitRequirementChangeQueryBuilder
        || builder instanceof ProjectWatch.WatcherChangeQueryBuilder) {
      throw new QueryParseException(
          name + " cannot be used in submit requirements or watch filters");
    }
  }
}
