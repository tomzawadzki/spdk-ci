package io.spdk.gerrit.plugins.stacks;

import com.google.common.flogger.FluentLogger;
import com.google.gerrit.extensions.annotations.PluginName;
import com.google.gerrit.extensions.client.MenuItem;
import com.google.gerrit.extensions.webui.TopMenu;
import com.google.gerrit.server.config.AllProjectsName;
import com.google.gerrit.server.config.PluginConfigFactory;
import com.google.gerrit.server.project.NoSuchProjectException;
import com.google.inject.Inject;
import java.util.ArrayList;
import java.util.List;
import java.util.concurrent.TimeUnit;
import org.eclipse.jgit.errors.ConfigInvalidException;
import org.eclipse.jgit.lib.Config;

/**
 * The menus of the menu bar, read from stacks.config on refs/meta/config of All-Projects, see
 * config.md. Gerrit reads the file through the project cache on every request, so a submitted
 * change to it shows without a reload. An item with a mistake is left out and logged.
 */
final class ConfigMenu implements TopMenu {
  private static final FluentLogger logger = FluentLogger.forEnclosingClass();

  private final PluginConfigFactory configs;
  private final AllProjectsName allProjects;
  private final String pluginName;

  @Inject
  ConfigMenu(
      PluginConfigFactory configs, AllProjectsName allProjects, @PluginName String pluginName) {
    this.configs = configs;
    this.allProjects = allProjects;
    this.pluginName = pluginName;
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
    List<MenuEntry> menus = new ArrayList<>();
    List<String> errors = new ArrayList<>();
    for (String menu : cfg.getSubsections("menu")) {
      List<MenuItem> items = new ArrayList<>();
      for (String name : cfg.getStringList("menu", menu, "item")) {
        try {
          items.add(item(cfg, name));
        } catch (ConfigInvalidException e) {
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

  private static MenuItem item(Config cfg, String name) throws ConfigInvalidException {
    if (!cfg.getSubsections("link").contains(name)) {
      throw new ConfigInvalidException("no link named " + name);
    }
    String url = cfg.getString("link", name, "url");
    if (url == null) {
      throw new ConfigInvalidException("link " + name + " has no url");
    }
    return new MenuItem(name, url, cfg.getString("link", name, "target"));
  }
}
