/**
 * Plugin entry point: wiring only.
 *
 * The plugin registers two read-only panes, a settings tab, and two commands. It holds no
 * database handle of its own — it never opens SQLite — and it exposes no write path, so
 * nothing here can change what people-context stores.
 */

import { Plugin, type WorkspaceLeaf } from "obsidian";

import { PeopleContextClient } from "./client.js";
import { DEFAULT_SETTINGS, type PeopleContextSettings, normalizeSettings } from "./settings.js";
import { PeopleContextSettingTab } from "./settings-tab.js";
import {
  PEOPLE_INDEX_VIEW,
  PERSON_BRIEF_VIEW,
  PeopleIndexView,
  PersonBriefView,
  type ViewHost,
} from "./views.js";

export default class PeopleContextPlugin extends Plugin implements ViewHost {
  override settings: PeopleContextSettings = { ...DEFAULT_SETTINGS };
  client!: PeopleContextClient;

  override async onload(): Promise<void> {
    this.settings = normalizeSettings(await this.loadData());
    this.client = new PeopleContextClient({
      // Read through a closure so a settings change takes effect on the next read without
      // rebuilding the client or the open panes.
      settings: () => this.settings,
      env: process.env,
    });

    this.registerView(PEOPLE_INDEX_VIEW, (leaf: WorkspaceLeaf) => new PeopleIndexView(leaf, this));
    this.registerView(PERSON_BRIEF_VIEW, (leaf: WorkspaceLeaf) => new PersonBriefView(leaf, this));

    this.addRibbonIcon("users", "Open people-context", () => {
      void this.openIndex();
    });

    this.addCommand({
      id: "open-people-index",
      name: "Open the people index",
      callback: () => {
        void this.openIndex();
      },
    });

    this.addCommand({
      id: "refresh-people-context",
      name: "Refresh people-context panes",
      callback: () => {
        void this.refreshAll();
      },
    });

    this.addSettingTab(new PeopleContextSettingTab(this));
  }

  override onunload(): void {
    // Obsidian detaches the registered views; the panes cancel their own in-flight reads.
  }

  /** Persist a settings change, normalizing whatever the tab produced. */
  async updateSettings(change: Partial<PeopleContextSettings>): Promise<void> {
    this.settings = normalizeSettings({ ...this.settings, ...change });
    await this.saveData(this.settings);
  }

  refreshOnOpen(): boolean {
    return this.settings.refreshPolicy === "on-open";
  }

  /** Reveal the index pane in the right sidebar, creating it if needed. */
  async openIndex(): Promise<void> {
    const leaf = await this.revealLeaf(PEOPLE_INDEX_VIEW);
    if (leaf !== null) {
      this.app.workspace.revealLeaf(leaf);
    }
  }

  /** Show one person in the brief pane, reusing the existing pane when there is one. */
  async showPerson(personId: string): Promise<void> {
    const leaf = await this.revealLeaf(PERSON_BRIEF_VIEW);
    if (leaf === null) {
      return;
    }
    const view = leaf.view;
    if (view instanceof PersonBriefView) {
      await view.showPerson(personId);
    }
    this.app.workspace.revealLeaf(leaf);
  }

  /** Re-read every open pane. */
  async refreshAll(): Promise<void> {
    const leaves = [
      ...this.app.workspace.getLeavesOfType(PEOPLE_INDEX_VIEW),
      ...this.app.workspace.getLeavesOfType(PERSON_BRIEF_VIEW),
    ];
    for (const leaf of leaves) {
      const view = leaf.view;
      if (view instanceof PeopleIndexView || view instanceof PersonBriefView) {
        await view.refresh();
      }
    }
  }

  private async revealLeaf(viewType: string): Promise<WorkspaceLeaf | null> {
    const existing = this.app.workspace.getLeavesOfType(viewType);
    const first = existing[0];
    if (first !== undefined) {
      return first;
    }
    // The index lives in the sidebar; a brief is a document and belongs in the main area.
    const leaf =
      viewType === PEOPLE_INDEX_VIEW
        ? this.app.workspace.getRightLeaf(false)
        : this.app.workspace.getLeaf("tab");
    if (leaf === null) {
      return null;
    }
    await leaf.setViewState({ type: viewType, active: true });
    return leaf;
  }
}
