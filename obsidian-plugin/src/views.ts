/**
 * The two read-only panes.
 *
 * Both panes are painted with `createEl`/`setText`, never with `innerHTML`: names, summaries,
 * and notes are personal data of unknown shape, and they are inserted as text nodes so markup
 * inside them can never become markup on screen.
 *
 * Each pane owns one in-flight request. Starting a new one cancels the old one, and closing
 * the pane cancels whatever is running, so a slow CLI cannot paint a stale or closed view.
 */

import { ItemView, type ViewStateResult, type WorkspaceLeaf } from "obsidian";

import { PeopleContextCliError } from "./bridge.js";
import type { PeopleContextClient } from "./client.js";
import { DocumentFormatError } from "./documents.js";
import { type BriefView, IndexPaneModel, buildBriefView } from "./render.js";
import { isUsablePersonId } from "./settings.js";

export const PEOPLE_INDEX_VIEW = "people-context-index";
export const PERSON_BRIEF_VIEW = "people-context-brief";

/** What a pane needs from the plugin, kept narrow so the views do not reach into it. */
export interface ViewHost {
  readonly client: PeopleContextClient;
  /** Whether opening a pane should read immediately. */
  refreshOnOpen(): boolean;
  /** Open (or reuse) the brief pane for one person. */
  showPerson(personId: string): Promise<void>;
}

abstract class PeopleContextView extends ItemView {
  protected readonly host: ViewHost;
  private inFlight: AbortController | null = null;
  private released = false;

  constructor(leaf: WorkspaceLeaf, host: ViewHost) {
    super(leaf);
    this.host = host;
  }

  /**
   * Run one read, superseding any read still in flight for this pane.
   *
   * Named `runRead` rather than `load` because `Component.load` is already Obsidian's
   * lifecycle hook and must not be shadowed.
   */
  protected async runRead<T>(read: (signal: AbortSignal) => Promise<T>): Promise<T | null> {
    this.inFlight?.abort();
    const controller = new AbortController();
    this.inFlight = controller;
    try {
      return await read(controller.signal);
    } finally {
      if (this.inFlight === controller) {
        this.inFlight = null;
      }
    }
  }

  /**
   * Cut this pane loose from the plugin that created it.
   *
   * Called while the plugin unloads. A pane can outlive its plugin, and everything it would
   * use to read — the client, the settings behind it — belongs to the instance being torn
   * down. So the pane stops reading now and refuses to start again: a later read would use a
   * stale client and the settings of a plugin that no longer exists, which is worse than
   * showing nothing. A reload registers the view type again and builds a live pane.
   */
  releaseFromPlugin(): void {
    this.released = true;
    this.inFlight?.abort();
    this.inFlight = null;
    // Repaint now rather than waiting for something to ask. Every control already on screen
    // — a row, a Refresh button — carries a listener closed over the instance being torn
    // down, so a guard on each one would be a guard per control and easy to miss on the next.
    // Emptying the container removes the controls and their listeners together.
    this.paintReleased();
  }

  /** Abandon whatever this pane is reading, without releasing it. */
  cancelReads(): void {
    this.inFlight?.abort();
    this.inFlight = null;
  }

  /** True once the owning plugin has unloaded; the pane must not read again. */
  protected get isReleased(): boolean {
    return this.released;
  }

  /**
   * Whether this pane can still be driven.
   *
   * The plugin asks before reusing a leaf: a pane left behind by a previous load answers
   * `false` and is rebuilt through the newly registered factory rather than handed back inert.
   */
  isUsable(): boolean {
    return !this.released;
  }

  /** Paint the one thing a released pane can honestly say, replacing whatever was there. */
  protected paintReleased(): void {
    const container = this.containerEl.children[1] as HTMLElement;
    container.empty();
    container.createEl("p", {
      text:
        "This pane belongs to a previous load of the people-context plugin. " +
        "Open it again from the ribbon or the command palette to reconnect it.",
    });
  }

  override async onClose(): Promise<void> {
    this.cancelReads();
  }

  /** Paint a failure without ever showing a raw payload. */
  protected renderError(parent: HTMLElement, error: unknown): void {
    const box = parent.createDiv({ cls: "people-context-error" });
    box.createEl("p", { text: messageFor(error) });
    if (error instanceof PeopleContextCliError && error.hint !== undefined) {
      box.createEl("p", { cls: "people-context-hint", text: error.hint });
    }
  }
}

/** The browsable person index. */
export class PeopleIndexView extends PeopleContextView {
  private readonly model = new IndexPaneModel();
  private listEl: HTMLElement | null = null;
  private statusEl: HTMLElement | null = null;
  private errorEl: HTMLElement | null = null;
  private warningEl: HTMLElement | null = null;

  getViewType(): string {
    return PEOPLE_INDEX_VIEW;
  }

  getDisplayText(): string {
    return "People";
  }

  override getIcon(): string {
    return "users";
  }

  override async onOpen(): Promise<void> {
    this.paintShell();
    if (this.host.refreshOnOpen()) {
      await this.refresh();
    } else {
      this.paint();
    }
  }

  /** Re-read the index and repaint the pane. */
  async refresh(): Promise<void> {
    if (this.isReleased) {
      this.paintReleased();
      return;
    }
    this.model.beginRead();
    this.paint();
    try {
      const document = await this.runRead((signal) => this.host.client.listPeople(signal));
      if (document === null) {
        return;
      }
      this.model.setDocument(document);
    } catch (error) {
      if (isCancellation(error)) {
        // A superseded or closed read is not a failure of the pane, and its rejection can
        // land after the read that replaced it. Painting it would show a stale "cancelled".
        return;
      }
      this.model.setFailure(error);
    }
    this.paint();
  }

  private paintShell(): void {
    const container = this.containerEl.children[1] as HTMLElement;
    container.empty();
    container.addClass("people-context-index");
    container.createEl("h4", { text: "People" });

    const search = container.createEl("input", {
      cls: "people-context-search",
      attr: { type: "search", placeholder: "Filter people", "aria-label": "Filter people" },
    });
    search.addEventListener("input", () => {
      this.model.setQuery(search.value);
      // Filtering is a pure re-derivation from the document already in hand; it never
      // re-runs the CLI.
      this.paint();
    });

    const refresh = container.createEl("button", { text: "Refresh" });
    refresh.addEventListener("click", () => {
      void this.refresh();
    });

    this.warningEl = container.createDiv({ cls: "people-context-warning" });
    this.statusEl = container.createDiv({ cls: "people-context-status" });
    // A dedicated, always-emptied element, so failures replace each other instead of
    // accumulating and cannot outlive the read that fixed them.
    this.errorEl = container.createDiv({ cls: "people-context-failure" });
    this.listEl = container.createDiv({ cls: "people-context-list" });
  }

  /** Repaint every part of the pane from the model. */
  private paint(): void {
    this.statusEl?.setText(this.model.status());
    this.warningEl?.setText(this.model.compatibilityWarning() ?? "");

    const errorEl = this.errorEl;
    if (errorEl !== null) {
      errorEl.empty();
      const failure = this.model.error();
      if (failure !== null) {
        this.renderError(errorEl, failure);
      }
    }

    const list = this.listEl;
    if (list === null) {
      return;
    }
    list.empty();
    for (const row of this.model.rows()) {
      const item = list.createDiv({ cls: "people-context-row" });
      const button = item.createEl("button", { cls: "people-context-person" });
      // The label is display data; the click carries the stable id instead.
      button.createSpan({ cls: "people-context-name", text: row.title });
      if (row.isSelf) {
        button.createSpan({ cls: "people-context-badge", text: "you" });
      }
      if (row.subtitle !== "") {
        item.createDiv({ cls: "people-context-subtitle", text: row.subtitle });
      }
      button.addEventListener("click", () => {
        void this.host.showPerson(row.id);
      });
    }
  }
}

/** One person's read-only brief. */
export class PersonBriefView extends PeopleContextView {
  private personId: string | null = null;

  getViewType(): string {
    return PERSON_BRIEF_VIEW;
  }

  /**
   * A fixed label, deliberately not the selected person's name.
   *
   * The name is only known once the brief has been read, and this version of the workspace API
   * exposes no supported way to make an existing leaf re-read its display text afterwards. A
   * name assigned after the header was drawn would simply sit there stale — showing the
   * previous person while the pane shows the current one — which is worse than a generic label.
   * There is one brief pane and it is reused, so the person is identified in the pane itself.
   */
  getDisplayText(): string {
    return "Person brief";
  }

  override getIcon(): string {
    return "user";
  }

  override async onOpen(): Promise<void> {
    if (this.personId === null) {
      this.paintEmpty();
    } else if (!this.host.refreshOnOpen()) {
      this.paintDeferred();
    }
  }

  /**
   * Persist which person this pane is showing, so a restored tab is not empty.
   *
   * Only the opaque id is stored, and it is stored where the host keeps workspace layout —
   * inside the vault. That is a pointer to a person rather than any of their records, but it
   * is still vault content, which the plugin's privacy notes account for.
   */
  override getState(): Record<string, unknown> {
    const state = super.getState();
    return this.personId === null ? state : { ...state, personId: this.personId };
  }

  override async setState(state: unknown, result: ViewStateResult): Promise<void> {
    await super.setState(state, result);
    const restored = readPersonId(state);
    if (restored === null || restored === this.personId) {
      return;
    }
    this.personId = restored;
    if (this.host.refreshOnOpen()) {
      await this.refresh();
      return;
    }
    // Under the manual policy the restored id is kept but nothing is read. Restoring a
    // workspace must not start `pctx` on its own: that policy is exactly what a user picks to
    // stay in control of when the database is opened, created, or migrated.
    this.paintDeferred();
  }

  /** Point the pane at one person and read their brief. */
  async showPerson(personId: string): Promise<void> {
    if (!isUsablePersonId(personId)) {
      this.personId = null;
      const container = this.containerEl.children[1] as HTMLElement;
      container.empty();
      this.renderError(container, new Error("Refusing to open an unrecognized person id."));
      return;
    }
    this.personId = personId;
    // Record the choice in the workspace layout, so reopening Obsidian restores this person
    // rather than an empty pane.
    this.app.workspace.requestSaveLayout();
    await this.refresh();
  }

  /** Re-read the current person, if there is one. */
  async refresh(): Promise<void> {
    if (this.isReleased) {
      this.paintReleased();
      return;
    }
    const container = this.containerEl.children[1] as HTMLElement;
    const personId = this.personId;
    if (personId === null) {
      this.paintEmpty();
      return;
    }
    container.empty();
    container.addClass("people-context-brief");
    container.createDiv({ cls: "people-context-status", text: "Reading…" });
    try {
      const document = await this.runRead((signal) => this.host.client.getBrief(personId, signal));
      if (document === null) {
        return;
      }
      const view = buildBriefView(document);
      container.empty();
      this.paintBrief(container, view);
    } catch (error) {
      if (isCancellation(error)) {
        // Superseded by a newer selection, or the pane closed. The read that replaced this
        // one owns the pane now.
        return;
      }
      container.empty();
      this.renderError(container, error);
    }
  }

  private paintEmpty(): void {
    const container = this.containerEl.children[1] as HTMLElement;
    container.empty();
    container.addClass("people-context-brief");
    container.createEl("p", { text: "Select a person in the People pane." });
  }

  /**
   * Show that a person is restored but not yet read.
   *
   * The person is deliberately not named: their name is only known by reading, which is the
   * thing this state exists to avoid doing.
   */
  private paintDeferred(): void {
    const container = this.containerEl.children[1] as HTMLElement;
    container.empty();
    container.addClass("people-context-brief");
    container.createEl("p", {
      text:
        "This pane is set to a person, and refresh is set to manual. " +
        'Run "Refresh people-context panes" to read their brief.',
    });
  }

  private paintBrief(container: HTMLElement, view: BriefView): void {
    container.createEl("h2", { text: view.title });
    if (view.compatibilityWarning !== null) {
      container.createDiv({ cls: "people-context-warning", text: view.compatibilityWarning });
    }
    const header = container.createEl("ul", { cls: "people-context-header" });
    for (const line of view.headerLines) {
      header.createEl("li", { text: line });
    }
    container.createEl("blockquote", { cls: "people-context-notice", text: view.notice });
    for (const section of view.sections) {
      container.createEl("h3", { text: section.title });
      if (section.items.length === 0) {
        container.createEl("p", { cls: "people-context-empty", text: "None recorded." });
        continue;
      }
      const list = container.createEl("ul");
      for (const item of section.items) {
        list.createEl("li", { text: item });
      }
    }
  }
}

/** Read a usable person id out of restored workspace state, or `null`. */
function readPersonId(state: unknown): string | null {
  if (typeof state !== "object" || state === null) {
    return null;
  }
  const candidate = (state as { personId?: unknown }).personId;
  return isUsablePersonId(candidate) ? candidate : null;
}

/** Whether a rejection is a cancellation rather than something the user should see. */
function isCancellation(error: unknown): boolean {
  return error instanceof PeopleContextCliError && error.kind === "aborted";
}

/** The user-facing message for a failure, never including a returned payload. */
export function messageFor(error: unknown): string {
  if (error instanceof PeopleContextCliError || error instanceof DocumentFormatError) {
    return error.message;
  }
  if (error instanceof Error) {
    return error.message;
  }
  return "The people-context request failed.";
}
