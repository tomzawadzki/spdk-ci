package io.spdk.gerrit.plugins.stacks;

import com.google.common.flogger.FluentLogger;
import com.google.gerrit.extensions.annotations.PluginName;
import com.google.gerrit.extensions.client.MenuItem;
import com.google.gerrit.extensions.webui.TopMenu;
import com.google.gerrit.server.CurrentUser;
import com.google.gerrit.server.config.AllProjectsName;
import com.google.gerrit.server.config.PluginConfigFactory;
import com.google.gerrit.server.project.NoSuchProjectException;
import com.google.inject.Inject;
import com.google.inject.Provider;
import java.net.URLEncoder;
import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.HashSet;
import java.util.List;
import java.util.Set;
import java.util.concurrent.TimeUnit;
import java.util.regex.Matcher;
import java.util.regex.Pattern;
import org.eclipse.jgit.errors.ConfigInvalidException;
import org.eclipse.jgit.lib.Config;

/**
 * The menus of the menu bar, read from stacks.config on refs/meta/config of All-Projects, see
 * config.md. Gerrit reads the file through the project cache on every request, so a submitted
 * change to it shows without a reload. An item with a mistake is left out and logged.
 */
final class ConfigMenu implements TopMenu {
  private static final FluentLogger logger = FluentLogger.forEnclosingClass();

  private static final Pattern FRAGMENT = Pattern.compile("\\$\\{([^}]*)\\}");

  private final PluginConfigFactory configs;
  private final AllProjectsName allProjects;
  private final String pluginName;
  private final Provider<CurrentUser> user;

  @Inject
  ConfigMenu(
      PluginConfigFactory configs,
      AllProjectsName allProjects,
      @PluginName String pluginName,
      Provider<CurrentUser> user) {
    this.configs = configs;
    this.allProjects = allProjects;
    this.pluginName = pluginName;
    this.user = user;
  }

  @Override
  public List<MenuEntry> getEntries() {
    Config cfg;
    try {
      cfg = configs.getProjectPluginConfig(allProjects, pluginName);
    } catch (NoSuchProjectException e) {
      logger.atWarning().atMostEvery(1, TimeUnit.MINUTES).withCause(e).log("No %s", allProjects);
      return List.of();
    }
    // A section with a self term fails for anonymous users, and blanks the whole dashboard.
    boolean signedIn = user.get().isIdentifiedUser();
    List<MenuEntry> menus = new ArrayList<>();
    List<String> errors = new ArrayList<>();
    for (String menu : cfg.getSubsections("menu")) {
      List<MenuItem> items = new ArrayList<>();
      for (String name : cfg.getStringList("menu", menu, "item")) {
        try {
          MenuItem item = item(cfg, name, signedIn);
          if (item != null) {
            items.add(item);
          }
        } catch (ConfigInvalidException | IllegalArgumentException e) {
          errors.add("menu " + menu + ": " + e.getMessage());
        }
      }
      if (!items.isEmpty()) {
        menus.add(new MenuEntry(menu, items));
      }
    }
    if (!errors.isEmpty()) {
      // Every request reads the file again, so a mistake would log on each page load.
      logger.atWarning().atMostEvery(1, TimeUnit.MINUTES).log(
          "%s.config of %s leaves out items: %s", pluginName, allProjects, errors);
    }
    return menus;
  }

  /** The dashboard or link of that name, or null if the caller does not get it. */
  private static MenuItem item(Config cfg, String name, boolean signedIn)
      throws ConfigInvalidException {
    boolean dashboard = cfg.getSubsections("dashboard").contains(name);
    boolean link = cfg.getSubsections("link").contains(name);
    if (dashboard == link) {
      throw new ConfigInvalidException(
          (dashboard ? "both a dashboard and a link are named " : "no dashboard or link named ")
              + name);
    }
    if (cfg.getBoolean(dashboard ? "dashboard" : "link", name, "signedIn", false) && !signedIn) {
      return null;
    }
    return dashboard ? dashboard(cfg, name) : link(cfg, name);
  }

  private static MenuItem link(Config cfg, String name) throws ConfigInvalidException {
    String url = cfg.getString("link", name, "url");
    if (url == null) {
      throw new ConfigInvalidException("link " + name + " has no url");
    }
    return new MenuItem(name, url, cfg.getString("link", name, "target"));
  }

  /** A custom dashboard, which opens in the same tab. */
  private static MenuItem dashboard(Config cfg, String name) throws ConfigInvalidException {
    String[] titles = cfg.getStringList("dashboard", name, "section");
    String[] queries = cfg.getStringList("dashboard", name, "query");
    if (titles.length != queries.length) {
      throw new ConfigInvalidException(
          String.format(
              "dashboard %s has %d sections and %d queries", name, titles.length, queries.length));
    }
    String foreach = cfg.getString("dashboard", name, "foreach");
    // The web UI joins foreach and a section query with a space, so each goes in parentheses to
    // keep an OR to its own terms.
    StringBuilder url =
        new StringBuilder("/dashboard/?title=")
            .append(encode(name))
            .append("&foreach=")
            .append(
                encode("status:open" + (foreach == null ? "" : " (" + expand(cfg, foreach) + ")")));
    for (int i = 0; i < titles.length; i++) {
      url.append('&')
          .append(encode(titles[i]))
          .append('=')
          .append(encode("(" + expand(cfg, queries[i]) + ")"));
    }
    return new MenuItem(name, url.toString(), null);
  }

  /** Replaces each ${name} with that key of the fragment section, which can use others. */
  private static String expand(Config cfg, String value) throws ConfigInvalidException {
    return expand(cfg, value, new HashSet<>());
  }

  private static String expand(Config cfg, String value, Set<String> using)
      throws ConfigInvalidException {
    Matcher m = FRAGMENT.matcher(value);
    StringBuilder out = new StringBuilder();
    while (m.find()) {
      String name = m.group(1);
      String fragment = cfg.getString("fragment", null, name);
      if (fragment == null) {
        throw new ConfigInvalidException("no fragment named " + name);
      }
      if (!using.add(name)) {
        throw new ConfigInvalidException("fragment " + name + " uses itself");
      }
      m.appendReplacement(out, Matcher.quoteReplacement(expand(cfg, fragment, using)));
      using.remove(name);
    }
    return m.appendTail(out).toString();
  }

  private static String encode(String s) {
    return URLEncoder.encode(s, StandardCharsets.UTF_8);
  }
}
