import type {FilmOperation} from "./film-timeline-contract";
import {filmTimelineMath, type FilmTimelineLinkedEditResolution, type FilmTimelineMoveResolution, type FilmTimelineSlipResolution, type FilmTimelineTrimResolution, type FilmTimelineSnapCandidate} from "./film-timeline-math";

export interface FilmTimelineMoveMemberSnapshot {
  id: string;
  originalAt: number;
  originalEnd?: number;
  trackId: string;
  trackIndex: number;
}

export interface FilmTimelineMoveGestureSnapshot {
  members: readonly FilmTimelineMoveMemberSnapshot[];
  trackId?: string;
  originalTrackIndex?: number;
  trackOrder: readonly string[];
  targetTrackId?: string;
  invalidTargetMessage?: string;
}

export interface FilmTimelineMoveClipSnapshot {
  id: string;
  at?: unknown;
  end?: unknown;
  attrs?: Record<string, unknown> | null;
}

export interface FilmTimelineMoveTrackSnapshot {
  id: string;
  attrs?: Record<string, unknown> | null;
  clips?: readonly FilmTimelineMoveClipSnapshot[];
}

export interface FilmTimelineMoveResolutionInput {
  minimumStart: number;
  rawDeltaSeconds: number;
  candidates: readonly FilmTimelineSnapCandidate[];
  pixelsPerSecond: number;
  thresholdPixels: number;
  snapEnabled: boolean;
  frameRate: number;
}

export interface FilmTimelineMoveControllerInput extends FilmTimelineMoveResolutionInput {
  gesture: FilmTimelineMoveGestureSnapshot;
  tracks: readonly FilmTimelineMoveTrackSnapshot[];
  targetTrackId?: string;
}

export interface FilmTimelineMovePreviewResult {
  resolution: FilmTimelineMoveResolution;
  preview: Record<string, {left: number; targetTrackId?: string}>;
  operations: FilmTimelineMoveOperation[] | null;
}

export interface FilmTimelineMoveOperation {
  op: "move";
  clip: string;
  track_id: string;
  at: number;
}

export interface FilmTimelineTrimGestureInput {
  clipId: string;
  edge: "start" | "end";
  originalAt: number;
  originalEnd: number;
  clipTag: string;
  sourceIn: number;
  playbackSpeed: number;
  rawDeltaSeconds: number;
  candidates: readonly FilmTimelineSnapCandidate[];
  pixelsPerSecond: number;
  thresholdPixels: number;
  snapEnabled: boolean;
  frameRate: number;
}

export interface FilmTimelineTrimGestureResult {
  resolution: FilmTimelineTrimResolution;
  preview: {left: number; width: number};
  operation: FilmOperation;
}

export interface FilmTimelineRollGestureInput {
  leftClipId: string;
  rightClipId: string;
  activeClipId: string;
  rightAt: number;
  leftDuration: number;
  rightDuration: number;
  rawDeltaSeconds: number;
  candidates: readonly FilmTimelineSnapCandidate[];
  pixelsPerSecond: number;
  thresholdPixels: number;
  snapEnabled: boolean;
  frameRate: number;
}

export interface FilmTimelineSlideGestureInput {
  leftClipId: string;
  centerClipId: string;
  rightClipId: string;
  centerAt: number;
  rightAt: number;
  leftDuration: number;
  centerDuration: number;
  rightDuration: number;
  rawDeltaSeconds: number;
  candidates: readonly FilmTimelineSnapCandidate[];
  pixelsPerSecond: number;
  thresholdPixels: number;
  snapEnabled: boolean;
  frameRate: number;
}

export interface FilmTimelineSlipGestureInput {
  clipId: string;
  rawDeltaSeconds: number;
  playbackSpeed: number;
  snapEnabled: boolean;
  frameRate: number;
}

export interface FilmTimelineControllerPreviewEntry {
  left?: number;
  width?: number;
  mode?: "roll" | "slide" | "slip";
  deltaLabel?: string;
}
export type FilmTimelineControllerPreview = Record<string, FilmTimelineControllerPreviewEntry>;

export interface FilmTimelineLinkedGestureResult {
  resolution: FilmTimelineLinkedEditResolution;
  operation: FilmOperation;
  preview: FilmTimelineControllerPreview;
}

export interface FilmTimelineSlipGestureResult {
  resolution: FilmTimelineSlipResolution;
  operation: FilmOperation;
  preview: FilmTimelineControllerPreview;
}

const resolveMove = (input: FilmTimelineMoveResolutionInput): FilmTimelineMoveResolution =>
  filmTimelineMath.resolveMoveGesture(
    input.minimumStart,
    input.rawDeltaSeconds,
    input.candidates,
    input.pixelsPerSecond,
    input.thresholdPixels,
    input.snapEnabled,
    input.frameRate,
  );

const minimumSelectedStart = (gesture: FilmTimelineMoveGestureSnapshot): number =>
  gesture.members.reduce((minimum, member) => Math.min(minimum, member.originalAt), Number.POSITIVE_INFINITY);

const tracksFor = (input: FilmTimelineMoveControllerInput): readonly FilmTimelineMoveTrackSnapshot[] =>
  Array.isArray(input.tracks) ? input.tracks : [];

const findClip = (
  tracks: readonly FilmTimelineMoveTrackSnapshot[],
  clipId: string,
): FilmTimelineMoveClipSnapshot | undefined => {
  for (const track of tracks) {
    const clip = (track.clips || []).find(item => item.id === clipId);
    if (clip) return clip;
  }
  return undefined;
};

const validateMoveTarget = (
  input: FilmTimelineMoveControllerInput,
  resolved?: FilmTimelineMoveResolution,
): string | null => {
  const gesture = input.gesture;
  const order = gesture.trackOrder || [];
  const targetTrackId = String(input.targetTrackId || gesture.targetTrackId || "");
  const targetIndex = order.indexOf(targetTrackId);
  if (targetIndex < 0) return "目标轨道不存在，多选移动已取消。";

  const originIndex = Number.isInteger(gesture.originalTrackIndex)
    ? Number(gesture.originalTrackIndex)
    : order.indexOf(String(gesture.trackId || ""));
  if (originIndex < 0) return "无法确认原轨道位置，多选移动已取消。";

  const members = gesture.members || [];
  const selectedIds = new Set(members.map(member => member.id));
  const resolution = resolved || resolveMove({
    minimumStart: minimumSelectedStart(gesture),
    rawDeltaSeconds: Number(input.rawDeltaSeconds) || 0,
    candidates: input.candidates,
    pixelsPerSecond: input.pixelsPerSecond,
    thresholdPixels: input.thresholdPixels,
    snapEnabled: input.snapEnabled,
    frameRate: input.frameRate,
  });
  const horizontalDelta = resolution.deltaSeconds;
  const tracks = tracksFor(input);

  for (const member of members) {
    const destinationIndex = member.trackIndex + targetIndex - originIndex;
    if (destinationIndex < 0 || destinationIndex >= order.length) {
      return "多选移动超出轨道范围，操作已取消。";
    }
    const destinationId = order[destinationIndex];
    const destination = tracks.find(track => track.id === destinationId);
    if (!destination) return "目标轨道 " + destinationId + " 已不存在，多选移动已取消。";
    if (Object.prototype.hasOwnProperty.call(destination.attrs || {}, "locked")) {
      return "目标轨道 " + destinationId + " 已锁定，多选移动已取消。";
    }

    const sourceClip = findClip(tracks, member.id);
    if (!sourceClip || sourceClip.end == null || !Number.isFinite(Number(sourceClip.end))) continue;
    const targetStart = Math.max(0, member.originalAt + horizontalDelta);
    const duration = Math.max(0, Number(sourceClip.end) - Number(sourceClip.at));
    const targetEnd = targetStart + duration;
    for (const other of destination.clips || []) {
      if (selectedIds.has(other.id) || other.end == null) continue;
      const otherStart = Number(other.at);
      const otherEnd = Number(other.end);
      if (!Number.isFinite(otherStart) || !Number.isFinite(otherEnd)) continue;
      if (targetStart < otherEnd - 0.001 && otherStart < targetEnd - 0.001) {
        return "多选移动后片段 " + member.id + " 与目标轨道 " + destinationId +
          " 上的片段 " + other.id + " 时间重叠，操作已取消。";
      }
    }
  }
  return null;
};

const buildMoveOperationsForResolution = (
  input: FilmTimelineMoveControllerInput,
  resolution: FilmTimelineMoveResolution,
): FilmTimelineMoveOperation[] | null => {
  const gesture = input.gesture;
  // The public operation builders are an authority boundary, not just geometry
  // helpers. Revalidate here so callers cannot persist locked, out-of-range, or
  // overlapping cross-track moves by bypassing the UI's pointer-target guard.
  if (gesture.invalidTargetMessage || validateMoveTarget(input, resolution)) return null;
  const members = gesture.members || [];
  let targetIndex = (gesture.trackOrder || []).indexOf(
    String(input.targetTrackId || gesture.targetTrackId || gesture.trackId || ""),
  );
  if (targetIndex < 0) targetIndex = Number(gesture.originalTrackIndex);
  const originIndex = Number(gesture.originalTrackIndex);
  const trackDelta = targetIndex - originIndex;
  if (!Number.isInteger(targetIndex) || !Number.isInteger(originIndex)) return null;

  const operations: FilmTimelineMoveOperation[] = [];
  for (const member of members) {
    const destinationIndex = member.trackIndex + trackDelta;
    if (destinationIndex < 0 || destinationIndex >= gesture.trackOrder.length) return null;
    const at = Math.max(0, member.originalAt + resolution.deltaSeconds);
    const frameAligned = input.snapEnabled &&
      Math.abs(at * input.frameRate - Math.round(at * input.frameRate)) < 0.00002;
    const preserveFramePrecision = resolution.snapCandidate?.kind === "帧" || frameAligned;
    operations.push({
      op: "move",
      clip: member.id,
      track_id: gesture.trackOrder[destinationIndex],
      at: Number(at.toFixed(preserveFramePrecision ? 6 : 3)),
    });
  }
  return operations.length === members.length ? operations : null;
};

const buildMovePreview = (input: FilmTimelineMoveControllerInput): FilmTimelineMovePreviewResult => {
  const gesture = input.gesture;
  const members = gesture.members || [];
  const resolution = resolveMove({
    minimumStart: minimumSelectedStart(gesture),
    rawDeltaSeconds: input.rawDeltaSeconds,
    candidates: input.candidates,
    pixelsPerSecond: input.pixelsPerSecond,
    thresholdPixels: input.thresholdPixels,
    snapEnabled: input.snapEnabled,
    frameRate: input.frameRate,
  });
  let targetIndex = (gesture.trackOrder || []).indexOf(
    String(input.targetTrackId || gesture.targetTrackId || gesture.trackId || ""),
  );
  if (targetIndex < 0) targetIndex = Number(gesture.originalTrackIndex);
  const originIndex = Number(gesture.originalTrackIndex);
  const trackDelta = targetIndex - originIndex;
  const canPreviewCrossTrack = !gesture.invalidTargetMessage &&
    Number.isInteger(targetIndex) && Number.isInteger(originIndex);
  const preview: Record<string, {left: number; targetTrackId?: string}> = {};
  for (const member of members) {
    const entry: {left: number; targetTrackId?: string} = {
      left: filmTimelineMath.clipLeft(member.originalAt + resolution.deltaSeconds, input.pixelsPerSecond),
    };
    const destinationIndex = member.trackIndex + trackDelta;
    if (canPreviewCrossTrack && destinationIndex >= 0 && destinationIndex < gesture.trackOrder.length) {
      const destinationTrackId = gesture.trackOrder[destinationIndex];
      if (destinationTrackId !== member.trackId) entry.targetTrackId = destinationTrackId;
    }
    preview[member.id] = entry;
  }
  return {
    resolution,
    preview,
    operations: buildMoveOperationsForResolution(input, resolution),
  };
};

const buildMoveOperations = (input: FilmTimelineMoveControllerInput): FilmTimelineMoveOperation[] | null => {
  const resolution = resolveMove({
    minimumStart: minimumSelectedStart(input.gesture),
    rawDeltaSeconds: input.rawDeltaSeconds,
    candidates: input.candidates,
    pixelsPerSecond: input.pixelsPerSecond,
    thresholdPixels: input.thresholdPixels,
    snapEnabled: input.snapEnabled,
    frameRate: input.frameRate,
  });
  return buildMoveOperationsForResolution(input, resolution);
};

const resolveTrimGesture = (input: FilmTimelineTrimGestureInput): FilmTimelineTrimGestureResult | null => {
  const clipId = String(input.clipId || "").trim();
  const originalAt = input.originalAt;
  const originalEnd = input.originalEnd;
  if (!clipId || (input.edge !== "start" && input.edge !== "end") ||
      !Number.isFinite(originalAt) || !Number.isFinite(originalEnd) ||
      originalAt < 0 || originalEnd <= originalAt ||
      !Number.isFinite(input.rawDeltaSeconds) ||
      !Number.isFinite(input.pixelsPerSecond) || input.pixelsPerSecond <= 0) return null;

  // A trim may reveal source frames outside the current selected range, but
  // never before source time zero. FilmDocument validates actual media duration.
  const supportsExtension = ["video", "audio", "img"].includes(input.clipTag);
  const sourceIn = Number.isFinite(input.sourceIn) ? Math.max(0, input.sourceIn) : 0;
  const playbackSpeed = Number.isFinite(input.playbackSpeed) && input.playbackSpeed > 0
    ? input.playbackSpeed : 1;
  const minimumStart = !supportsExtension
    ? originalAt + 0.04
    : (input.clipTag === "video" || input.clipTag === "audio")
      ? Math.max(0, originalAt - sourceIn / playbackSpeed)
      : 0;
  const resolution = input.edge === "start"
    ? filmTimelineMath.resolveTrimStart(
      originalAt, originalEnd, input.rawDeltaSeconds, input.candidates,
      input.pixelsPerSecond, input.thresholdPixels, input.snapEnabled, input.frameRate,
      minimumStart,
    )
    : filmTimelineMath.resolveTrimEnd(
      originalAt, originalEnd, input.rawDeltaSeconds, input.candidates,
      input.pixelsPerSecond, input.thresholdPixels, input.snapEnabled, input.frameRate,
      supportsExtension,
    );
  if (!Number.isFinite(resolution.target)) return null;

  const previewStart = input.edge === "start" ? resolution.target : originalAt;
  const previewEnd = input.edge === "end" ? resolution.target : originalEnd;
  const frameRate = Number.isFinite(input.frameRate) ? input.frameRate : 0;
  const frameAligned = frameRate > 0 &&
    Math.abs(resolution.target * frameRate - Math.round(resolution.target * frameRate)) < 0.00002;
  const preserveFramePrecision = resolution.snapCandidate?.kind === "帧" || frameAligned;
  const target = Number(resolution.target.toFixed(preserveFramePrecision ? 6 : 3));
  const operation: FilmOperation = input.edge === "start"
    ? {op: "trim", clip: clipId, edge: "start", at: target}
    : {op: "trim", clip: clipId, edge: "end", end: target};

  return {
    resolution,
    preview: {
      left: filmTimelineMath.clipLeft(previewStart, input.pixelsPerSecond),
      width: Math.max(3, filmTimelineMath.secondsToPixels(previewEnd - previewStart, input.pixelsPerSecond)),
    },
    operation,
  };
};


const frameAligned = (value: number, frameRate: number): boolean =>
  Number.isFinite(frameRate) && frameRate > 0 &&
  Math.abs(value * frameRate - Math.round(value * frameRate)) < 0.00002;

const operationNumber = (value: number, preserveFramePrecision: boolean): number =>
  Number(value.toFixed(preserveFramePrecision ? 6 : 3));

const resolveRollGesture = (input: FilmTimelineRollGestureInput): FilmTimelineLinkedGestureResult | null => {
  const leftClipId = String(input.leftClipId || "").trim();
  const rightClipId = String(input.rightClipId || "").trim();
  if (!leftClipId || !rightClipId || leftClipId === rightClipId ||
      !Number.isFinite(input.rightAt) || input.rightAt < 0 ||
      !Number.isFinite(input.leftDuration) || input.leftDuration <= 0.04 ||
      !Number.isFinite(input.rightDuration) || input.rightDuration <= 0.04 ||
      !Number.isFinite(input.rawDeltaSeconds)) return null;
  const resolution = filmTimelineMath.resolveRollGesture(
    input.rightAt, input.rawDeltaSeconds, input.leftDuration, input.rightDuration,
    input.candidates, input.pixelsPerSecond, input.thresholdPixels, input.snapEnabled, input.frameRate,
  );
  const preserveFramePrecision = resolution.snapCandidate?.kind === "帧" ||
    frameAligned(resolution.targetAt, input.frameRate);
  const delta = resolution.deltaSeconds;
  const activeClipId = input.activeClipId;
  if (activeClipId !== leftClipId && activeClipId !== rightClipId) return null;
  const preview: FilmTimelineControllerPreview = {
    [leftClipId]: {
      width: Math.max(3, filmTimelineMath.secondsToPixels(input.leftDuration + delta, input.pixelsPerSecond)),
    },
    [rightClipId]: {
      left: Math.max(0, filmTimelineMath.clipLeft(input.rightAt + delta, input.pixelsPerSecond)),
      width: Math.max(3, filmTimelineMath.secondsToPixels(input.rightDuration - delta, input.pixelsPerSecond)),
    },
  };
  preview[activeClipId] = {
    ...preview[activeClipId],
    mode: "roll",
    deltaLabel: "切口 " + (delta >= 0 ? "+" : "") + delta.toFixed(2) + "s",
  };
  return {
    resolution,
    operation: {
      op: "roll", clip: leftClipId, other_clip: rightClipId,
      delta: operationNumber(delta, preserveFramePrecision),
    },
    preview,
  };
};

const resolveSlideGesture = (input: FilmTimelineSlideGestureInput): FilmTimelineLinkedGestureResult | null => {
  const leftClipId = String(input.leftClipId || "").trim();
  const centerClipId = String(input.centerClipId || "").trim();
  const rightClipId = String(input.rightClipId || "").trim();
  if (!leftClipId || !centerClipId || !rightClipId ||
      new Set([leftClipId, centerClipId, rightClipId]).size !== 3 ||
      !Number.isFinite(input.centerAt) || input.centerAt < 0 ||
      !Number.isFinite(input.rightAt) || input.rightAt < 0 ||
      !Number.isFinite(input.leftDuration) || input.leftDuration <= 0.04 ||
      !Number.isFinite(input.centerDuration) || input.centerDuration <= 0.04 ||
      !Number.isFinite(input.rightDuration) || input.rightDuration <= 0.04 ||
      !Number.isFinite(input.rawDeltaSeconds)) return null;
  const resolution = filmTimelineMath.resolveSlideGesture(
    input.centerAt, input.rawDeltaSeconds, input.leftDuration, input.centerDuration, input.rightDuration,
    input.candidates, input.pixelsPerSecond, input.thresholdPixels, input.snapEnabled, input.frameRate,
  );
  const preserveFramePrecision = resolution.snapCandidate?.kind === "帧" ||
    frameAligned(resolution.targetAt, input.frameRate);
  const delta = resolution.deltaSeconds;
  const preview: FilmTimelineControllerPreview = {
    [leftClipId]: {
      width: Math.max(3, filmTimelineMath.secondsToPixels(input.leftDuration + delta, input.pixelsPerSecond)),
    },
    [centerClipId]: {
      left: Math.max(0, filmTimelineMath.clipLeft(input.centerAt + delta, input.pixelsPerSecond)),
      mode: "slide",
      deltaLabel: "滑移 " + (delta >= 0 ? "+" : "") + delta.toFixed(2) + "s",
    },
    [rightClipId]: {
      left: Math.max(0, filmTimelineMath.clipLeft(input.rightAt + delta, input.pixelsPerSecond)),
      width: Math.max(3, filmTimelineMath.secondsToPixels(input.rightDuration - delta, input.pixelsPerSecond)),
    },
  };
  return {
    resolution,
    operation: {
      op: "slide", clip: centerClipId,
      delta: operationNumber(delta, preserveFramePrecision),
    },
    preview,
  };
};

const resolveSlipGesture = (input: FilmTimelineSlipGestureInput): FilmTimelineSlipGestureResult | null => {
  const clipId = String(input.clipId || "").trim();
  if (!clipId || !Number.isFinite(input.rawDeltaSeconds) ||
      !Number.isFinite(input.playbackSpeed) || input.playbackSpeed <= 0) return null;
  const resolution = filmTimelineMath.resolveSlipGesture(
    input.rawDeltaSeconds, input.playbackSpeed, input.snapEnabled, input.frameRate,
  );
  const delta = resolution.sourceDeltaSeconds;
  return {
    resolution,
    operation: {
      op: "slip", clip: clipId,
      delta_source: operationNumber(delta, input.snapEnabled),
    },
    preview: {
      [clipId]: {
        mode: "slip",
        deltaLabel: "源偏移 " + (delta >= 0 ? "+" : "") + delta.toFixed(2) + "s",
      },
    },
  };
};

export const filmTimelineGestureController = {
  resolveMoveGesture: resolveMove,
  resolveTrimGesture,
  resolveRollGesture,
  resolveSlideGesture,
  resolveSlipGesture,
  validateMoveTarget,
  buildMovePreview,
  buildMoveOperations,
};
