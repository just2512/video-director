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



export type FilmTimelineGestureStartMode = "move" | "trim-start" | "trim-end" | "roll" | "slide" | "slip";

export interface FilmTimelineGestureModeInput {
  clipTag:string;
  trimEdge?:"start"|"end"|null;
  ctrlKey?:boolean;
  altKey?:boolean;
  shiftKey?:boolean;
  slideMode?:boolean;
  slipMode?:boolean;
}

export interface FilmTimelineGestureStartInput {
  tracks:unknown;
  clipId:string;
  mode:FilmTimelineGestureStartMode;
  trimEdge?:"start"|"end"|null;
  selectedIds:readonly string[];
  pointerId:number;
  startX:number;
  startY:number;
  startScrollLeft:number;
}

type GestureStartRecord = Record<string,unknown>;
type GestureStartTrack = {
  id:string;
  record:GestureStartRecord;
  clips:Array<{id:string;record:GestureStartRecord}>;
};
type GestureStartFailure =
  | {code:"CLIP_NOT_FOUND"}
  | {code:"UNKNOWN_DURATION"}
  | {code:"INVALID_POINTER_CONTEXT"}
  | {code:"INVALID_GESTURE_MODE"}
  | {code:"NO_VALID_MEMBERS"}
  | {code:"ROLL_CUT_REQUIRED"}
  | {code:"SLIDE_NEIGHBORS_REQUIRED"}
  | {code:"MOVE_MEMBER_BLOCKED";kind:"片段"|"轨道";id:string;reason:"已不存在"|"已锁定"};

export type FilmTimelineGestureStartResult =
  | {ok:true;context:GestureStartRecord;memberIds:string[]}
  | {ok:false;error:GestureStartFailure};

const gestureStartRecord = (value:unknown):GestureStartRecord|null =>
  value && typeof value==="object" && !Array.isArray(value) ? value as GestureStartRecord : null;
const gestureStartString = (value:unknown):string =>
  typeof value==="string" ? value : value==null ? "" : String(value);
const gestureStartNumber = (value:unknown):number|undefined => {
  const number=typeof value==="number" ? value : Number(value);
  return Number.isFinite(number) ? number : undefined;
};
const gestureStartHas = (record:GestureStartRecord|null,key:string):boolean =>
  !!record && Object.prototype.hasOwnProperty.call(record,key);
const gestureStartAt = (clip:GestureStartRecord):number|undefined => gestureStartNumber(clip.at);
const gestureStartEnd = (clip:GestureStartRecord):number|undefined =>
  clip.end==null ? undefined : gestureStartNumber(clip.end);
const gestureStartDuration = (clip:GestureStartRecord):number|undefined =>
  clip.duration==null ? undefined : gestureStartNumber(clip.duration);

const gestureStartTracks = (value:unknown):GestureStartTrack[] => {
  if(!Array.isArray(value)) return [];
  const result:GestureStartTrack[]=[];
  for(const rawTrack of value) {
    const record=gestureStartRecord(rawTrack);
    const id=record ? gestureStartString(record.id) : "";
    if(!record||!id) continue;
    const clips:Array<{id:string;record:GestureStartRecord}>=[];
    if(Array.isArray(record.clips)) {
      for(const rawClip of record.clips) {
        const clip=gestureStartRecord(rawClip);
        const clipId=clip ? gestureStartString(clip.id) : "";
        if(clip&&clipId) clips.push({id:clipId,record:clip});
      }
    }
    result.push({id,record,clips});
  }
  return result;
};

const gestureStartFindClip = (
  tracks:GestureStartTrack[],clipId:string,
):{trackId:string;track:GestureStartRecord;clip:GestureStartRecord}|null => {
  for(const track of tracks) {
    const found=track.clips.find(item=>item.id===clipId);
    if(found) return {trackId:track.id,track:track.record,clip:found.record};
  }
  return null;
};

export const resolveTimelineGestureStartMode = (
  input:FilmTimelineGestureModeInput,
):FilmTimelineGestureStartMode => {
  const edge=input.trimEdge==="start"||input.trimEdge==="end" ? input.trimEdge : null;
  const media=input.clipTag==="video"||input.clipTag==="audio";
  if(edge) return input.ctrlKey===true ? "roll" : edge==="start" ? "trim-start" : "trim-end";
  if(!input.slipMode&&(input.slideMode===true||(input.altKey===true&&input.shiftKey===true))&&media) return "slide";
  if((input.slipMode===true||(input.altKey===true&&input.shiftKey!==true))&&media) return "slip";
  return "move";
};

export const createTimelineGestureStart = (
  input:FilmTimelineGestureStartInput,
):FilmTimelineGestureStartResult => {
  // TypeScript callers are statically checked, but the browser bridge is a
  // runtime boundary from JavaScript. Never reinterpret an unknown mode as move.
  const mode=input.mode;
  if(mode!=="move"&&mode!=="trim-start"&&mode!=="trim-end"&&
     mode!=="roll"&&mode!=="slide"&&mode!=="slip") {
    return {ok:false,error:{code:"INVALID_GESTURE_MODE"}};
  }
  const clipId=gestureStartString(input.clipId);
  const tracks=gestureStartTracks(input.tracks);
  const active=gestureStartFindClip(tracks,clipId);
  if(!clipId||!active) return {ok:false,error:{code:"CLIP_NOT_FOUND"}};
  if(!Number.isInteger(input.pointerId)||input.pointerId<0||
     !Number.isFinite(input.startX)||!Number.isFinite(input.startY)||
     !Number.isFinite(input.startScrollLeft)||input.startScrollLeft<0) {
    return {ok:false,error:{code:"INVALID_POINTER_CONTEXT"}};
  }
  const duration=gestureStartDuration(active.clip);
  if(mode!=="move"&&duration===undefined) return {ok:false,error:{code:"UNKNOWN_DURATION"}};

  const chosenIds=mode==="move"
    ? Array.from(new Set(input.selectedIds.map(gestureStartString).filter(Boolean)))
    : [clipId];
  if(!chosenIds.length||(mode==="move"&&!chosenIds.includes(clipId))) {
    return {ok:false,error:{code:"NO_VALID_MEMBERS"}};
  }
  const trackOrder=tracks.map(track=>track.id);
  const members:Array<{id:string;originalAt:number;originalEnd:number;trackId:string;trackIndex:number}>=[];
  for(const memberId of chosenIds) {
    const found=gestureStartFindClip(tracks,memberId);
    if(!found) return {ok:false,error:{code:"MOVE_MEMBER_BLOCKED",kind:"片段",id:memberId,reason:"已不存在"}};
    if(mode==="move") {
      if(gestureStartHas(gestureStartRecord(found.track.attrs),"locked")) {
        return {ok:false,error:{code:"MOVE_MEMBER_BLOCKED",kind:"轨道",id:found.trackId,reason:"已锁定"}};
      }
      if(gestureStartHas(gestureStartRecord(found.clip.attrs),"data-clip-locked")) {
        return {ok:false,error:{code:"MOVE_MEMBER_BLOCKED",kind:"片段",id:memberId,reason:"已锁定"}};
      }
    }
    const at=gestureStartAt(found.clip);
    if(at===undefined||at<0) return {ok:false,error:{code:"NO_VALID_MEMBERS"}};
    const end=gestureStartEnd(found.clip);
    const rawDuration=gestureStartNumber(found.clip.duration);
    // Keep the prior adapter's finite-number fallback, including null -> 0.
    const originalEnd=end===undefined ? at+(rawDuration===undefined?2:rawDuration) : end;
    const trackIndex=trackOrder.indexOf(found.trackId);
    if(trackIndex<0||originalEnd<at) return {ok:false,error:{code:"NO_VALID_MEMBERS"}};
    members.push({id:memberId,originalAt:at,originalEnd,trackId:found.trackId,trackIndex});
  }
  if(!members.some(member=>member.id===clipId)) return {ok:false,error:{code:"NO_VALID_MEMBERS"}};

  const attrs=gestureStartRecord(active.clip.attrs);
  const source=gestureStartString(attrs?.src);
  const sourceMatch=source.match(/#t=([0-9]+(?:\.[0-9]+)?)(?:,|$)/);
  const rawSpeed=gestureStartNumber(attrs?.speed);
  const originalSpeed=active.clip.tag==="video"||active.clip.tag==="audio"
    ? (rawSpeed===undefined?1:rawSpeed) : 1;
  const at=gestureStartAt(active.clip);
  if(at===undefined) return {ok:false,error:{code:"NO_VALID_MEMBERS"}};

  const context:GestureStartRecord={
    id:clipId,mode,pointerId:input.pointerId,startX:input.startX,startY:input.startY,
    startScrollLeft:input.startScrollLeft,active:false,members,trackOrder,targetTrackId:active.trackId,
  };
  if(mode==="roll") {
    const ordered=(tracks.find(track=>track.id===active.trackId)?.clips||[]).slice().sort(
      (left,right)=>(gestureStartAt(left.record)??0)-(gestureStartAt(right.record)??0)
    );
    const index=ordered.findIndex(item=>item.id===clipId);
    let left:GestureStartRecord|null=null,right:GestureStartRecord|null=null;
    if(input.trimEdge==="end"&&index>=0&&index+1<ordered.length) {
      const next=ordered[index+1].record,end=gestureStartEnd(active.clip),nextAt=gestureStartAt(next);
      if(end!==undefined&&nextAt!==undefined&&Math.abs(end-nextAt)<=0.001) { left=active.clip;right=next; }
    } else if(input.trimEdge==="start"&&index>0) {
      const previous=ordered[index-1].record,previousEnd=gestureStartEnd(previous);
      if(previousEnd!==undefined&&Math.abs(previousEnd-at)<=0.001) { left=previous;right=active.clip; }
    }
    if(!left||!right) return {ok:false,error:{code:"ROLL_CUT_REQUIRED"}};
    const leftDuration=gestureStartDuration(left),rightDuration=gestureStartDuration(right),rightAt=gestureStartAt(right);
    if(leftDuration===undefined||rightDuration===undefined||rightAt===undefined) return {ok:false,error:{code:"ROLL_CUT_REQUIRED"}};
    Object.assign(context,{
      leftClipId:gestureStartString(left.id),rightClipId:gestureStartString(right.id),
      rightAt,leftDuration,rightDuration,
    });
  } else if(mode==="slide") {
    const ordered=(tracks.find(track=>track.id===active.trackId)?.clips||[]).slice().sort(
      (left,right)=>(gestureStartAt(left.record)??0)-(gestureStartAt(right.record)??0)
    );
    const index=ordered.findIndex(item=>item.id===clipId);
    const leftItem=index>0?ordered[index-1]:null;
    const rightItem=index>=0&&index+1<ordered.length?ordered[index+1]:null;
    const left=leftItem?.record||null,right=rightItem?.record||null;
    const leftEnd=left?gestureStartEnd(left):undefined,centerEnd=gestureStartEnd(active.clip);
    const leftDuration=left?gestureStartDuration(left):undefined;
    const rightDuration=right?gestureStartDuration(right):undefined;
    const rightAt=right?gestureStartAt(right):undefined;
    if(!leftItem||!rightItem||leftEnd===undefined||centerEnd===undefined||
       leftDuration===undefined||duration===undefined||rightDuration===undefined||rightAt===undefined||
       Math.abs(leftEnd-at)>0.001||Math.abs(centerEnd-rightAt)>0.001) {
      return {ok:false,error:{code:"SLIDE_NEIGHBORS_REQUIRED"}};
    }
    Object.assign(context,{
      leftClipId:leftItem.id,centerClipId:clipId,rightClipId:rightItem.id,
      centerAt:at,rightAt,leftDuration,centerDuration:duration,rightDuration,
    });
  } else {
    const activeEnd=gestureStartEnd(active.clip);
    const rawDuration=gestureStartNumber(active.clip.duration);
    Object.assign(context,{
      originalAt:at,originalEnd:activeEnd===undefined?at+(rawDuration===undefined?0:rawDuration):activeEnd,
      trackId:active.trackId,originalTrackIndex:trackOrder.indexOf(active.trackId),
      clipTag:gestureStartString(active.clip.tag),originalSourceIn:sourceMatch?Number(sourceMatch[1]):0,
      originalSpeed,
    });
  }
  return {ok:true,context,memberIds:members.map(member=>member.id)};
};

export const filmTimelineGestureController = {
  resolveMoveGesture: resolveMove,
  resolveGestureStartMode: resolveTimelineGestureStartMode,
  createGestureStart: createTimelineGestureStart,
  resolveTrimGesture,
  resolveRollGesture,
  resolveSlideGesture,
  resolveSlipGesture,
  validateMoveTarget,
  buildMovePreview,
  buildMoveOperations,
};
