/** Shared Film Studio project state. Legacy gesture code binds to this store instead of owning duplicate context. */
export interface FilmStudioPendingTransaction {
  kind: string;
  baseRevision: string;
  operations: readonly unknown[];
  operationCount: number;
}

export interface FilmStudioStoreSnapshot {
  projectId: string;
  film: unknown | null;
  revision: string;
  selectedId: string;
  selectedIds: string[];
  selectedIdsVersion: number;
  selectionAnchor: string;
  playhead: number;
  duration: number;
  pendingExternalRevision: string;
  insertMode: boolean;
  overwriteMode: boolean;
  snapEnabled: boolean;
  envelopeMode: boolean;
  slideMode: boolean;
  slipMode: boolean;
  busy: boolean;
  broken: boolean;
  pendingTransaction: FilmStudioPendingTransaction | null;
}

export interface FilmStudioStoreBridge {
  getSnapshot: () => FilmStudioStoreSnapshot;
  subscribe: (listener: () => void) => () => void;
  subscribeField<K extends keyof FilmStudioStoreSnapshot>(key: K, listener: () => void): () => void;
  getField<K extends keyof FilmStudioStoreSnapshot>(key: K): FilmStudioStoreSnapshot[K];
  bindLegacyState: (target: Record<string, unknown>) => void;
  dispatchTransaction<T>(
    context: FilmStudioPendingTransaction,
    submitter: () => Promise<T> | T,
  ): Promise<T>;
}

const asString = (value: unknown): string =>
  typeof value === "string" ? value : value == null ? "" : String(value);

const asFiniteNumber = (value: unknown, fallback: number): number => {
  const parsed = typeof value === "number" ? value : Number(value);
  return Number.isFinite(parsed) ? parsed : fallback;
};

const asPendingTransaction = (value: unknown): FilmStudioPendingTransaction | null => {
  if (!value || typeof value !== "object") return null;
  const raw = value as Record<string, unknown>;
  const operations = Array.isArray(raw.operations) ? raw.operations.slice() : [];
  const count = Number(raw.operationCount);
  return {
    kind: asString(raw.kind) || "unknown",
    baseRevision: asString(raw.baseRevision),
    operations,
    operationCount: Number.isFinite(count) && count >= 0 ? count : operations.length,
  };
};

type FilmTransactionErrorCode =
  | "FILM_TRANSACTION_BUSY"
  | "FILM_TRANSACTION_CONTEXT_INVALID"
  | "FILM_EXTERNAL_REVISION_PENDING"
  | "FILM_REVISION_CONFLICT";

const filmTransactionError = (code: FilmTransactionErrorCode): Error & {code: FilmTransactionErrorCode} => {
  const error = new Error(code) as Error & {code: FilmTransactionErrorCode};
  error.code = code;
  return error;
};

class FilmStore implements FilmStudioStoreBridge {
  private readonly listeners = new Set<() => void>();
  private readonly fieldListeners = new Map<keyof FilmStudioStoreSnapshot, Set<() => void>>();
  private readonly selectedIdsTarget: string[] = [];
  private selectedIdsProxy: string[];
  private mutingSelectedIds = false;
  private boundLegacyTarget: Record<string, unknown> | null = null;
  private snapshot: FilmStudioStoreSnapshot;

  constructor() {
    this.selectedIdsProxy = new Proxy(this.selectedIdsTarget, {
      set: (target, property, value) => {
        const changed = Reflect.set(target, property, value, target);
        if (changed && !this.mutingSelectedIds) {
          this.publish({...this.snapshot, selectedIds: this.selectedIdsProxy,
            selectedIdsVersion: this.snapshot.selectedIdsVersion + 1});
        }
        return changed;
      },
      deleteProperty: (target, property) => {
        const changed = Reflect.deleteProperty(target, property);
        if (changed && !this.mutingSelectedIds) {
          this.publish({...this.snapshot, selectedIds: this.selectedIdsProxy,
            selectedIdsVersion: this.snapshot.selectedIdsVersion + 1});
        }
        return changed;
      },
    });
    this.snapshot = {
      projectId: "",
      film: null,
      revision: "",
      selectedId: "",
      selectedIds: this.selectedIdsProxy,
      selectedIdsVersion: 0,
      selectionAnchor: "",
      playhead: 0,
      duration: 10,
      pendingExternalRevision: "",
      insertMode: false,
      overwriteMode: false,
      snapEnabled: true,
      envelopeMode: false,
      slideMode: false,
      slipMode: false,
      busy: false,
      broken: false,
      pendingTransaction: null,
    };
  }

  getSnapshot = (): FilmStudioStoreSnapshot => this.snapshot;

  subscribe = (listener: () => void): (() => void) => {
    this.listeners.add(listener);
    return () => this.listeners.delete(listener);
  };

  subscribeField<K extends keyof FilmStudioStoreSnapshot>(key: K, listener: () => void): () => void {
    let listeners = this.fieldListeners.get(key);
    if (!listeners) {
      listeners = new Set<() => void>();
      this.fieldListeners.set(key, listeners);
    }
    listeners.add(listener);
    return () => {
      const current = this.fieldListeners.get(key);
      if (!current) return;
      current.delete(listener);
      if (!current.size) this.fieldListeners.delete(key);
    };
  }

  getField<K extends keyof FilmStudioStoreSnapshot>(key: K): FilmStudioStoreSnapshot[K] {
    return this.snapshot[key];
  }

  dispatchTransaction<T>(
    context: FilmStudioPendingTransaction,
    submitter: () => Promise<T> | T,
  ): Promise<T> {
    const pending = asPendingTransaction(context);
    if (!pending || !pending.kind || !pending.baseRevision) {
      return Promise.reject(filmTransactionError("FILM_TRANSACTION_CONTEXT_INVALID"));
    }
    if (this.snapshot.busy) {
      return Promise.reject(filmTransactionError("FILM_TRANSACTION_BUSY"));
    }
    if (this.snapshot.pendingExternalRevision) {
      return Promise.reject(filmTransactionError("FILM_EXTERNAL_REVISION_PENDING"));
    }
    if (pending.baseRevision !== this.snapshot.revision) {
      return Promise.reject(filmTransactionError("FILM_REVISION_CONFLICT"));
    }
    this.publish({...this.snapshot, busy: true, pendingTransaction: pending});
    return Promise.resolve()
      .then(submitter)
      .finally(() => {
        this.publish({...this.snapshot, busy: false, pendingTransaction: null});
      });
  }

  private publish(snapshot: FilmStudioStoreSnapshot): void {
    const previous = this.snapshot;
    this.snapshot = snapshot;
    for (const listener of Array.from(this.listeners)) listener();
    for (const [key, listeners] of Array.from(this.fieldListeners.entries())) {
      if (!Object.is(previous[key], snapshot[key])) {
        for (const listener of Array.from(listeners)) listener();
      }
    }
  }

  private replaceSelectedIds(value: unknown, shouldPublish = true): void {
    const values = Array.isArray(value)
      ? value.filter((item): item is string => typeof item === "string")
      : [];
    this.mutingSelectedIds = true;
    this.selectedIdsTarget.splice(0, this.selectedIdsTarget.length, ...values);
    this.mutingSelectedIds = false;
    if (shouldPublish) {
      this.publish({...this.snapshot, selectedIds: this.selectedIdsProxy,
        selectedIdsVersion: this.snapshot.selectedIdsVersion + 1});
    }
  }

  private setValue(key: keyof FilmStudioStoreSnapshot, value: unknown): void {
    if (key === "selectedIds") {
      this.replaceSelectedIds(value);
      return;
    }
    let normalized: unknown = value;
    if (key === "projectId" || key === "revision" || key === "selectedId" ||
        key === "selectionAnchor" || key === "pendingExternalRevision") {
      normalized = asString(value);
    } else if (key === "busy" || key === "broken" ||
        key === "insertMode" || key === "overwriteMode" || key === "snapEnabled" ||
        key === "envelopeMode" || key === "slideMode" || key === "slipMode") {
      normalized = value === true;
    } else if (key === "playhead" || key === "duration") {
      const parsed = asFiniteNumber(value, key === "duration" ? 10 : 0);
      normalized = key === "duration" ? Math.max(0.001, parsed) : Math.max(0, parsed);
    } else if (key === "pendingTransaction") {
      normalized = asPendingTransaction(value);
    } else if (key === "film") {
      normalized = value ?? null;
      if (normalized && typeof normalized === "object" &&
          typeof (normalized as Record<string, unknown>).rev === "string") {
        const revision = (normalized as Record<string, unknown>).rev as string;
        if (revision !== this.snapshot.revision) {
          this.publish({...this.snapshot, film: normalized, revision});
          return;
        }
      }
    }
    if (Object.is(this.snapshot[key], normalized)) return;
    this.publish({...this.snapshot, [key]: normalized} as FilmStudioStoreSnapshot);
  }

  bindLegacyState(target: Record<string, unknown>): void {
    if (this.boundLegacyTarget === target) return;
    this.replaceSelectedIds(target.selectedIds, false);
    const film = target.data ?? null;
    const filmRevision = film && typeof film === "object" &&
      typeof (film as Record<string, unknown>).rev === "string"
      ? (film as Record<string, unknown>).rev as string
      : asString(target.rev);

    this.snapshot = {
      projectId: asString(target.projectId),
      film,
      revision: filmRevision,
      selectedId: asString(target.selectedId),
      selectedIds: this.selectedIdsProxy,
      selectedIdsVersion: this.snapshot.selectedIdsVersion,
      selectionAnchor: asString(target.selectionAnchor),
      playhead: asFiniteNumber(target.playhead, 0),
      duration: Math.max(0.001, asFiniteNumber(target.duration, 10)),
      pendingExternalRevision: asString(target.pendingExternalRevision),
      insertMode: target.insertMode === true,
      overwriteMode: target.overwriteMode === true,
      snapEnabled: target.snapEnabled !== false,
      envelopeMode: target.envelopeMode === true,
      slideMode: target.slideMode === true,
      slipMode: target.slipMode === true,
      busy: target.busy === true,
      broken: target.broken === true,
      pendingTransaction: asPendingTransaction(target.pendingTransaction),
    };
    this.boundLegacyTarget = target;
    this.publish(this.snapshot);

    const bindings: Array<[string, keyof FilmStudioStoreSnapshot]> = [
      ["projectId", "projectId"],
      ["data", "film"],
      ["rev", "revision"],
      ["selectedId", "selectedId"],
      ["selectedIds", "selectedIds"],
      ["selectionAnchor", "selectionAnchor"],
      ["playhead", "playhead"],
      ["duration", "duration"],
      ["pendingExternalRevision", "pendingExternalRevision"],
      ["insertMode", "insertMode"],
      ["overwriteMode", "overwriteMode"],
      ["snapEnabled", "snapEnabled"],
      ["envelopeMode", "envelopeMode"],
      ["slideMode", "slideMode"],
      ["slipMode", "slipMode"],
      ["busy", "busy"],
      ["broken", "broken"],
      ["pendingTransaction", "pendingTransaction"],
    ];
    for (const [legacyKey, storeKey] of bindings) {
      Object.defineProperty(target, legacyKey, {
        configurable: true,
        enumerable: true,
        get: () => this.snapshot[storeKey],
        set: (nextValue: unknown) => this.setValue(storeKey, nextValue),
      });
    }
  }
}

export const filmStore = new FilmStore();
window.filmStudioStore = filmStore;
