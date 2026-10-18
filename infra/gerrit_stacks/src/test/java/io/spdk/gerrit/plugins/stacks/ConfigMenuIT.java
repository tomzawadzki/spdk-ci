package io.spdk.gerrit.plugins.stacks;

import static com.google.common.truth.Truth.assertThat;
import static com.google.common.truth.Truth.assertWithMessage;
import static java.util.stream.Collectors.toList;

import com.google.gerrit.acceptance.GitUtil;
import com.google.gerrit.acceptance.LightweightPluginDaemonTest;
import com.google.gerrit.acceptance.PushOneCommit;
import com.google.gerrit.acceptance.RestResponse;
import com.google.gerrit.acceptance.TestPlugin;
import com.google.gerrit.acceptance.testsuite.request.RequestScopeOperations;
import com.google.gerrit.entities.RefNames;
import com.google.gerrit.extensions.api.changes.ReviewInput;
import com.google.gerrit.extensions.client.MenuItem;
import com.google.gerrit.extensions.webui.TopMenu;
import com.google.gerrit.testing.ConfigSuite;
import com.google.inject.Inject;
import java.net.URLDecoder;
import java.net.URLEncoder;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.List;
import java.util.Optional;
import org.eclipse.jgit.internal.storage.dfs.InMemoryRepository;
import org.eclipse.jgit.junit.TestRepository;
import org.eclipse.jgit.lib.Config;
import org.junit.Test;

@TestPlugin(name = "stacks", sysModule = "io.spdk.gerrit.plugins.stacks.Module")
public class ConfigMenuIT extends LightweightPluginDaemonTest {
  @Inject private RequestScopeOperations requestScopeOperations;

  @ConfigSuite.Default
  public static Config defaultConfig() {
    Config cfg = new Config();
    cfg.setString(
        "change", null, "mergeabilityComputationBehavior", "API_REF_UPDATED_AND_CHANGE_REINDEX");
    return cfg;
  }

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
  public void dashboards() throws Exception {
    setConfig(
        String.join(
            "\n",
            "[fragment]",
            "  rebase = (-is:mergeable OR ${broken})",
            "  broken = stack_stacks:broken",
            "[menu \"Review\"]",
            "  item = Mine",
            "  item = All",
            "  item = Queue",
            "[dashboard \"Mine\"]",
            "  signedIn = true",
            "  foreach = owner:self",
            "  section = Rebase now",
            "  query = -is:wip ${rebase}",
            "  section = Work in progress",
            "  query = is:wip",
            "[dashboard \"All\"]",
            "  section = Open",
            "  query = is:open",
            "[link \"Queue\"]",
            "  url = /queue_status.html"));

    List<TopMenu.MenuEntry> menus = gApi.config().server().topMenus();
    MenuItem mine = menus.get(0).items.get(0);
    assertThat(mine.url)
        .isEqualTo(
            "/dashboard/?title=Mine&foreach=status%3Aopen+%28owner%3Aself%29"
                + "&Rebase+now=%28-is%3Awip+%28-is%3Amergeable+OR+stack_stacks%3Abroken%29%29"
                + "&Work+in+progress=%28is%3Awip%29");
    assertThat(mine.target).isNull();
    assertThat(menus.get(0).items.get(1).url)
        .isEqualTo("/dashboard/?title=All&foreach=status%3Aopen&Open=%28is%3Aopen%29");

    requestScopeOperations.setApiUserAnonymous();
    assertThat(itemNames(gApi.config().server().topMenus(), "Review"))
        .containsExactly("All", "Queue")
        .inOrder();
  }

  @Test
  public void orStaysInsideForeachAndSection() throws Exception {
    int a = createChange().getChange().getId().get();
    int b = createChange().getChange().getId().get();
    int c = createChange().getChange().getId().get();
    setConfig(
        String.join(
            "\n",
            "[menu \"Review\"]",
            "  item = Or",
            "[dashboard \"Or\"]",
            String.format("  foreach = change:%d OR change:%d", a, b),
            "  section = S",
            String.format("  query = change:%d OR change:%d", b, c)));

    List<String> queries = sectionQueries(gApi.config().server().topMenus().get(0).items.get(0));
    assertThat(queries).hasSize(1);
    assertThat(
            gApi.changes().query(queries.get(0)).get().stream()
                .map(i -> i._number)
                .collect(toList()))
        .containsExactly(b);
  }

  @Test
  public void mistakesLeaveOutTheItem() throws Exception {
    setConfig(
        String.join(
            "\n",
            "[menu \"Review\"]",
            "  item = Missing",
            "  item = Unknown fragment",
            "  item = No query",
            "  item = No url",
            "  item = Loop",
            "  item = Bad signedIn",
            "  item = Good",
            "[menu \"Empty\"]",
            "  item = Missing",
            "[fragment]",
            "  loop = ${loop}",
            "[dashboard \"Unknown fragment\"]",
            "  section = S",
            "  query = ${nope}",
            "[dashboard \"No query\"]",
            "  section = S",
            "[dashboard \"Loop\"]",
            "  section = S",
            "  query = ${loop}",
            "[link \"No url\"]",
            "  target = _blank",
            "[link \"Bad signedIn\"]",
            "  url = /bad",
            "  signedIn = maybe",
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

  /**
   * The file that review.spdk.io runs: every menu and item shows up in order, and every section
   * works as a search, the way a dashboard sends it.
   */
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
      for (MenuItem item : menu.items) {
        if (item.url.startsWith("/dashboard/")) {
          assertSectionsWork(item);
        }
      }
    }
  }

  private void assertSectionsWork(MenuItem dashboard) throws Exception {
    for (String q : sectionQueries(dashboard)) {
      RestResponse r =
          adminRestSession.get("/changes/?q=" + URLEncoder.encode(q, StandardCharsets.UTF_8));
      assertWithMessage(dashboard.name + ", " + q + ": " + r.getEntityContent())
          .that(r.getStatusCode())
          .isEqualTo(200);
    }
  }

  /**
   * The search of each section, as the web UI makes it from the URL: it decodes the whole query
   * string, splits it into parameters, decodes each again, and joins foreach and a section query
   * with a space.
   */
  private static List<String> sectionQueries(MenuItem dashboard) {
    String query = dashboard.url.substring(dashboard.url.indexOf('?') + 1);
    String foreach = "";
    List<String> sections = new ArrayList<>();
    for (String param : decode(query).split("&")) {
      int eq = param.indexOf('=');
      String key = decode(eq < 0 ? param : param.substring(0, eq));
      String value = eq < 0 ? "" : decode(param.substring(eq + 1));
      if (key.equals("title")) {
        continue;
      } else if (key.equals("foreach")) {
        foreach = value + " ";
      } else if (!key.isEmpty() && !value.isEmpty()) {
        sections.add(value);
      }
    }
    String prefix = foreach;
    return sections.stream().map(s -> prefix + s).collect(toList());
  }

  private static String decode(String s) {
    return URLDecoder.decode(s, StandardCharsets.UTF_8);
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
