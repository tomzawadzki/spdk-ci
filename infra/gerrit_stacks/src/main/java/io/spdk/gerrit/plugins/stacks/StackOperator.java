package io.spdk.gerrit.plugins.stacks;

import com.google.gerrit.entities.BranchNameKey;
import com.google.gerrit.index.query.PostFilterPredicate;
import com.google.gerrit.index.query.Predicate;
import com.google.gerrit.index.query.QueryParseException;
import com.google.gerrit.server.cache.PerThreadCache;
import com.google.gerrit.server.query.change.ChangeData;
import com.google.gerrit.server.query.change.ChangeQueryBuilder;
import com.google.gerrit.server.query.change.ChangeQueryBuilder.ChangeOperatorFactory;
import com.google.inject.Inject;
import java.util.Arrays;
import java.util.HashMap;
import java.util.Locale;
import java.util.Map;
import java.util.concurrent.atomic.AtomicInteger;
import java.util.stream.Collectors;

/** Creates {@code stack_stacks:<value>}, which matches a change by its place in its series. */
final class StackOperator implements ChangeOperatorFactory {
  /** Gerrit exports the operator as {@code <export>_<plugin>}. */
  static final String EXPORT = "stack";

  private static final String NAME = EXPORT + "_stacks";

  /** The places a change can have in its series. A change can have several. */
  enum Value {
    /** No open change has it as a parent, the last change of a series. */
    LEAF,
    /** Meets {@link #CRITERIA}, has no broken link, and all its open parents are ready. */
    READY,
    /** Ready, and no child is ready. Submitting it submits its open ancestors as well. */
    SUBMIT,
    /** Not ready, while all its open parents are ready. */
    BLOCKED,
    /** Has a parent that is neither an open change the caller can see nor merged. */
    BROKEN;

    String word() {
      return name().toLowerCase(Locale.ROOT);
    }
  }

  /**
   * What a change needs on its own to be ready. Gerrit refuses to submit a work in progress change
   * even when it is submittable, and with REBASE_ALWAYS is:mergeable tests the change together with
   * its open ancestors.
   */
  static final String CRITERIA = "status:open is:submittable is:mergeable -is:wip";

  /**
   * Most uses in one request. Each OR branch that holds the operator scans all open changes, and
   * many of them exhaust the heap.
   */
  static final int MAX_USES = 6;

  private final BranchLoader loader;

  @Inject
  StackOperator(BranchLoader loader) {
    this.loader = loader;
  }

  @Override
  public Predicate<ChangeData> create(ChangeQueryBuilder builder, String value)
      throws QueryParseException {
    SearchOnly.check(builder, NAME);
    Value v = parse(value);
    // Outside a request PerThreadCache gives a new counter each time, so internal callers are not
    // limited.
    AtomicInteger uses =
        PerThreadCache.getOrCompute(
            PerThreadCache.Key.create(AtomicInteger.class, StackOperator.class),
            AtomicInteger::new);
    if (uses.incrementAndGet() > MAX_USES) {
      throw new QueryParseException(
          String.format("%s can be used at most %d times per request", NAME, MAX_USES));
    }
    // A post filter alone makes Gerrit scan open and closed changes.
    return Predicate.and(
        builder.statusOpen(),
        new StackPredicate(v, loader, builder.isVisible(), builder.parse(CRITERIA)));
  }

  private static Value parse(String value) throws QueryParseException {
    for (Value v : Value.values()) {
      if (v.word().equals(value)) {
        return v;
      }
    }
    throw new QueryParseException(
        String.format(
            "%s does not accept %s, use one of %s",
            NAME,
            value,
            Arrays.stream(Value.values()).map(Value::word).collect(Collectors.joining(", "))));
  }

  /** Matches the changes of one {@link Value} in the branch of the change. */
  private static final class StackPredicate extends PostFilterPredicate<ChangeData> {
    private final Value value;
    private final BranchLoader loader;
    private final Predicate<ChangeData> visible;
    private final Predicate<ChangeData> criteria;
    private final Map<BranchNameKey, BranchLoader.Graph> graphs = new HashMap<>();

    StackPredicate(
        Value value,
        BranchLoader loader,
        Predicate<ChangeData> visible,
        Predicate<ChangeData> criteria) {
      super(NAME, value.word());
      this.value = value;
      this.loader = loader;
      this.visible = visible;
      this.criteria = criteria;
    }

    @Override
    public boolean match(ChangeData cd) {
      return graph(cd.change().getDest()).has(value, cd.getId());
    }

    /**
     * Builds the graph of a branch once per predicate. Within a request, the other stack predicates
     * of the same caller share it through Gerrit's per request cache.
     */
    private synchronized BranchLoader.Graph graph(BranchNameKey branch) {
      return graphs.computeIfAbsent(
          branch,
          b ->
              PerThreadCache.getOrCompute(
                  PerThreadCache.Key.create(BranchLoader.Graph.class, b, visible),
                  () -> loader.load(visible, criteria, b)));
    }

    @Override
    public int getCost() {
      // Gerrit runs the cheaper post filters of an AND first, and this one may build a graph.
      return 10;
    }
  }
}
