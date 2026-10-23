/** Shared Film Studio project state. Legacy gesture code binds to this store instead of owning duplicate context. */
export interface FilmStudioPendingTransaction {
  kind: string;
  baseRevision: string;
  operations: readonly unknown[];
  operationCount: number;
}

export type FilmStudioTimelineGesturePreviewGeometry = {
  left?: number;
  width?: number;
  mode?: "roll" | "slide" | "slip";
  deltaLabel?: string;
};
export type FilmStudioTimelineGesturePreviewEntry = number | FilmStudioTimelineGesturePreviewGeometry;
export type FilmStudioTimelineGesturePreview = Record<string, FilmStudioTimelineGesturePreviewEntry> | null;
export type FilmStudioTimelineSnapGuide = {at: number; kind: string; title: string} | null;
export type FilmStudioTimelineDropTarget = {trackId: string; invalid: boolean} | null;
export type FilmStudioTimelineActiveDragIds = string[] | null;
export type FilmStudioTimelineGestureMode = "move" | "trim-start" | "trim-end" | "roll" | "slide" | "slip";
export type FilmStudioTimelineGestureMember = {
  id: string;
  originalAt: number;
  originalEnd: number;
  trackId: string;
  trackIndex: number;
};
export type FilmStudioTimelineGestureContext = {
  id: string;
  mode: FilmStudioTimelineGestureMode;
  pointerId: number;
  startX: number;
  startY: number;
  startScrollLeft: number;
  active: boolean;
  members: FilmStudioTimelineGestureMember[];
  trackOrder: string[];
  originalAt: number | undefined;
  originalEnd: number | undefined;
  trackId: string | undefined;
  originalTrackIndex: number | undefined;
  targetTrackId: string | undefined;
  invalidTargetMessage: string | undefined;
  leftClipId: string | undefined;
  centerClipId: string | undefined;
  rightClipId: string | undefined;
  rightAt: number | undefined;
  centerAt: number | undefined;
  leftDuration: number | undefined;
  centerDuration: number | undefined;
  rightDuration: number | undefined;
};
export type FilmStudioTimelineEnvelopePoint = {t: number; v: number};
export type FilmStudioTimelineEnvelopePreview = {clipId: string; points: FilmStudioTimelineEnvelopePoint[]} | null;
export type FilmStudioTimelineWaveformSource = {duration: number; peaks: Uint8Array};
export type FilmStudioTimelineWaveformSources = Record<string, FilmStudioTimelineWaveformSource>;

export interface FilmStudioStoreSnapshot {
  projectId: string;
  film: unknown | null;
  revision: string;
  selectedId: string;
  selectedIds: string[];
  selectedIdsVersion: number;
  selectionAnchor: string;
  playhead: number;
  playing: boolean;
  duration: number;
  pixelsPerSecond: number;
  timelineGesturePreview: FilmStudioTimelineGesturePreview;
  timelineSnapGuide: FilmStudioTimelineSnapGuide;
  timelineDropTarget: FilmStudioTimelineDropTarget;
  timelineActiveDragIds: FilmStudioTimelineActiveDragIds;
  timelineGestureContext: FilmStudioTimelineGestureContext | null;
  timelineEnvelopePreview: FilmStudioTimelineEnvelopePreview;
  timelineWaveformSources: FilmStudioTimelineWaveformSources;
  pendingExternalRevision: string;
  insertMode: boolean;
  overwriteMode: boolean;
  snapEnabled: boolean;
  envelopeMode: boolean;
  slideMode: boolean;
  slipMode: boolean;
  rendering: boolean;
  busy: boolean;
  broken: boolean;
  pendingTransaction: FilmStudioPendingTransaction | null;
}

export interface FilmStudioStoreBridge {
  getSnapshot: () => FilmStudioStoreSnapshot;
  subscribe: (listener: () => void) => () => void;
  subscribeField<K extends keyof FilmStudioStoreSnapshot>(key: K, listener: () => void): () => void;
  getField<K extends keyof FilmStudioStoreSnapshot>(key: K): FilmStudioStoreSnapshot[K];
  setField<K extends keyof FilmStudioStoreSnapshot>(key: K, value: FilmStudioStoreSnapshot[K]): void;
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

const asOptionalFiniteNumber = (value: unknown): number | undefined => {
  if (value === undefined || value === null || value === "") return undefined;
  const parsed = typeof value === "number" ? value : Number(value);
  return Number.isFinite(parsed) ? parsed : undefined;
};

const asTimelineGestureContext = (value: unknown): FilmStudioTimelineGestureContext | null => {
  if (!value || typeof value !== "object" || Array.isArray(value)) return null;
  const raw = value as Record<string, unknown>;
  const modes: FilmStudioTimelineGestureMode[] = ["move", "trim-start", "trim-end", "roll", "slide", "slip"];
  const mode = typeof raw.mode === "string" && modes.includes(raw.mode as FilmStudioTimelineGestureMode)
    ? raw.mode as FilmStudioTimelineGestureMode : null;
  const id = asString(raw.id);
  const pointerId = asOptionalFiniteNumber(raw.pointerId);
  const startX = asOptionalFiniteNumber(raw.startX);
  const startY = asOptionalFiniteNumber(raw.startY);
  const startScrollLeft = asOptionalFiniteNumber(raw.startScrollLeft);
  const rawMembers = Array.isArray(raw.members) ? raw.members : null;
  const rawTrackOrder = Array.isArray(raw.trackOrder) ? raw.trackOrder : null;
  if (!id || !mode || pointerId === undefined || !Number.isInteger(pointerId) || pointerId < 0 ||
      startX === undefined || startY === undefined || startScrollLeft === undefined ||
      !rawMembers || !rawMembers.length || !rawTrackOrder || !rawTrackOrder.length ||
      rawTrackOrder.some(item => typeof item !== "string" || item.length === 0) ||
      new Set(rawTrackOrder).size !== rawTrackOrder.length) return null;

  const trackOrder = rawTrackOrder as string[];
  const members: FilmStudioTimelineGestureMember[] = [];
  const memberIds = new Set<string>();
  for (const item of rawMembers) {
    if (!item || typeof item !== "object" || Array.isArray(item)) return null;
    const member = item as Record<string, unknown>;
    const memberId = asString(member.id);
    const originalAt = asOptionalFiniteNumber(member.originalAt);
    const originalEnd = asOptionalFiniteNumber(member.originalEnd);
    const trackId = asString(member.trackId);
    const trackIndex = asOptionalFiniteNumber(member.trackIndex);
    if (!memberId || memberIds.has(memberId) ||
        originalAt === undefined || originalEnd === undefined || originalAt < 0 || originalEnd < originalAt ||
        !trackId || !Number.isInteger(trackIndex) || trackIndex === undefined || trackIndex < 0 ||
        trackOrder[trackIndex] !== trackId) return null;
    memberIds.add(memberId);
    members.push({id:memberId,originalAt,originalEnd,trackId,trackIndex});
  }
  if (!memberIds.has(id)) return null;

  const optionalString = (key: string): string | undefined => {
    const value = asString(raw[key]);
    return value || undefined;
  };
  const optionalNumber = (key: string): number | undefined => {
    const number = asOptionalFiniteNumber(raw[key]);
    return number;
  };
  const numberKeys = [
    "originalAt","originalEnd","originalTrackIndex","rightAt","centerAt",
    "leftDuration","centerDuration","rightDuration",
  ];
  if (numberKeys.some(key => raw[key] !== undefined && raw[key] !== null && optionalNumber(key) === undefined)) {
    return null;
  }
  const originalTrackIndex = optionalNumber("originalTrackIndex");
  if (originalTrackIndex !== undefined &&
      (!Number.isInteger(originalTrackIndex) || originalTrackIndex < 0 ||
       (optionalString("trackId") && trackOrder[originalTrackIndex] !== optionalString("trackId")))) {
    return null;
  }
  return {
    id, mode, pointerId,
    startX, startY, startScrollLeft, active: raw.active === true, members,
    trackOrder,
    originalAt: optionalNumber("originalAt"),
    originalEnd: optionalNumber("originalEnd"),
    trackId: optionalString("trackId"),
    originalTrackIndex,
    targetTrackId: optionalString("targetTrackId"),
    invalidTargetMessage: optionalString("invalidTargetMessage"),
    leftClipId: optionalString("leftClipId"),
    centerClipId: optionalString("centerClipId"),
    rightClipId: optionalString("rightClipId"),
    rightAt: optionalNumber("rightAt"),
    centerAt: optionalNumber("centerAt"),
    leftDuration: optionalNumber("leftDuration"),
    centerDuration: optionalNumber("centerDuration"),
    rightDuration: optionalNumber("rightDuration"),
  };
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
      playing: false,
      duration: 10,
      pixelsPerSecond: 36,
      timelineGesturePreview: null,
      timelineSnapGuide: null,
      timelineDropTarget: null,
      timelineActiveDragIds: null,
      timelineGestureContext: null,
      timelineEnvelopePreview: null,
      timelineWaveformSources: {},
      pendingExternalRevision: "",
      insertMode: false,
      overwriteMode: false,
      snapEnabled: true,
      envelopeMode: false,
      slideMode: false,
      slipMode: false,
      rendering: false,
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

  setField<K extends keyof FilmStudioStoreSnapshot>(key: K, value: FilmStudioStoreSnapshot[K]): void {
    this.setValue(key, value);
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
    } else if (key === "busy" || key === "broken" || key === "playing" ||
        key === "insertMode" || key === "overwriteMode" || key === "snapEnabled" ||
        key === "envelopeMode" || key === "slideMode" || key === "slipMode" || key === "rendering") {
      normalized = value === true;
    } else if (key === "pixelsPerSecond") {
      normalized = Math.max(12, Math.min(120, asFiniteNumber(value, 36)));
    } else if (key === "playhead" || key === "duration") {
      const parsed = asFiniteNumber(value, key === "duration" ? 10 : 0);
      normalized = key === "duration" ? Math.max(0.001, parsed) : Math.max(0, parsed);
    } else if (key === "pendingTransaction") {
      normalized = asPendingTransaction(value);
    } else if (key === "timelineGesturePreview") {
      if (!value || typeof value !== "object" || Array.isArray(value)) {
        normalized = null;
      } else {
        const entries: Record<string, FilmStudioTimelineGesturePreviewEntry> = {};
        for (const [clipId, rawEntry] of Object.entries(value as Record<string, unknown>)) {
          if (!clipId) continue;
          if (typeof rawEntry === "number" && Number.isFinite(rawEntry)) {
            entries[clipId] = rawEntry;
            continue;
          }
          if (!rawEntry || typeof rawEntry !== "object" || Array.isArray(rawEntry)) continue;
          const raw = rawEntry as Record<string, unknown>;
          const geometry: FilmStudioTimelineGesturePreviewGeometry = {};
          if (typeof raw.left === "number" && Number.isFinite(raw.left)) geometry.left = raw.left;
          if (typeof raw.width === "number" && Number.isFinite(raw.width) && raw.width > 0) geometry.width = raw.width;
          if (raw.mode === "roll" || raw.mode === "slide" || raw.mode === "slip") geometry.mode = raw.mode;
          if (typeof raw.deltaLabel === "string") geometry.deltaLabel = raw.deltaLabel;
          if (Object.keys(geometry).length) entries[clipId] = geometry;
        }
        normalized = Object.keys(entries).length ? entries : null;
      }
    } else if (key === "timelineSnapGuide") {
      if (!value || typeof value !== "object" || Array.isArray(value)) normalized = null;
      else {
        const raw = value as Record<string, unknown>;
        const at = Number(raw.at);
        normalized = Number.isFinite(at) && at >= 0 && typeof raw.kind === "string" && typeof raw.title === "string"
          ? {at, kind: raw.kind, title: raw.title}
          : null;
      }
    } else if (key === "timelineDropTarget") {
      if (!value || typeof value !== "object" || Array.isArray(value)) normalized = null;
      else {
        const raw = value as Record<string, unknown>;
        normalized = typeof raw.trackId === "string" && raw.trackId
          ? {trackId: raw.trackId, invalid: raw.invalid === true}
          : null;
      }
    } else if (key === "timelineActiveDragIds") {
      const ids = Array.isArray(value) ? value.filter((item): item is string => typeof item === "string" && item.length > 0) : [];
      normalized = ids.length ? Array.from(new Set(ids)) : null;
    } else if (key === "timelineGestureContext") {
      normalized = asTimelineGestureContext(value);
    } else if (key === "timelineEnvelopePreview") {
      if (!value || typeof value !== "object" || Array.isArray(value)) normalized = null;
      else {
        const raw = value as Record<string, unknown>;
        const clipId = typeof raw.clipId === "string" ? raw.clipId : "";
        const points = Array.isArray(raw.points)
          ? raw.points.filter((item): item is Record<string, unknown> => !!item && typeof item === "object" && !Array.isArray(item))
              .map(item => ({t:Number(item.t),v:Number(item.v)}))
              .filter(point => Number.isFinite(point.t) && point.t >= 0 && Number.isFinite(point.v))
              .map(point => ({t:point.t,v:Math.max(0,Math.min(1,point.v))}))
              .sort((a,b) => a.t - b.t)
          : [];
        normalized = clipId && points.length >= 2 ? {clipId,points} : null;
      }
    } else if (key === "timelineWaveformSources") {
      const sources: FilmStudioTimelineWaveformSources = {};
      if (value && typeof value === "object" && !Array.isArray(value)) {
        for (const [key, entry] of Object.entries(value as Record<string, unknown>)) {
          if (!key || !entry || typeof entry !== "object" || Array.isArray(entry)) continue;
          const raw = entry as Record<string, unknown>;
          const duration = Number(raw.duration);
          const rawPeaks = raw.peaks;
          const peaks = rawPeaks instanceof Uint8Array
            ? rawPeaks
            : Array.isArray(rawPeaks)
              ? Uint8Array.from(rawPeaks.map(item => Math.max(0,Math.min(255,Math.round(Number(item)||0)))))
              : null;
          if (Number.isFinite(duration) && duration > 0 && peaks && peaks.length > 0) sources[key] = {duration,peaks};
        }
      }
      normalized = sources;
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
      playing: target.playing === true,
      duration: Math.max(0.001, asFiniteNumber(target.duration, 10)),
      pixelsPerSecond: Math.max(12, Math.min(120, asFiniteNumber(target.pxPerSec, 36))),
      timelineGesturePreview: null,
      timelineSnapGuide: null,
      timelineDropTarget: null,
      timelineActiveDragIds: null,
      timelineGestureContext: null,
      timelineEnvelopePreview: null,
      timelineWaveformSources: {},
      pendingExternalRevision: asString(target.pendingExternalRevision),
      insertMode: target.insertMode === true,
      overwriteMode: target.overwriteMode === true,
      snapEnabled: target.snapEnabled !== false,
      envelopeMode: target.envelopeMode === true,
      slideMode: target.slideMode === true,
      slipMode: target.slipMode === true,
      rendering: target.rendering === true,
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
      ["playing", "playing"],
      ["duration", "duration"],
      ["pxPerSec", "pixelsPerSecond"],
      ["drag", "timelineGestureContext"],
      ["pendingExternalRevision", "pendingExternalRevision"],
      ["insertMode", "insertMode"],
      ["overwriteMode", "overwriteMode"],
      ["snapEnabled", "snapEnabled"],
      ["envelopeMode", "envelopeMode"],
      ["slideMode", "slideMode"],
      ["slipMode", "slipMode"],
      ["rendering", "rendering"],
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
