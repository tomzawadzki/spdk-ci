package io.spdk.gerrit.plugins.stacks;

import static com.google.common.truth.Truth.assertThat;
import static com.google.common.truth.Truth.assertWithMessage;
import static java.util.stream.Collectors.toList;

import com.google.gerrit.acceptance.GitUtil;
import com.google.gerrit.acceptance.LightweightPluginDaemonTest;
import com.google.gerrit.acceptance.PushOneCommit;
import com.google.gerrit.acceptance.TestPlugin;
import com.google.gerrit.entities.RefNames;
import com.google.gerrit.extensions.api.changes.ReviewInput;
import com.google.gerrit.extensions.client.MenuItem;
import com.google.gerrit.extensions.webui.TopMenu;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.Arrays;
import java.util.List;
import java.util.Optional;
import org.eclipse.jgit.internal.storage.dfs.InMemoryRepository;
import org.eclipse.jgit.junit.TestRepository;
import org.eclipse.jgit.lib.Config;
import org.junit.Test;

@TestPlugin(name = "stacks", sysModule = "io.spdk.gerrit.plugins.stacks.Module")
public class ConfigMenuIT extends LightweightPluginDaemonTest {
  @Test
  public void noFileNoMenus() throws Exception {
    assertThat(gApi.config().server().topMenus()).isEmpty();
  }

  @Test
  public void links() throws Exception {
    setConfig(
        String.join(
            "\n",
            "[menu \"Review\"]",
            "  item = Queue",
            "[menu \"Other\"]",
            "  item = Docs",
            "[link \"Queue\"]",
            "  url = /queue_status.html",
            "[link \"Docs\"]",
            "  url = https://spdk.io/doc/",
            "  target = _blank"));

    List<TopMenu.MenuEntry> menus = gApi.config().server().topMenus();
    assertThat(menus.stream().map(m -> m.name).collect(toList()))
        .containsExactly("Review", "Other")
        .inOrder();
    MenuItem queue = menus.get(0).items.get(0);
    assertThat(queue.url).isEqualTo("/queue_status.html");
    assertThat(queue.target).isNull();
    assertThat(menus.get(1).items.get(0).target).isEqualTo("_blank");
  }

  @Test
  public void mistakesLeaveOutTheItem() throws Exception {
    setConfig(
        String.join(
            "\n",
            "[menu \"Review\"]",
            "  item = Missing",
            "  item = No url",
            "  item = Good",
            "[menu \"Empty\"]",
            "  item = Missing",
            "[link \"No url\"]",
            "  target = _blank",
            "[link \"Good\"]",
            "  url = /good"));

    List<TopMenu.MenuEntry> menus = gApi.config().server().topMenus();
    assertThat(menus.stream().map(m -> m.name).collect(toList())).containsExactly("Review");
    assertThat(itemNames(menus, "Review")).containsExactly("Good");
  }

  @Test
  public void submittedChangeUpdatesTheMenus() throws Exception {
    submitConfig("[menu \"A\"]\n  item = L\n[link \"L\"]\n  url = /a\n");
    assertThat(itemNames(gApi.config().server().topMenus(), "A")).containsExactly("L");
    submitConfig("[menu \"B\"]\n  item = L\n[link \"L\"]\n  url = /b\n");
    assertThat(gApi.config().server().topMenus().stream().map(m -> m.name).collect(toList()))
        .containsExactly("B");
  }

  /** The file that review.spdk.io runs: every menu and item shows up, in order. */
  @Test
  public void spdkConfig() throws Exception {
    String file = Files.readString(Path.of("stacks.config"));
    setConfig(file);
    Config cfg = new Config();
    cfg.fromText(file);

    List<TopMenu.MenuEntry> menus = gApi.config().server().topMenus();
    assertThat(menus.stream().map(m -> m.name).collect(toList()))
        .containsExactlyElementsIn(cfg.getSubsections("menu"))
        .inOrder();
    for (TopMenu.MenuEntry menu : menus) {
      assertWithMessage(menu.name)
          .that(menu.items.stream().map(i -> i.name).collect(toList()))
          .containsExactlyElementsIn(Arrays.asList(cfg.getStringList("menu", menu.name, "item")))
          .inOrder();
    }
  }

  private static List<String> itemNames(List<TopMenu.MenuEntry> menus, String menu) {
    Optional<TopMenu.MenuEntry> m = menus.stream().filter(e -> e.name.equals(menu)).findFirst();
    return m.map(e -> e.items.stream().map(i -> i.name).collect(toList())).orElse(List.of());
  }

  /** Replaces stacks.config on refs/meta/config of All-Projects with a direct push. */
  private void setConfig(String content) throws Exception {
    pushConfig(content, RefNames.REFS_CONFIG).assertOkStatus();
  }

  /** Replaces stacks.config through a change for refs/meta/config, the way it is changed. */
  private void submitConfig(String content) throws Exception {
    PushOneCommit.Result r = pushConfig(content, "refs/for/" + RefNames.REFS_CONFIG);
    r.assertOkStatus();
    gApi.changes().id(r.getChangeId()).current().review(ReviewInput.approve());
    gApi.changes().id(r.getChangeId()).current().submit();
  }

  private PushOneCommit.Result pushConfig(String content, String ref) throws Exception {
    TestRepository<InMemoryRepository> repo = cloneProject(allProjects, admin);
    GitUtil.fetch(repo, RefNames.REFS_CONFIG + ":config");
    repo.reset("config");
    return pushFactory
        .create(admin.newIdent(), repo, "Set stacks.config", "stacks.config", content)
        .to(ref);
  }
}
