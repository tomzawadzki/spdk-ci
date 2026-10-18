package io.spdk.gerrit.plugins.stacks;

import static com.google.common.truth.Truth.assertThat;
import static com.google.common.truth.Truth.assertWithMessage;
import static com.google.gerrit.testing.GerritJUnit.assertThrows;
import static java.util.stream.Collectors.toSet;

import com.google.gerrit.acceptance.LightweightPluginDaemonTest;
import com.google.gerrit.acceptance.RestResponse;
import com.google.gerrit.acceptance.TestAccount;
import com.google.gerrit.acceptance.TestPlugin;
import com.google.gerrit.acceptance.testsuite.change.ChangeOperations;
import com.google.gerrit.acceptance.testsuite.request.RequestScopeOperations;
import com.google.gerrit.entities.Address;
import com.google.gerrit.extensions.api.changes.ChangeIdentifier;
import com.google.gerrit.extensions.api.changes.ReviewInput;
import com.google.gerrit.extensions.api.projects.ConfigInput;
import com.google.gerrit.extensions.client.SubmitType;
import com.google.gerrit.extensions.common.SubmitRequirementInput;
import com.google.gerrit.extensions.restapi.BadRequestException;
import com.google.gerrit.testing.ConfigSuite;
import com.google.inject.Inject;
import java.net.URLEncoder;
import java.nio.charset.StandardCharsets;
import java.util.Arrays;
import java.util.Collections;
import java.util.Set;
import org.eclipse.jgit.lib.Config;
import org.junit.Test;

/** The graphs of about.md, each in a project of its own. "+" marks a change that passes. */
@TestPlugin(name = "stacks", sysModule = "io.spdk.gerrit.plugins.stacks.Module")
public class StackOperatorIT extends LightweightPluginDaemonTest {
  @Inject private ChangeOperations changeOperations;
  @Inject private RequestScopeOperations requestScopeOperations;

  @ConfigSuite.Default
  public static Config defaultConfig() {
    Config cfg = new Config();
    // Without it is:mergeable, and so every stack_stacks: search, fails.
    cfg.setString(
        "change", null, "mergeabilityComputationBehavior", "API_REF_UPDATED_AND_CHANGE_REINDEX");
    return cfg;
  }

  @Test
  public void linear() throws Exception {
    // A+ <- B+ <- C <- D+
    ChangeIdentifier a = passing(change());
    ChangeIdentifier b = passing(childOf(a));
    ChangeIdentifier c = childOf(b);
    ChangeIdentifier d = passing(childOf(c));

    assertStack("leaf", d);
    assertStack("ready", a, b);
    assertStack("submit", b);
    assertStack("blocked", c);
    assertStack("broken");
  }

  @Test
  public void fork() throws Exception {
    // A+ <- B+, with C and D+ both on B
    ChangeIdentifier a = passing(change());
    ChangeIdentifier b = passing(childOf(a));
    ChangeIdentifier c = childOf(b);
    ChangeIdentifier d = passing(childOf(b));

    assertStack("leaf", c, d);
    assertStack("ready", a, b, d);
    assertStack("submit", d);
    assertStack("blocked", c);
  }

  @Test
  public void firstChangeDoesNotPass() throws Exception {
    // A <- B+
    ChangeIdentifier a = change();
    passing(childOf(a));

    assertStack("ready");
    assertStack("submit");
    assertStack("blocked", a);
  }

  @Test
  public void single() throws Exception {
    ChangeIdentifier a = passing(change());

    assertStack("leaf", a);
    assertStack("ready", a);
    assertStack("submit", a);
  }

  @Test
  public void workInProgressDoesNotPass() throws Exception {
    ChangeIdentifier a = passing(change());
    gApi.changes().id(a).setWorkInProgress();

    assertStack("ready");
    assertStack("blocked", a);
  }

  @Test
  public void merge() throws Exception {
    // M+ merging X+ and Y
    ChangeIdentifier x = passing(change());
    ChangeIdentifier y = change();
    ChangeIdentifier m =
        passing(
            changeOperations
                .newChange()
                .project(project)
                .owner(admin.id())
                .mergeOf()
                .change(x)
                .and()
                .change(y)
                .create());

    assertStack("leaf", m);
    assertStack("ready", x);
    assertStack("submit", x);
    assertStack("blocked", y);
  }

  @Test
  public void parentOnOldPatchSet() throws Exception {
    // A+ <- B, B on an old patch set of A
    ChangeIdentifier a = change();
    ChangeIdentifier b = passing(childOf(a));
    changeOperations.change(a).newPatchset().create();
    passing(a);

    assertStack("ready", a);
    assertStack("submit", a);
    assertStack("blocked", b);
    assertStack("broken", b);
  }

  @Test
  public void abandonedParent() throws Exception {
    ChangeIdentifier a = change();
    ChangeIdentifier b = passing(childOf(a));
    gApi.changes().id(a).abandon();

    assertStack("leaf", b);
    assertStack("ready");
    assertStack("blocked", b);
    assertStack("broken", b);
  }

  @Test
  public void parentMergedAndRebased() throws Exception {
    // Submit rebases A, and B stays on the old patch set of A, which is merged now.
    ConfigInput in = new ConfigInput();
    in.submitType = SubmitType.REBASE_ALWAYS;
    gApi.projects().name(project.get()).config(in);
    ChangeIdentifier a = passing(change());
    ChangeIdentifier b = passing(childOf(a));
    gApi.changes().id(a).current().submit();

    assertStack("ready", b);
    assertStack("submit", b);
    assertStack("broken");
  }

  @Test
  public void invisibleParent() throws Exception {
    ChangeIdentifier a = passing(change());
    ChangeIdentifier b = passing(childOf(a));
    gApi.changes().id(a).setPrivate(true);

    assertStack("ready", a, b);
    requestScopeOperations.setApiUser(user.id());
    assertStack("blocked", b);
    assertStack("broken", b);
  }

  @Test
  public void otherTermsFilterOnlyTheResult() throws Exception {
    ChangeIdentifier a = passing(change());
    ChangeIdentifier b = passing(childOf(a));

    assertThat(numbers("stack_stacks:submit change:" + number(a))).isEmpty();
    assertThat(numbers("stack_stacks:submit change:" + number(b))).containsExactly(number(b));
  }

  @Test
  public void unknownValue() throws Exception {
    BadRequestException e =
        assertThrows(BadRequestException.class, () -> numbers("stack_stacks:Ready"));
    assertThat(e)
        .hasMessageThat()
        .contains(
            "stack_stacks does not accept Ready, use one of leaf, ready, submit, blocked,"
                + " broken");
  }

  @Test
  public void limitOfUsesPerRequest() throws Exception {
    // A dashboard sends each section as its own REST request.
    restQuery(StackOperator.MAX_USES).assertOK();
    RestResponse r = restQuery(StackOperator.MAX_USES + 1);
    r.assertBadRequest();
    assertThat(r.getEntityContent())
        .contains("stack_stacks can be used at most 6 times per request");
  }

  private RestResponse restQuery(int uses) throws Exception {
    String q = String.join(" OR ", Collections.nCopies(uses, "stack_stacks:leaf"));
    return adminRestSession.get("/changes/?q=" + URLEncoder.encode(q, StandardCharsets.UTF_8));
  }

  @Test
  public void refusedInSubmitRequirement() throws Exception {
    SubmitRequirementInput in = new SubmitRequirementInput();
    in.name = "Stacks";
    in.submittabilityExpression = "stack_stacks:ready";
    BadRequestException e =
        assertThrows(
            BadRequestException.class,
            () -> gApi.projects().name(project.get()).submitRequirement("Stacks").create(in));
    assertThat(e)
        .hasMessageThat()
        .contains("stack_stacks cannot be used in submit requirements or watch filters");
  }

  @Test
  public void refusedInWatchFilter() throws Exception {
    requestScopeOperations.setApiUser(user.id());
    watch(project.get());
    TestAccount other = accountCreator.user2();
    requestScopeOperations.setApiUser(other.id());
    watch(project.get(), "-stack_stacks:ready");
    requestScopeOperations.setApiUser(admin.id());
    sender.clear();

    createChange();

    Set<String> notified =
        sender.getMessages().stream()
            .flatMap(m -> m.rcpt().stream())
            .map(Address::email)
            .collect(toSet());
    assertThat(notified).contains(user.email());
    assertThat(notified).doesNotContain(other.email());
  }

  private ChangeIdentifier change() throws Exception {
    return changeOperations.newChange().project(project).owner(admin.id()).create();
  }

  private ChangeIdentifier childOf(ChangeIdentifier parent) throws Exception {
    return changeOperations
        .newChange()
        .project(project)
        .owner(admin.id())
        .childOf()
        .change(parent)
        .create();
  }

  /** Gives the change the vote it needs to be submittable. */
  private ChangeIdentifier passing(ChangeIdentifier change) throws Exception {
    gApi.changes().id(change).current().review(ReviewInput.approve());
    return change;
  }

  private int number(ChangeIdentifier change) {
    return changeOperations.change(change).get().numericChangeId().get();
  }

  private void assertStack(String value, ChangeIdentifier... want) throws Exception {
    assertWithMessage(value)
        .that(numbers("stack_stacks:" + value))
        .containsExactlyElementsIn(Arrays.stream(want).map(this::number).collect(toSet()));
  }

  private Set<Integer> numbers(String q) throws Exception {
    return gApi.changes().query("project:" + project.get() + " (" + q + ")").get().stream()
        .map(c -> c._number)
        .collect(toSet());
  }
}
