import type {FilmOperation} from "./film-timeline-contract";
import {filmTimelineMath, type FilmTimelineMoveResolution, type FilmTimelineTrimResolution, type FilmTimelineSnapCandidate} from "./film-timeline-math";

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

const validateMoveTarget = (input: FilmTimelineMoveControllerInput): string | null => {
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
  const resolution = resolveMove({
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
  return {resolution, preview};
};

const buildMoveOperations = (input: FilmTimelineMoveControllerInput): FilmTimelineMoveOperation[] | null => {
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

const resolveTrimGesture = (input: FilmTimelineTrimGestureInput): FilmTimelineTrimGestureResult | null => {
  const clipId = String(input.clipId || "").trim();
  const originalAt = input.originalAt;
  const originalEnd = input.originalEnd;
  if (!clipId || (input.edge !== "start" && input.edge !== "end") ||
      !Number.isFinite(originalAt) || !Number.isFinite(originalEnd) ||
      originalAt < 0 || originalEnd <= originalAt ||
      !Number.isFinite(input.rawDeltaSeconds) ||
      !Number.isFinite(input.pixelsPerSecond) || input.pixelsPerSecond <= 0) return null;

  // Preview and pointer-up commit share this exact snap and boundary resolution.
  const resolution = input.edge === "start"
    ? filmTimelineMath.resolveTrimStart(
      originalAt, originalEnd, input.rawDeltaSeconds, input.candidates,
      input.pixelsPerSecond, input.thresholdPixels, input.snapEnabled, input.frameRate,
    )
    : filmTimelineMath.resolveTrimEnd(
      originalAt, originalEnd, input.rawDeltaSeconds, input.candidates,
      input.pixelsPerSecond, input.thresholdPixels, input.snapEnabled, input.frameRate,
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

export const filmTimelineGestureController = {
  resolveMoveGesture: resolveMove,
  resolveTrimGesture,
  validateMoveTarget,
  buildMovePreview,
  buildMoveOperations,
};
