package io.spdk.gerrit.plugins.stacks;

import static com.google.common.truth.Truth.assertThat;
import static com.google.gerrit.testing.GerritJUnit.assertThrows;
import static java.util.stream.Collectors.toSet;

import com.google.gerrit.acceptance.LightweightPluginDaemonTest;
import com.google.gerrit.acceptance.PushOneCommit;
import com.google.gerrit.acceptance.TestPlugin;
import com.google.gerrit.extensions.common.SubmitRequirementInput;
import com.google.gerrit.extensions.restapi.BadRequestException;
import com.google.gerrit.testing.TestTimeUtil;
import java.sql.Timestamp;
import java.time.Duration;
import java.time.Instant;
import java.util.Set;
import java.util.concurrent.TimeUnit;
import org.eclipse.jgit.junit.TestRepository;
import org.eclipse.jgit.lib.PersonIdent;
import org.eclipse.jgit.lib.Repository;
import org.junit.After;
import org.junit.Before;
import org.junit.Test;

@TestPlugin(name = "stacks", sysModule = "io.spdk.gerrit.plugins.stacks.Module")
public class BehindOperatorIT extends LightweightPluginDaemonTest {
  /** Every change is uploaded at this time, the clock stands still. */
  private static final Instant UPLOAD = TestTimeUtil.START;

  @Before
  public void freezeClock() {
    TestTimeUtil.resetWithClockStep(0, TimeUnit.SECONDS);
  }

  @After
  public void systemClock() {
    TestTimeUtil.useSystemTime();
  }

  @Test
  public void weeksBeforeTheBranchTip() throws Exception {
    PushOneCommit.Result r = createChange();
    int num = r.getChange().getId().get();
    moveTip(UPLOAD.plus(Duration.ofDays(14)));

    // Exactly N weeks is not behind, a second more is.
    assertThat(numbers("behind_stacks:2w")).isEmpty();
    moveTip(UPLOAD.plus(Duration.ofDays(14).plusSeconds(1)));
    assertThat(numbers("behind_stacks:2w")).containsExactly(num);
    assertThat(numbers("behind_stacks:1w")).containsExactly(num);
    assertThat(numbers("behind_stacks:3w")).isEmpty();
    assertThat(numbers("behind_stacks:1w -behind_stacks:3w")).containsExactly(num);
  }

  @Test
  public void branchTipNotCurrentTime() throws Exception {
    createChange();
    moveTip(UPLOAD);
    TestTimeUtil.setClock(Timestamp.from(UPLOAD.plus(Duration.ofDays(70))));

    assertThat(numbers("behind_stacks:1w")).isEmpty();
  }

  @Test
  public void closedChangesDoNotMatch() throws Exception {
    PushOneCommit.Result r = createChange();
    gApi.changes().id(r.getChangeId()).abandon();
    moveTip(UPLOAD.plus(Duration.ofDays(70)));

    assertThat(numbers("behind_stacks:1w")).isEmpty();
  }

  @Test
  public void notWeeks() throws Exception {
    BadRequestException e =
        assertThrows(BadRequestException.class, () -> numbers("behind_stacks:14d"));
    assertThat(e)
        .hasMessageThat()
        .contains("behind_stacks does not accept 14d, use a number of weeks such as 4w");
    assertThrows(BadRequestException.class, () -> numbers("behind_stacks:0w"));
  }

  @Test
  public void refusedInSubmitRequirement() throws Exception {
    SubmitRequirementInput in = new SubmitRequirementInput();
    in.name = "Behind";
    in.submittabilityExpression = "-behind_stacks:4w";
    BadRequestException e =
        assertThrows(
            BadRequestException.class,
            () -> gApi.projects().name(project.get()).submitRequirement("Behind").create(in));
    assertThat(e)
        .hasMessageThat()
        .contains("behind_stacks cannot be used in submit requirements or watch filters");
  }

  /** Adds a commit with that commit time to master, without a change. */
  private void moveTip(Instant when) throws Exception {
    try (Repository repo = repoManager.openRepository(project);
        TestRepository<Repository> tr = new TestRepository<>(repo)) {
      PersonIdent ident = new PersonIdent(admin.newIdent(), when);
      tr.branch("refs/heads/master").commit().author(ident).committer(ident).create();
    }
  }

  private Set<Integer> numbers(String q) throws Exception {
    return gApi.changes().query("project:" + project.get() + " " + q).get().stream()
        .map(c -> c._number)
        .collect(toSet());
  }
}
