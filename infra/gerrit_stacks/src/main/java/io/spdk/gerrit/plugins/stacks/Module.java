package io.spdk.gerrit.plugins.stacks;

import com.google.gerrit.extensions.annotations.Exports;
import com.google.gerrit.server.query.change.ChangeQueryBuilder.ChangeOperatorFactory;
import com.google.inject.AbstractModule;

/** Plugin entry point. */
public class Module extends AbstractModule {
  @Override
  protected void configure() {
    bind(ChangeOperatorFactory.class)
        .annotatedWith(Exports.named(StackOperator.EXPORT))
        .to(StackOperator.class);
  }
}
