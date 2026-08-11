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

import { ItemView, type WorkspaceLeaf } from "obsidian";

import { PeopleContextCliError } from "./bridge.js";
import type { PeopleContextClient } from "./client.js";
import { DocumentFormatError } from "./documents.js";
import { type BriefView, type IndexRow, buildBriefView, buildIndexRows } from "./render.js";
import { isSafePersonId } from "./settings.js";

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

  protected cancelInFlight(): void {
    this.inFlight?.abort();
    this.inFlight = null;
  }

  override async onClose(): Promise<void> {
    this.cancelInFlight();
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
  private query = "";
  private rows: IndexRow[] = [];
  private listEl: HTMLElement | null = null;
  private statusEl: HTMLElement | null = null;

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
      this.setStatus("Select Refresh to read the people-context database.");
    }
  }

  /** Re-read the index and repaint the list. */
  async refresh(): Promise<void> {
    this.setStatus("Reading…");
    try {
      const document = await this.runRead((signal) => this.host.client.listPeople(signal));
      if (document === null) {
        return;
      }
      this.rows = buildIndexRows(document, this.query);
      this.paintRows();
      this.setStatus(
        document.people.length === 0
          ? "No people recorded yet."
          : `${this.rows.length} of ${document.people.length} shown.`,
      );
    } catch (error) {
      this.rows = [];
      this.listEl?.empty();
      this.setStatus("");
      const container = this.containerEl.children[1] as HTMLElement;
      this.renderError(container, error);
    }
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
      this.query = search.value;
      this.paintRows();
    });

    const refresh = container.createEl("button", { text: "Refresh" });
    refresh.addEventListener("click", () => {
      void this.refresh();
    });

    this.statusEl = container.createDiv({ cls: "people-context-status" });
    this.listEl = container.createDiv({ cls: "people-context-list" });
  }

  private paintRows(): void {
    const list = this.listEl;
    if (list === null) {
      return;
    }
    list.empty();
    for (const row of this.rows) {
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

  private setStatus(text: string): void {
    this.statusEl?.setText(text);
  }
}

/** One person's read-only brief. */
export class PersonBriefView extends PeopleContextView {
  private personId: string | null = null;
  private title = "Person";

  getViewType(): string {
    return PERSON_BRIEF_VIEW;
  }

  getDisplayText(): string {
    return this.title;
  }

  override getIcon(): string {
    return "user";
  }

  override async onOpen(): Promise<void> {
    if (this.personId === null) {
      this.paintEmpty();
    }
  }

  /** Point the pane at one person and read their brief. */
  async showPerson(personId: string): Promise<void> {
    if (!isSafePersonId(personId)) {
      this.personId = null;
      const container = this.containerEl.children[1] as HTMLElement;
      container.empty();
      this.renderError(container, new Error("Refusing to open an unrecognized person id."));
      return;
    }
    this.personId = personId;
    await this.refresh();
  }

  /** Re-read the current person, if there is one. */
  async refresh(): Promise<void> {
    const personId = this.personId;
    const container = this.containerEl.children[1] as HTMLElement;
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
      this.title = view.title;
      container.empty();
      this.paintBrief(container, view);
    } catch (error) {
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
