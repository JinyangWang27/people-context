/**
 * Plugin entry point: wiring only.
 *
 * The plugin registers two read-only panes, a settings tab, and two commands. It holds no
 * database handle of its own — it never opens SQLite — and it exposes no write path, so
 * nothing here can change what people-context stores.
 */

import { Plugin, type View, type WorkspaceLeaf } from "obsidian";

import { PeopleContextClient } from "./client.js";
import { SerialQueue } from "./serial.js";
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
  /** Serializes persistence so overlapping keystroke-driven saves cannot land out of order. */
  private readonly saves = new SerialQueue();

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
    // Release every pane. A registered pane can outlive the plugin that created it, and its
    // `onClose` does not run while it survives, so this both stops a read that would otherwise
    // hold a `pctx` process until its own timeout, and stops the pane starting another one
    // later with this instance's client and settings after the instance is gone.
    //
    // The panes themselves are deliberately left attached. Detaching them would remove them
    // from the user's workspace on every plugin update or reload, and that is a change to
    // their layout rather than cleanup.
    for (const view of this.ownedViews()) {
      view.releaseFromPlugin();
    }
  }

  /**
   * Persist a settings change, normalizing whatever the tab produced.
   *
   * The host fires a text field's `onChange` per keystroke without awaiting the previous call,
   * so several of these can be in flight at once. Writes are queued rather than issued
   * concurrently: otherwise an earlier one could finish last and leave the file holding a value
   * the user has already typed past.
   */
  async updateSettings(change: Partial<PeopleContextSettings>): Promise<void> {
    this.settings = normalizeSettings({ ...this.settings, ...change });
    const snapshot = this.settings;
    await this.saves.run(async () => {
      await this.saveData(snapshot);
    });
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

  /** Re-read every open pane, reconnecting any left behind by a previous load first. */
  async refreshAll(): Promise<void> {
    for (const view of await this.reconnectedViews()) {
      await view.refresh();
    }
  }

  /**
   * Whether a pane belongs to this plugin instance and can still be driven.
   *
   * A pane can outlive the plugin that made it. Such a pane fails this check either because it
   * was released when its plugin unloaded, or because a reload re-evaluated the bundle and its
   * class is no longer the class this instance tests against.
   */
  private owns(view: View): view is PeopleIndexView | PersonBriefView {
    return (
      (view instanceof PeopleIndexView || view instanceof PersonBriefView) && view.isUsable()
    );
  }

  /** Rebuild one leaf if this instance cannot drive its view. */
  private async reconnect(leaf: WorkspaceLeaf, viewType: string): Promise<void> {
    if (!this.owns(leaf.view)) {
      await this.rebuild(leaf, viewType);
    }
  }

  /**
   * Rebuild a leaf left behind by a previous load, keeping what it was showing.
   *
   * Setting the type away and back is what forces the workspace to construct the view through
   * the factory this instance registered; re-applying the same type alone can reuse the view
   * object that is the problem. The serialized state is carried across, so a brief pane comes
   * back on the same person.
   */
  private async rebuild(leaf: WorkspaceLeaf, viewType: string): Promise<void> {
    const carried = leaf.getViewState();
    await leaf.setViewState({ type: "empty" });
    await leaf.setViewState({ ...carried, type: viewType, active: true });
  }

  /**
   * Every pane this instance can drive, reconnecting the ones it cannot.
   *
   * Every path that drives panes goes through here rather than reading the workspace directly.
   * A pane left behind by a previous load has to be repaired before it can be used, and doing
   * that in only some of those paths is what left the refresh command unable to reconnect
   * panes that the ribbon could.
   */
  private async reconnectedViews(): Promise<(PeopleIndexView | PersonBriefView)[]> {
    const views: (PeopleIndexView | PersonBriefView)[] = [];
    for (const viewType of [PEOPLE_INDEX_VIEW, PERSON_BRIEF_VIEW]) {
      for (const leaf of this.app.workspace.getLeavesOfType(viewType)) {
        await this.reconnect(leaf, viewType);
        // Re-read the view: a rebuilt leaf carries a different instance than the one checked.
        const view = leaf.view;
        if (this.owns(view)) {
          views.push(view);
        }
      }
    }
    return views;
  }

  /**
   * Panes belonging to this instance's classes, without rebuilding anything.
   *
   * Used while unloading, where the point is to release what this instance made rather than
   * to repair anything — and where rebuilding a pane would be actively wrong.
   */
  private ownedViews(): (PeopleIndexView | PersonBriefView)[] {
    const leaves = [
      ...this.app.workspace.getLeavesOfType(PEOPLE_INDEX_VIEW),
      ...this.app.workspace.getLeavesOfType(PERSON_BRIEF_VIEW),
    ];
    return leaves
      .map((leaf) => leaf.view)
      .filter(
        (view): view is PeopleIndexView | PersonBriefView =>
          view instanceof PeopleIndexView || view instanceof PersonBriefView,
      );
  }

  private async revealLeaf(viewType: string): Promise<WorkspaceLeaf | null> {
    const existing = this.app.workspace.getLeavesOfType(viewType);
    const first = existing[0];
    if (first !== undefined) {
      await this.reconnect(first, viewType);
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
