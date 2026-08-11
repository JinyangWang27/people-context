/**
 * The settings tab.
 *
 * Every control here maps to one typed field. There is deliberately no "extra arguments" box
 * and no command-line field: a free-form string would be the one place a user could turn a
 * bounded, shell-free invocation back into an arbitrary command.
 *
 * There is likewise no field for the database key. An encrypted database is opened with the
 * key the Obsidian process already carries in its environment, so the plugin has nothing to
 * store, prompt for, or write to `data.json`.
 */

import { PluginSettingTab, Setting } from "obsidian";

import type PeopleContextPlugin from "./main.js";
import { DB_KEY_ENV, isRefreshPolicy } from "./settings.js";

export class PeopleContextSettingTab extends PluginSettingTab {
  private readonly plugin: PeopleContextPlugin;

  constructor(plugin: PeopleContextPlugin) {
    super(plugin.app, plugin);
    this.plugin = plugin;
  }

  override display(): void {
    const { containerEl } = this;
    containerEl.empty();

    new Setting(containerEl)
      .setName("people-context executable")
      .setDesc("Path to the pctx command, or a bare name resolved through PATH.")
      .addText((text) =>
        text
          .setPlaceholder("pctx")
          .setValue(this.plugin.settings.executablePath)
          .onChange(async (value) => {
            await this.plugin.updateSettings({ executablePath: value.trim() || "pctx" });
          }),
      );

    new Setting(containerEl)
      .setName("Database path")
      .setDesc("Optional explicit database file. Leave empty to let the CLI resolve it.")
      .addText((text) =>
        text
          .setPlaceholder("(default resolution)")
          .setValue(this.plugin.settings.databasePath)
          .onChange(async (value) => {
            await this.plugin.updateSettings({ databasePath: value.trim() });
          }),
      );

    new Setting(containerEl)
      .setName("Encrypted database")
      .setDesc(
        `Open the database with SQLCipher. The key is read from the ${DB_KEY_ENV} variable ` +
          "Obsidian was started with; the plugin never stores or prompts for it, and never " +
          "falls back to plaintext.",
      )
      .addToggle((toggle) =>
        toggle.setValue(this.plugin.settings.encryptedDatabase).onChange(async (value) => {
          await this.plugin.updateSettings({ encryptedDatabase: value });
        }),
      );

    new Setting(containerEl)
      .setName("Refresh")
      .setDesc("Whether opening a pane reads the database immediately.")
      .addDropdown((dropdown) =>
        dropdown
          .addOption("on-open", "When a pane opens")
          .addOption("manual", "Only when I ask")
          .setValue(this.plugin.settings.refreshPolicy)
          .onChange(async (value) => {
            if (isRefreshPolicy(value)) {
              await this.plugin.updateSettings({ refreshPolicy: value });
            }
          }),
      );

    containerEl.createEl("p", {
      cls: "people-context-privacy-note",
      text:
        "This plugin is read-only and never requests sensitive-disclosure records. Anything it " +
        "renders is still personal data: if this vault is synchronized, that content leaves the " +
        "local-first perimeter people-context maintains.",
    });
  }
}
