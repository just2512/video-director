import React, {useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState, useSyncExternalStore} from "react";
import type {FilmOperation} from "./film-timeline-contract";
import {filmTimelineMath, type FilmTimelineMathBridge} from "./film-timeline-math";
import {
  filmStore,
  type FilmStudioAssetMode,
  type FilmStudioStoreBridge,
  type FilmStudioStoreSnapshot,
  type FilmStudioTimelineGesturePreview,
  type FilmStudioTimelineGesturePreviewGeometry,
  type FilmStudioTimelineSnapGuide,
  type FilmStudioTimelineDropTarget,
  type FilmStudioTimelineActiveDragIds,
  type FilmStudioTimelineEnvelopePoint,
  type FilmStudioTimelineEnvelopePreview,
  type FilmStudioTimelineWaveformSource,
  type FilmStudioTimelineWaveformSources,
  type FilmStudioTimelineContextMenu,
  type FilmStudioCanvasElementSelection,
  type FilmStudioTimelineSurfaceClip,
  type FilmStudioTimelineSurfaceTrack,
  type FilmStudioTimelineSurfaceModel,
} from "./film-store";
import {createPortal, flushSync} from "react-dom";
import {createRoot, type Root} from "react-dom/client";

type Rect = {x?: number; y?: number; width?: number; height?: number};

function useFilmStoreField<K extends keyof FilmStudioStoreSnapshot>(key: K): FilmStudioStoreSnapshot[K] {
  const subscribe = useCallback((listener: () => void) => filmStore.subscribeField(key, listener), [key]);
  const getSnapshot = useCallback(() => filmStore.getField(key), [key]);
  return useSyncExternalStore(subscribe, getSnapshot, getSnapshot);
}

const formatFilmTime = (value: number): string => {
  const time = Math.max(0, Number(value) || 0);
  const minutes = Math.floor(time / 60);
  const seconds = time - minutes * 60;
  return String(minutes).padStart(2, "0") + ":" + seconds.toFixed(3).padStart(6, "0");
};

type TimelineRecord = Record<string, unknown>;

const timelineRecord = (value: unknown): TimelineRecord =>
  value && typeof value === "object" && !Array.isArray(value) ? value as TimelineRecord : {};

const timelineKeyframeMarks = (
  tag: string,
  attrs: TimelineRecord,
  duration: number | null,
): Array<{at: number; properties: string[]}> => {
  const raw = attrs["data-film-keyframes"];
  if (tag === "audio" || typeof raw !== "string" || !raw.trim() ||
      duration == null || !Number.isFinite(duration) || duration <= 0) return [];
  let channels: unknown;
  try { channels = JSON.parse(raw); } catch { return []; }
  if (!Array.isArray(channels)) return [];
  const grouped = new Map<string, {at: number; properties: string[]}>();
  for (const rawChannel of channels) {
    const channel = timelineRecord(rawChannel);
    if (!Array.isArray(channel.keyframes)) continue;
    const property = String(channel.property || "");
    for (const rawPoint of channel.keyframes) {
      const point = timelineRecord(rawPoint);
      const at = Number(point.at);
      if (!Number.isFinite(at) || at < 0 || at > duration + 0.001) continue;
      const boundedAt = Math.max(0, Math.min(duration, Number(at.toFixed(3))));
      const key = String(boundedAt);
      let entry = grouped.get(key);
      if (!entry) {
        entry = {at:boundedAt,properties:[]};
        grouped.set(key,entry);
      }
      if (property && !entry.properties.includes(property)) entry.properties.push(property);
    }
  }
  return Array.from(grouped.values()).sort((a,b) => a.at-b.at).slice(0,500);
};

const timelineEnvelopePoints = (
  attrs: TimelineRecord,
  duration: number | null,
): FilmStudioTimelineEnvelopePoint[] => {
  const raw = attrs.volume_envelope;
  if (typeof raw === "string" && raw) {
    try {
      const parsed: unknown = JSON.parse(raw);
      if (Array.isArray(parsed)) {
        const points = parsed.map(item => {
          const point = timelineRecord(item);
          return {t:Number(point.t),v:Number(point.v)};
        }).filter(point => Number.isFinite(point.t) && Number.isFinite(point.v));
        if (points.length) return points;
      }
    } catch {
      // Retain the legacy flat-envelope fallback for malformed keyframe data.
    }
  }
  const fallbackDuration = Math.max(0.04, duration == null || !Number.isFinite(duration) ? 0.04 : duration);
  return [{t:0,v:1},{t:fallbackDuration,v:1}];
};

const buildTimelineSurfaceModel = (
  filmValue: unknown,
  projectId: string,
  duration: number,
  pixelsPerSecond: number,
  selectedIds: readonly string[],
): FilmStudioTimelineSurfaceModel | null => {
  const film = timelineRecord(filmValue);
  if (film.exists !== true || !Array.isArray(film.tracks)) return null;
  const safeDuration = Number.isFinite(duration) ? Math.max(0,duration) : 0;
  const scale = Number.isFinite(pixelsPerSecond) && pixelsPerSecond > 0 ? pixelsPerSecond : 36;
  const laneWidth = Math.max(720,Math.ceil(filmTimelineMath.secondsToPixels(safeDuration,scale)));
  const tickCount = Math.min(240,Math.ceil(safeDuration/5));
  const ticks = Array.from({length:tickCount+1},(_,index) => {
    const at = Math.min(safeDuration,index*5);
    return {at,label:formatFilmTime(at),left:filmTimelineMath.secondsToPixels(at,scale)};
  });
  const tracks: FilmTimelineSurfaceTrack[] = film.tracks.map(rawTrack => {
    const track = timelineRecord(rawTrack);
    const attrs = timelineRecord(track.attrs);
    const id = String(track.id ?? "");
    const rawClips = Array.isArray(track.clips) ? track.clips : [];
    const clips: FilmTimelineSurfaceClip[] = rawClips.map(rawClip => {
      const clip = timelineRecord(rawClip);
      const clipAttrs = timelineRecord(clip.attrs);
      const clipId = String(clip.id ?? "");
      const tag = String(clip.tag || "");
      const atValue = Number(clip.at);
      const at = Number.isFinite(atValue) ? atValue : 0;
      const rawDuration = clip.duration;
      const parsedDuration = rawDuration == null ? null : Number(rawDuration);
      const clipDuration = parsedDuration == null || !Number.isFinite(parsedDuration) ? null : parsedDuration;
      const assetId = clipAttrs["data-asset-id"] || "";
      const thumbnail = assetId && tag !== "audio"
        ? "/api/asset_thumbnail?asset_id=" + encodeURIComponent(String(assetId)) +
          "&project_id=" + encodeURIComponent(projectId)
        : "";
      const sourceStartNumber = Number(clip.src_in);
      const sourceStart = Number.isFinite(sourceStartNumber) ? sourceStartNumber || 0 : 0;
      const sourceEnd = typeof clip.src_out === "number" && Number.isFinite(clip.src_out) ? clip.src_out : null;
      const locked = Object.prototype.hasOwnProperty.call(clipAttrs,"data-clip-locked");
      return {
        id:clipId,
        trackId:id,
        tag,
        title:String(clipAttrs.title || clipId),
        at,
        left:filmTimelineMath.clipLeft(at,scale),
        width:filmTimelineMath.clipWidth(clipDuration,scale,24,2,0,false),
        atLabel:formatFilmTime(at),
        durationLabel:clipDuration == null ? "" : " · " + clipDuration.toFixed(2) + "s",
        selected:selectedIds.includes(clipId),
        thumbnail:String(thumbnail),
        duration:clipDuration,
        keyframeMarks:timelineKeyframeMarks(tag,clipAttrs,clipDuration),
        waveform:tag === "audio",
        waveformKey:String(clip.media_url || "").split("#",1)[0],
        sourceStart,
        sourceEnd,
        envelope:(tag === "audio" || tag === "video") && clipDuration != null,
        envelopePoints:timelineEnvelopePoints(clipAttrs,clipDuration),
        locked,
        trimHandles:clipDuration != null && !locked,
      };
    });
    return {
      id,
      label:String(attrs["data-label"] || id),
      locked:Object.prototype.hasOwnProperty.call(attrs,"locked"),
      syncLocked:Object.prototype.hasOwnProperty.call(attrs,"sync-lock"),
      hidden:Object.prototype.hasOwnProperty.call(attrs,"hidden"),
      muted:Object.prototype.hasOwnProperty.call(attrs,"muted"),
      clips,
    };
  });
  return {laneWidth,totalWidth:132+laneWidth,pixelsPerSecond:scale,ticks,tracks};
};


export type CanvasElementSelection = FilmStudioCanvasElementSelection;
type FilmClipInspectorValues = {
  at: string;
  speed: string;
  volume: string;
  muted: "true" | "false";
  fadeIn: string;
  fadeOut: string;
  title: string;
};
export interface FilmClipInspectorModel {
  id: string;
  tag: string;
  at: number;
  duration: number | null;
  attrs: Record<string, string>;
  fps?: number;
  playhead?: number;
  trackLocked?: boolean;
}
type FilmKeyframeProperty = "x"|"y"|"scale"|"scale_x"|"scale_y"|"rotation"|"opacity"|"blur"|"camera_x"|"camera_y"|"camera_scale"|"offset_x"|"offset_y";
type FilmKeyframePreset = "linear"|"ease_in"|"ease_out"|"ease_in_out";
type FilmKeyframeBezier = {type:"bezier";x1:number;y1:number;x2:number;y2:number};
type FilmKeyframeWindow =
  | {type:"window";preset:FilmKeyframePreset;start:number;end:number}
  | {type:"window";curve:FilmKeyframeBezier;start:number;end:number};
type FilmKeyframeEasing = FilmKeyframePreset|FilmKeyframeBezier|FilmKeyframeWindow;
const DEFAULT_FILM_BEZIER: Omit<FilmKeyframeBezier,"type"> = {x1:.25,y1:.1,x2:.25,y2:1};
type FilmKeyframePoint = {at:number;value:number;easing:FilmKeyframeEasing};
type FilmKeyframeChannel = {property:FilmKeyframeProperty;keyframes:FilmKeyframePoint[]};
const FILM_KEYFRAME_PROPERTIES: Array<{value:FilmKeyframeProperty;label:string;min:number;max:number;initial:number}> = [
  {value:"x",label:"水平位置 X",min:0,max:1,initial:.5},
  {value:"y",label:"垂直位置 Y",min:0,max:1,initial:.5},
  {value:"scale",label:"整体缩放",min:.1,max:5,initial:1},
  {value:"scale_x",label:"水平缩放",min:.1,max:5,initial:1},
  {value:"scale_y",label:"垂直缩放",min:.1,max:5,initial:1},
  {value:"rotation",label:"旋转角度",min:-3600,max:3600,initial:0},
  {value:"opacity",label:"不透明度",min:0,max:1,initial:1},
  {value:"blur",label:"模糊",min:0,max:100,initial:0},
  {value:"camera_x",label:"镜头中心 X",min:0,max:1,initial:.5},
  {value:"camera_y",label:"镜头中心 Y",min:0,max:1,initial:.5},
  {value:"camera_scale",label:"镜头缩放",min:.1,max:5,initial:1},
  {value:"offset_x",label:"水平偏移",min:-10000,max:10000,initial:0},
  {value:"offset_y",label:"垂直偏移",min:-10000,max:10000,initial:0},
];
const keyframeInfo = (property:FilmKeyframeProperty) => FILM_KEYFRAME_PROPERTIES.find(item=>item.value===property)!;
const parseBezierEasing = (raw:any):FilmKeyframeBezier|null => {
  if(!raw||typeof raw!=="object"||raw.type!=="bezier")return null;
  const values={x1:Number(raw.x1),y1:Number(raw.y1),x2:Number(raw.x2),y2:Number(raw.y2)};
  if(Object.values(values).some(value=>!Number.isFinite(value)||value<0||value>1)||values.x1>values.x2)return null;
  return {type:"bezier",x1:values.x1,y1:values.y1,x2:values.x2,y2:values.y2};
};
const bezierCurveFor = (easing:FilmKeyframeEasing):FilmKeyframeBezier|null => {
  if(typeof easing==="string")return null;
  if(easing.type==="bezier")return easing;
  return "curve" in easing ? easing.curve : null;
};
const parseFilmKeyframes = (raw:string):FilmKeyframeChannel[] => {
  if(!raw)return [];
  try {
    const input=JSON.parse(raw);
    if(!Array.isArray(input))return [];
    const result:FilmKeyframeChannel[]=[];
    for(const channel of input){
      if(!channel||!FILM_KEYFRAME_PROPERTIES.some(item=>item.value===channel.property)||!Array.isArray(channel.keyframes)||result.some(item=>item.property===channel.property))continue;
      const points:FilmKeyframePoint[]=channel.keyframes.filter((point:any)=>point&&Number.isFinite(Number(point.at))&&Number.isFinite(Number(point.value)))
        .map((point:any)=>{
          const rawEasing=point.easing||"linear";
          let easing:FilmKeyframeEasing="linear";
          if(typeof rawEasing==="string"&&["linear","ease_in","ease_out","ease_in_out"].includes(rawEasing)){
            easing=rawEasing as FilmKeyframePreset;
          }else if(rawEasing&&typeof rawEasing==="object"&&rawEasing.type==="bezier"){
            easing=parseBezierEasing(rawEasing)||"linear";
          }else if(rawEasing&&typeof rawEasing==="object"&&rawEasing.type==="window"
            &&Number.isFinite(Number(rawEasing.start))&&Number.isFinite(Number(rawEasing.end))
            &&Number(rawEasing.start)>=0&&Number(rawEasing.end)<=1&&Number(rawEasing.end)>Number(rawEasing.start)){
            const curve=parseBezierEasing(rawEasing.curve);
            if(curve){
              easing={type:"window",curve,start:Number(rawEasing.start),end:Number(rawEasing.end)};
            }else if(["linear","ease_in","ease_out","ease_in_out"].includes(rawEasing.preset)){
              easing={type:"window",preset:rawEasing.preset as FilmKeyframePreset,start:Number(rawEasing.start),end:Number(rawEasing.end)};
            }
          }
          return {at:Number(point.at),value:Number(point.value),easing};
        })
        .sort((a:FilmKeyframePoint,b:FilmKeyframePoint)=>a.at-b.at);
      if(points.length)result.push({property:channel.property,keyframes:points});
    }
    return result;
  } catch { return []; }
};
const clampKeyframe = (value:number,min:number,max:number)=>Math.max(min,Math.min(max,value));
const keyframeEase = (t:number,easing:FilmKeyframeEasing):number=>{
  const x=clampKeyframe(t,0,1);
  const presetValue=(value:number,preset:FilmKeyframePreset):number=>{
    const v=clampKeyframe(value,0,1);
    if(preset==="ease_in")return v*v;
    if(preset==="ease_out")return 1-(1-v)*(1-v);
    if(preset==="ease_in_out")return v<.5?2*v*v:1-Math.pow(-2*v+2,2)/2;
    return v;
  };
  const bezierValue=(value:number,curve:FilmKeyframeBezier):number=>{
    const target=clampKeyframe(value,0,1);
    const cubic=(u:number,a:number,b:number)=>{
      const inverse=1-u;
      return 3*inverse*inverse*u*a+3*inverse*u*u*b+u*u*u;
    };
    if(target<=0||target>=1)return target;
    let low=0,high=1;
    for(let i=0;i<28;i++){
      const middle=(low+high)*.5;
      if(cubic(middle,curve.x1,curve.x2)<target)low=middle;else high=middle;
    }
    return clampKeyframe(cubic((low+high)*.5,curve.y1,curve.y2),0,1);
  };
  const baseValue=(value:number,spec:FilmKeyframePreset|FilmKeyframeBezier)=>
    typeof spec==="string"?presetValue(value,spec):bezierValue(value,spec);
  if(typeof easing==="string")return presetValue(x,easing);
  if(easing.type==="bezier")return bezierValue(x,easing);
  const start=clampKeyframe(easing.start,0,1),end=clampKeyframe(easing.end,0,1);
  if(end<=start)return x;
  const spec:FilmKeyframePreset|FilmKeyframeBezier="curve" in easing?easing.curve:easing.preset;
  const low=baseValue(start,spec),high=baseValue(end,spec);
  if(Math.abs(high-low)<1e-12)return x;
  return clampKeyframe((baseValue(start+(end-start)*x,spec)-low)/(high-low),0,1);
};
const keyframePath = (channel:FilmKeyframeChannel,duration:number,min:number,max:number)=>{
  if(duration<=0||channel.keyframes.length<2)return "";
  const x=(t:number)=>8+clampKeyframe(t/duration,0,1)*344;
  const y=(v:number)=>8+(1-(clampKeyframe(v,min,max)-min)/Math.max(1e-9,max-min))*72;
  const commands:string[]=[];
  for(let i=1;i<channel.keyframes.length;i++){
    const a=channel.keyframes[i-1],b=channel.keyframes[i],span=b.at-a.at;
    if(span<=0)continue;
    for(let j=0;j<=12;j++){
      if(i>1&&j===0)continue;
      const t=j/12,e=keyframeEase(t,b.easing);
      commands.push((commands.length?"L":"M")+x(a.at+span*t).toFixed(2)+" "+y(a.value+(b.value-a.value)*e).toFixed(2));
    }
  }
  return commands.join(" ");
};
interface CanvasInspectorBridge {
  render(element: CanvasElementSelection | null): void;
  renderClip(emptyMessage?: string): void;
  saveSelected(): Promise<void> | void;
  sync(element: CanvasElementSelection): void;
}
interface FilmStudioReactToolbarBridge {
  render(): void;
  renderTimeline(): void;
  renderScrub(): void;
}
type FilmStudioAssetClip = {
  id: string;
  tag: string;
  title: string;
  meta: string;
  thumbnail: string;
  selected: boolean;
};
type FilmStudioLibraryAsset = {
  id: string;
  kind: string;
  label: string;
  meta: string;
};
type FilmStudioAssetsModel = {
  clips: FilmStudioAssetClip[];
};
interface FilmStudioReactAssetsBridge {
  render(model: FilmStudioAssetsModel): void;
}
type FilmTimelineSurfaceClip = FilmStudioTimelineSurfaceClip;
type FilmTimelineSurfaceTrack = FilmStudioTimelineSurfaceTrack;
type FilmTimelineSurfaceModel = FilmStudioTimelineSurfaceModel;
type FilmTimelineContextMenu = FilmStudioTimelineContextMenu;
type FilmTimelineGesturePreviewGeometry = FilmStudioTimelineGesturePreviewGeometry;
type FilmTimelineGesturePreview = FilmStudioTimelineGesturePreview;
type FilmTimelineSnapGuide = FilmStudioTimelineSnapGuide;
type FilmTimelineDropTarget = FilmStudioTimelineDropTarget;
type FilmTimelineActiveDragIds = FilmStudioTimelineActiveDragIds;

interface FilmStudioReactTimelineBridge {
  renderSurface(): void;
  setGesturePreview(preview: FilmTimelineGesturePreview): void;
  setSnapGuide(guide: FilmTimelineSnapGuide): void;
  setTrackDropTarget(target: FilmTimelineDropTarget): void;
  setActiveDragIds(ids: FilmTimelineActiveDragIds): void;
  setEnvelopePreview(preview: FilmStudioTimelineEnvelopePreview): void;
  clearSurface(): void;
  hasSurface(): boolean;
  openContextMenu(model: FilmTimelineContextMenu): void;
  closeContextMenu(): void;
  isContextMenuOpen(): boolean;
}
type FilmStudioAiMessage = {text: string; role: string};
type FilmStudioAiOperationPreview = {
  id: string; action: string; target: string; detail: string; before: string; after: string;
};
type FilmStudioAiProposal = {
  summary: string; operationCount: number; baseRevision: string;
  operationPreviews: FilmStudioAiOperationPreview[];
  affectedClips: string[];
  affectedTruncatedCount: number;
  truncatedCount: number;
} | null;
type FilmStudioAiDirectorModel = {messages: FilmStudioAiMessage[]; proposal: FilmStudioAiProposal; currentRevision: string};
interface FilmStudioReactAiBridge {
  render(model: FilmStudioAiDirectorModel): void;
}

declare global {
  interface Window {
    filmStudioReactInspector?: CanvasInspectorBridge;
    filmStudioReactToolbar?: FilmStudioReactToolbarBridge;
    filmStudioSaveSelectedElement?: () => Promise<void> | void;
    filmStudioCommitCanvasElement?: (operation: Extract<FilmOperation, {op:"element_set"}>, options?: {allowInspectorDraft?: boolean}) => Promise<void> | void;
    filmStudioShowInspectorError?: (message: string) => void;
    filmStudioCommitInspectorOperations?: (ops: FilmOperation[]) => Promise<void> | void;
    filmStudioSetClipLock?: (clipId: string, locked: boolean) => Promise<void> | void;
    filmStudioSeek?: (value: string | number) => void;
    filmStudioJumpToKeyframe?: (clipId: string, at: number) => void;
    filmStudioTimelineMath?: FilmTimelineMathBridge;
    filmStudioZoom?: (direction: number, anchorClientX?: number) => void;
    filmStudioStore?: FilmStudioStoreBridge;
    filmStudioReactAssets?: FilmStudioReactAssetsBridge;
    filmStudioReactTimeline?: FilmStudioReactTimelineBridge;
    filmStudioReactAi?: FilmStudioReactAiBridge;
    filmStudioAsk?: () => Promise<void> | void;
    filmStudioSelectClip?: (clipId: string) => void;
    filmStudioClickClip?: (clipId: string, event: MouseEvent) => void;
    filmStudioBeginClipDrag?: (event: PointerEvent, element: HTMLElement) => boolean | void;
    filmStudioBeginClipGesture?: (event: PointerEvent, context: {
      clipId: string; trimEdge: "start" | "end" | null; targetIsButton: boolean;
    }) => boolean | void;
    filmStudioMoveClipGesture?: (event: PointerEvent) => void;
    filmStudioEndClipGesture?: (event: PointerEvent) => Promise<void> | void;
    filmStudioCancelClipGesture?: (event: PointerEvent) => void;
    filmStudioOwnsClipGesturePointer?: (pointerId: number) => boolean;
    filmStudioBeginEnvelopeGesture?: (event: PointerEvent, context: {clipId: string; rect: {left: number; top: number; width: number; height: number}}) => boolean;
    filmStudioMoveEnvelopeGesture?: (event: PointerEvent, rect: {left: number; top: number; width: number; height: number}) => void;
    filmStudioEndEnvelopeGesture?: (event: PointerEvent) => Promise<void> | void;
    filmStudioCancelEnvelopeGesture?: (event: PointerEvent) => void;
    filmStudioOwnsEnvelopeGesturePointer?: (pointerId: number) => boolean;
    filmStudioOpenTimelineContextMenu?: (clipId: string, clientX: number, clientY: number) => void;
    filmStudioContextMenuAction?: (action: string, clipId: string) => void;
    filmStudioToggleTrackLock?: (trackId: string) => Promise<boolean> | void;
    filmStudioToggleTrackFlag?: (trackId: string, field: string) => Promise<boolean> | void;
    filmStudioShowAssetMode?: (mode: string) => void;
    filmStudioAddAsset?: (assetId: string) => Promise<void> | void;
  }
}

const asNumber = (value: unknown, fallback: number): number => {
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : fallback;
};

const buildClipInspectorModel = (
  filmValue: unknown,
  selectedId: string,
  playheadValue: number,
): FilmClipInspectorModel | null => {
  const film = timelineRecord(filmValue);
  if (film.exists !== true || !selectedId || !Array.isArray(film.tracks)) return null;
  const fps = Math.max(1,Math.min(60,asNumber(film.fps,30)));
  for (const rawTrack of film.tracks) {
    const track = timelineRecord(rawTrack);
    const trackAttrs = timelineRecord(track.attrs);
    const rawClips = Array.isArray(track.clips) ? track.clips : [];
    for (const rawClip of rawClips) {
      const clip = timelineRecord(rawClip);
      if (String(clip.id ?? "") !== selectedId) continue;
      const durationNumber = clip.duration == null ? null : Number(clip.duration);
      const duration = durationNumber == null || !Number.isFinite(durationNumber) ? null : durationNumber;
      const attrs = timelineRecord(clip.attrs) as Record<string,string>;
      return {
        id:selectedId,
        tag:String(clip.tag || ""),
        at:asNumber(clip.at ?? attrs.at,0),
        duration,
        attrs,
        fps,
        playhead:asNumber(playheadValue,0),
        trackLocked:Object.prototype.hasOwnProperty.call(trackAttrs,"locked"),
      };
    }
  }
  return null;
};

type ClipInspectorBuildResult =
  | {ok: true; ops: FilmOperation[]}
  | {ok: false; message: string};

type CanvasElementFormValues = {
  text: string; left: string; top: string; width: string; height: string;
  color: string; backgroundEnabled: boolean; backgroundColor: string;
  opacity: string; fontWeight: string; textAlign: string; lineHeight: string; letterSpacing: string;
  fontSize: string; rotate: string;
};
type CanvasElementBuildResult =
  | {ok:true; operation:Extract<FilmOperation,{op:"element_set"}>; changed:boolean}
  | {ok:false; message:string};

/** Validate and build a single element_set intent before the revision-checked JS commit bridge. */
export const buildCanvasElementOperation = (
  element: CanvasElementSelection,
  form: CanvasElementFormValues,
): CanvasElementBuildResult => {
  if (!element.clipId || !element.selector.trim()) return {ok:false,message:"画布元素缺少片段 ID 或 selector；请重新选择元素。"};
  const ranges:Array<[string,string,number,number]>=[
    ["左坐标",form.left,-100000,100000],["上坐标",form.top,-100000,100000],
    ["宽度",form.width,1,10000],["高度",form.height,1,10000],
    ["字号",form.fontSize,1,1000],["旋转角度",form.rotate,-3600,3600],
  ];
  const values:Record<string,number>={};
  for(const [label,raw,min,max] of ranges){
    if(!raw.trim())return {ok:false,message:label+"必须填写有效数字。"};
    const value=Number(raw);
    if(!Number.isFinite(value)||value<min||value>max)return {ok:false,message:label+"必须在 "+min+" 到 "+max+" 之间。"};
    values[label]=value;
  }
  if(!/^#[0-9a-f]{6}$/i.test(form.color) || !/^#[0-9a-f]{6}$/i.test(form.backgroundColor)) {
    return {ok:false,message:"颜色必须是有效的 RGB 颜色。"};
  }
  if(element.textEditable&&form.text.length>4000)return {ok:false,message:"画布元素文本不能超过 4000 个字符。"};
  if(!form.opacity.trim() || !Number.isFinite(Number(form.opacity)) || Number(form.opacity)<0 || Number(form.opacity)>1) {
    return {ok:false,message:"不透明度必须是 0 到 1 之间的有效数字。"};
  }
  if(!form.fontWeight.trim() || !Number.isInteger(Number(form.fontWeight)) || Number(form.fontWeight)<100
      || Number(form.fontWeight)>900 || Number(form.fontWeight)%100!==0) {
    return {ok:false,message:"字重必须是 100 到 900 之间、以 100 为步进的整数。"};
  }
  if(!["left","center","right","justify","start","end"].includes(form.textAlign)) {
    return {ok:false,message:"文字对齐方式无效。"};
  }

  const rect=element.localRect||element.rect||{};
  const styles=element.styles||{};
  const style:Record<string,string>={};
  const nextLeft=values["左坐标"],nextTop=values["上坐标"];
  const nextWidth=values["宽度"],nextHeight=values["高度"];
  const leftChanged=Math.abs(nextLeft-asNumber(rect.x,0))>0.11;
  const topChanged=Math.abs(nextTop-asNumber(rect.y,0))>0.11;
  const widthChanged=Math.abs(nextWidth-asNumber(rect.width,1))>0.11;
  const heightChanged=Math.abs(nextHeight-asNumber(rect.height,1))>0.11;
  const geometryChanged=leftChanged||topChanged||widthChanged||heightChanged;
  // Position is not an Inspector field: materialize it only when geometry changes.
  if(geometryChanged&&String(styles.position||"")!=="fixed")style.position="fixed";
  if(leftChanged)style.left=nextLeft.toFixed(2)+"px";
  if(topChanged)style.top=nextTop.toFixed(2)+"px";
  if(widthChanged)style.width=nextWidth.toFixed(2)+"px";
  if(heightChanged)style.height=nextHeight.toFixed(2)+"px";
  if(toHexColor(String(styles.color||"#ffffff")).toLowerCase()!==form.color.toLowerCase())style.color=form.color;

  const currentFontSize=asNumber(parseFloat(styles.fontSize),24);
  if(Math.abs(values["字号"]-currentFontSize)>0.01)style.fontSize=values["字号"].toFixed(2)+"px";
  if(Math.abs(values["旋转角度"]-rotateDegrees(styles))>0.01)style.rotate=values["旋转角度"].toFixed(2)+"deg";

  const currentBackground = String(styles.backgroundColor || "transparent");
  const currentBackgroundTransparent = isTransparentBackground(currentBackground);
  if(form.backgroundEnabled) {
    if(currentBackgroundTransparent || toHexColor(currentBackground).toLowerCase()!==form.backgroundColor.toLowerCase()) {
      style.backgroundColor=form.backgroundColor;
    }
  } else if(!currentBackgroundTransparent) {
    // Unchecking an existing fill means explicitly restoring transparency.
    style.backgroundColor="transparent";
  }
  const currentOpacity=asNumber(styles.opacity,1);
  if(Math.abs(Number(form.opacity)-currentOpacity)>1e-5)style.opacity=Number(form.opacity).toFixed(3);
  if(normalizedFontWeight(styles.fontWeight)!==Number(form.fontWeight))style.fontWeight=String(Number(form.fontWeight));
  const currentTextAlign=String(styles.textAlign||"left");
  if(currentTextAlign!==form.textAlign)style.textAlign=form.textAlign;

  const appendOptionalCssLength=(key:"lineHeight"|"letterSpacing",raw:string,label:string):string|null=>{
    const value=raw.trim();
    // Blank means preserve the current CSS value (including keywords such as "normal").
    if(!value)return null;
    const match=value.match(/^(-?(?:\d+(?:\.\d+)?|\.\d+))(px|%|em|rem|vh|vw)?$/i);
    if(!match||value.length>120)return label+"必须是有效 CSS 长度值。";
    const magnitude=Number(match[1]);
    if(!Number.isFinite(magnitude))return label+"必须是有限数字。";
    if(key==="lineHeight"&&magnitude<=0)return "行高必须大于 0。";
    if(key==="letterSpacing"&&Math.abs(magnitude)>1000)return "字间距绝对值不能超过 1000。";
    if(String(styles[key]||"").trim()!==value)style[key]=value;
    return null;
  };
  const lineHeightError=appendOptionalCssLength("lineHeight",form.lineHeight,"行高");
  if(lineHeightError)return {ok:false,message:lineHeightError};
  const letterSpacingError=appendOptionalCssLength("letterSpacing",form.letterSpacing,"字间距");
  if(letterSpacingError)return {ok:false,message:letterSpacingError};

  const textChanged=Boolean(element.textEditable)&&form.text!==String(element.text||"");
  const operation:Extract<FilmOperation,{op:"element_set"}>={op:"element_set",clip:element.clipId,selector:element.selector,style};
  if(textChanged)operation.text=form.text;
  return {ok:true,operation,changed:Object.keys(style).length>0||textChanged};
};
const readCanvasElementForm=():CanvasElementFormValues|null=>{
  const ids=["fs-element-text","fs-element-left","fs-element-top","fs-element-width","fs-element-height","fs-element-color",
    "fs-element-background-enabled","fs-element-background-color","fs-element-opacity","fs-element-font-weight","fs-element-text-align",
    "fs-element-line-height","fs-element-letter-spacing","fs-element-font-size","fs-element-rotate"];
  const fields=ids.map(id=>document.getElementById(id) as HTMLInputElement|HTMLTextAreaElement|HTMLSelectElement|null);
  if(fields.some(field=>!field))return null;
  const [text,left,top,width,height,color,backgroundEnabled,backgroundColor,opacity,fontWeight,textAlign,lineHeight,letterSpacing,fontSize,rotate]=fields as Array<HTMLInputElement|HTMLTextAreaElement|HTMLSelectElement>;
  return {
    text:text.value,left:left.value,top:top.value,width:width.value,height:height.value,color:color.value,
    backgroundEnabled:(backgroundEnabled as HTMLInputElement).checked,backgroundColor:backgroundColor.value,
    opacity:opacity.value,fontWeight:fontWeight.value,textAlign:textAlign.value,lineHeight:lineHeight.value,
    letterSpacing:letterSpacing.value,fontSize:fontSize.value,rotate:rotate.value
  };
};

/** Typed intent builder; persistence remains in Studio's revision-checked commit path. */
export const buildClipInspectorOperations = (
  clip: FilmClipInspectorModel,
  form: FilmClipInspectorValues,
): ClipInspectorBuildResult => {
  const attrs = clip.attrs || {};
  const ops: FilmOperation[] = [];
  if (clip.trackLocked) {
    return {ok: false, message: "所在轨道已锁定；请先解锁轨道再修改片段属性。"};
  }
  if (Object.prototype.hasOwnProperty.call(attrs, "data-clip-locked")) {
    return {ok: false, message: "该片段已锁定；请先解锁再修改属性。"};
  }
  const appendNumber = (
    field: "at" | "speed" | "volume",
    raw: string,
    minimum: number,
    maximum: number | null,
    fallback: number,
    label: string,
  ): string | null => {
    if (!raw.trim()) return label + "必须填写有效数字。";
    const value = Number(raw);
    if (!Number.isFinite(value) || value < minimum || (maximum !== null && value > maximum)) {
      return label + "超出允许范围。";
    }
    const previousRaw = attrs[field];
    const previousValue = previousRaw == null ? fallback : Number(previousRaw);
    const previous = Number.isFinite(previousValue) ? previousValue : fallback;
    if (Math.abs(previous - value) > 1e-5) {
      ops.push({op:"set", clip:clip.id, field, value});
    }
    return null;
  };

  let error = appendNumber("at", form.at, 0, null, clip.at, "时间位置");
  if (error) return {ok:false, message:error};
  const mediaControls = clip.tag === "video" || clip.tag === "audio";
  if (mediaControls) {
    error = appendNumber("speed", form.speed, 0.25, 4, 1, "速度");
    if (error) return {ok:false, message:error};
    error = appendNumber("volume", form.volume, 0, 2, 1, "音量");
    if (error) return {ok:false, message:error};
  }

  const title = String(form.title || "");
  if (String(attrs.title || "") !== title) {
    ops.push({op:"set", clip:clip.id, field:"title", value:title || null});
  }

  if (clip.duration != null) {
    if (!form.fadeIn.trim() || !form.fadeOut.trim()) {
      return {ok:false, message:"淡入/淡出必须填写有效数字。"};
    }
    const fadeIn = Number(form.fadeIn), fadeOut = Number(form.fadeOut);
    if (!Number.isFinite(fadeIn) || !Number.isFinite(fadeOut)
      || fadeIn < 0 || fadeOut < 0 || fadeIn > 60 || fadeOut > 60) {
      return {ok:false, message:"淡入/淡出必须是 0–60 秒的有效数字。"};
    }
    if (fadeIn + fadeOut > clip.duration + 0.001) {
      return {ok:false, message:"淡入与淡出时长之和不能超过片段时长。"};
    }
    const previousFade = String(attrs.fade || "0,0").split(",");
    if (Math.abs(Number(previousFade[0] || 0) - fadeIn) > 0.001
      || Math.abs(Number(previousFade[1] || 0) - fadeOut) > 0.001) {
      ops.push({
        op:"set", clip:clip.id, field:"fade",
        value:(fadeIn > 0 || fadeOut > 0) ? fadeIn.toFixed(3) + "," + fadeOut.toFixed(3) : null,
      });
    }
  }

  if (mediaControls) {
    const currentlyMuted = Object.prototype.hasOwnProperty.call(attrs, "muted");
    const muted = form.muted === "true";
    if (currentlyMuted !== muted) ops.push({op:"set", clip:clip.id, field:"muted", value:muted});
  }
  return {ok:true, ops};
};


const toHexColor = (value: string): string => {
  const text = String(value || "").trim();
  const rgb = text.match(/^rgba?\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)/i);
  if (rgb) return "#" + rgb.slice(1, 4).map((part) =>
    Math.max(0, Math.min(255, Number(part))).toString(16).padStart(2, "0")
  ).join("");
  if (/^#[0-9a-f]{6}$/i.test(text)) return text;
  if (/^#[0-9a-f]{3}$/i.test(text)) {
    return "#" + text.slice(1).split("").map((part) => part + part).join("");
  }
  return "#ffffff";
};

const isTransparentBackground = (value: string): boolean => {
  const text = String(value || "").trim().toLowerCase();
  if (!text || text === "transparent") return true;
  const rgba = text.match(/^rgba\(\s*[^,]+,\s*[^,]+,\s*[^,]+,\s*([+-]?(?:\d+\.?\d*|\.\d+))\s*\)$/i);
  return Boolean(rgba && Number(rgba[1]) <= 0);
};

const normalizedFontWeight = (value: string): number => {
  const text = String(value || "").trim().toLowerCase();
  if (!text || text === "normal" || text === "lighter") return 400;
  if (text === "bold" || text === "bolder") return 700;
  const number = Number(text);
  return Number.isFinite(number)
    ? Math.max(100, Math.min(900, Math.round(number / 100) * 100))
    : 400;
};

const rotateDegrees = (styles: Record<string, string>): number => {
  const match = String(styles.rotate || "").match(/-?\d+(?:\.\d+)?/);
  return match ? Number(match[0]) : 0;
};

const ClipInspector: React.FC<{clip: FilmClipInspectorModel | null; emptyMessage?: string}> = ({clip, emptyMessage}) => {
  const [feedback, setFeedback] = useState("");
  const keyframesAttribute = clip?.attrs?.["data-film-keyframes"] || "";
  const currentAttrs = clip?.attrs || {};
  const currentFade = String(currentAttrs.fade || "0,0").split(",");
  const clipFormDefaults:FilmClipInspectorValues = {
    at:String(currentAttrs.at ?? clip?.at ?? 0),
    speed:String(currentAttrs.speed || 1),
    volume:String(currentAttrs.volume == null ? 1 : currentAttrs.volume),
    muted:Object.prototype.hasOwnProperty.call(currentAttrs,"muted")?"true":"false",
    fadeIn:currentFade[0]||"0",
    fadeOut:currentFade[1]||"0",
    title:String(currentAttrs.title||""),
  };
  const clipFormSignature=JSON.stringify(clipFormDefaults);
  const [keyframeChannels,setKeyframeChannels] = useState<FilmKeyframeChannel[]>(()=>parseFilmKeyframes(keyframesAttribute));
  const [keyframeDirty,setKeyframeDirty] = useState(false);
  const [animationProperty,setAnimationProperty] = useState<FilmKeyframeProperty>("opacity");
  const [newKeyframeAt,setNewKeyframeAt] = useState("0");
  const [newKeyframeValue,setNewKeyframeValue] = useState("1");
  const [newKeyframeEasing,setNewKeyframeEasing] = useState<FilmKeyframePreset|"bezier">("linear");
  const [newBezierCurve,setNewBezierCurve] = useState<Omit<FilmKeyframeBezier,"type">>({...DEFAULT_FILM_BEZIER});
  const [draggingKeyframe,setDraggingKeyframe] = useState<number|null>(null);
  const [draggingBezier,setDraggingBezier] = useState<{index:number;handle:"p1"|"p2"}|null>(null);
  const [keyframeFeedback,setKeyframeFeedback] = useState("");
  const [form, setForm] = useState<FilmClipInspectorValues>(() => ({...clipFormDefaults}));
  const update = <K extends keyof FilmClipInspectorValues>(field: K, value: FilmClipInspectorValues[K]) => {
    setForm((current) => ({...current, [field]: value}));
  };
  useEffect(()=>{
    // The same React component is reused when the selected clip changes. Reset
    // scalar and keyframe drafts from the new revision instead of leaking values
    // from the previously selected clip.
    setForm({...clipFormDefaults});
    setFeedback("");
    setKeyframeChannels(parseFilmKeyframes(keyframesAttribute));
    setKeyframeDirty(false);setDraggingKeyframe(null);setKeyframeFeedback("");
    const duration=clip?.duration;
    const relative=clip&&typeof clip.playhead==="number"?clip.playhead-clip.at:0;
    setNewKeyframeAt((duration!=null&&duration>0?clampKeyframe(relative,0,duration):0).toFixed(3));
    setNewKeyframeValue(String(keyframeInfo(animationProperty).initial));
    setNewKeyframeEasing("linear");setNewBezierCurve({...DEFAULT_FILM_BEZIER});
    setDraggingBezier(null);
  },[clip?.id,clipFormSignature,keyframesAttribute]);
  if (!clip) {
    return <div className="fs-empty" data-react-clip-inspector-empty="true">{emptyMessage || "选择时间线片段后编辑属性。"}</div>;
  }
  const attrs = clip.attrs || {};
  const clipLocked = Object.prototype.hasOwnProperty.call(attrs, "data-clip-locked");
  const trackLocked = Boolean(clip.trackLocked);
  const readOnly = clipLocked || trackLocked;
  const speed = Number(form.speed || 1);
  const hasMediaControls = clip.tag === "video" || clip.tag === "audio";
  const animationInfo=keyframeInfo(animationProperty);
  const activeChannel=keyframeChannels.find(channel=>channel.property===animationProperty)||null;
  const clipDuration=clip.duration||0;
  const frameStep=1/Math.max(1,Math.min(60,clip.fps||30));
  const updateKeyframePoint=(index:number,patch:Partial<FilmKeyframePoint>)=>{
    setKeyframeDirty(true);
    setKeyframeChannels(current=>current.map(channel=>{
      if(channel.property!==animationProperty)return channel;
      const points=channel.keyframes.map(point=>({...point})),point=points[index];
      if(!point)return channel;
      if(typeof patch.value==="number")point.value=clampKeyframe(patch.value,animationInfo.min,animationInfo.max);
      if(patch.easing)point.easing=patch.easing;
      if(typeof patch.at==="number"){
        const lower=index>0?points[index-1].at+frameStep:0;
        const upper=index<points.length-1?points[index+1].at-frameStep:clipDuration;
        point.at=clampKeyframe(Math.round(patch.at/frameStep)*frameStep,Math.min(lower,upper),Math.max(lower,upper));
      }
      return {...channel,keyframes:points};
    }));
    setKeyframeFeedback("");
  };
  const updateBezierControl=(index:number,handle:"p1"|"p2",axis:"x"|"y",rawValue:number)=>{
    if(!Number.isFinite(rawValue))return;
    const field=(handle==="p1"?(axis==="x"?"x1":"y1"):(axis==="x"?"x2":"y2"));
    const value=clampKeyframe(rawValue,0,1);
    setKeyframeChannels(current=>current.map(channel=>{
      if(channel.property!==animationProperty)return channel;
      const points=channel.keyframes.map(point=>({...point})),point=points[index];
      if(!point)return channel;
      const curve=bezierCurveFor(point.easing)||{type:"bezier" as const,...DEFAULT_FILM_BEZIER};
      const next={...curve,[field]:value};
      if(next.x1>next.x2){
        if(field==="x1")next.x1=next.x2;
        if(field==="x2")next.x2=next.x1;
      }
      point.easing=typeof point.easing!=="string"&&point.easing.type==="window"
        ? {...point.easing,curve:next,preset:undefined} as FilmKeyframeWindow
        : next;
      return {...channel,keyframes:points};
    }));
    setKeyframeDirty(true);setKeyframeFeedback("");
  };
  const updateNewBezierControl=(handle:"p1"|"p2",axis:"x"|"y",rawValue:number)=>{
    if(!Number.isFinite(rawValue))return;
    const field=(handle==="p1"?(axis==="x"?"x1":"y1"):(axis==="x"?"x2":"y2"));
    setNewBezierCurve(current=>{
      const next={...current,[field]:clampKeyframe(rawValue,0,1)};
      if(next.x1>next.x2){
        if(field==="x1")next.x1=next.x2;
        if(field==="x2")next.x2=next.x1;
      }
      return next;
    });
  };
  const addKeyframe=()=>{
    if(clip.duration==null||clip.duration<=0){setKeyframeFeedback("片段时长未知，不能安全添加关键帧。");return;}
    const at=Number(newKeyframeAt),value=Number(newKeyframeValue);
    if(!newKeyframeAt.trim()||!Number.isFinite(at)||at<0||at>clip.duration){setKeyframeFeedback("关键帧时间必须位于片段内（剪辑相对秒数）。");return;}
    if(!newKeyframeValue.trim()||!Number.isFinite(value)||value<animationInfo.min||value>animationInfo.max){setKeyframeFeedback("关键帧数值超出当前属性允许范围。");return;}
    const snapped=Math.round(at/frameStep)*frameStep;
    const channel=keyframeChannels.find(item=>item.property===animationProperty);
    if(channel?.keyframes.some(point=>Math.abs(point.at-snapped)<frameStep*0.5)){setKeyframeFeedback("该时间点已经存在关键帧；请编辑已有节点。");return;}
    setKeyframeChannels(current=>{
      const existing=current.find(item=>item.property===animationProperty);
      const easing:FilmKeyframeEasing=newKeyframeEasing==="bezier"
        ? {type:"bezier",...newBezierCurve}
        : newKeyframeEasing;
      const points=[...(existing?.keyframes||[]),{at:snapped,value,easing}].sort((a,b)=>a.at-b.at);
      const nextChannel:FilmKeyframeChannel={property:animationProperty,keyframes:points};
      return existing?current.map(item=>item.property===animationProperty?nextChannel:item):[...current,nextChannel];
    });
    setKeyframeDirty(true);setKeyframeFeedback("");
  };
  const removeKeyframe=(index:number)=>{
    setKeyframeChannels(current=>current.flatMap(channel=>{
      if(channel.property!==animationProperty)return [channel];
      const points=channel.keyframes.filter((_point,i)=>i!==index);
      return points.length?[{...channel,keyframes:points}]:[];
    }));
    setKeyframeDirty(true);setKeyframeFeedback("");
  };
  const handleKeyframePointerMove=(event:React.PointerEvent<SVGSVGElement>)=>{
    if(draggingKeyframe==null||!activeChannel||clip.duration==null||clip.duration<=0)return;
    const rect=event.currentTarget.getBoundingClientRect();
    if(!rect.width||!rect.height)return;
    const x=(event.clientX-rect.left)/rect.width*360,y=(event.clientY-rect.top)/rect.height*96;
    updateKeyframePoint(draggingKeyframe,{
      at:clampKeyframe((x-8)/344*clip.duration,0,clip.duration),
      value:animationInfo.max-(y-8)/72*(animationInfo.max-animationInfo.min)
    });
    event.preventDefault();
  };
  const handleBezierPointerMove=(event:React.PointerEvent<SVGSVGElement>)=>{
    if(!draggingBezier)return;
    const rect=event.currentTarget.getBoundingClientRect();
    if(!rect.width||!rect.height)return;
    const x=clampKeyframe((event.clientX-rect.left)/rect.width*100,10,90);
    const y=clampKeyframe((event.clientY-rect.top)/rect.height*100,10,90);
    const controlX=clampKeyframe((x-10)/80,0,1);
    const controlY=clampKeyframe((90-y)/80,0,1);
    updateBezierControl(draggingBezier.index,draggingBezier.handle,"x",controlX);
    updateBezierControl(draggingBezier.index,draggingBezier.handle,"y",controlY);
    event.preventDefault();
  };
  return (
    <div data-react-clip-inspector="true">
      <div className="fs-inspector-description">
        <strong>{clip.id}</strong><br />{clip.tag}<br />{attrs.src || ""}
      </div>
      <div className="fs-inspector-section">
        <label className="fs-inspector-field">
          时间位置（秒）
          <input id="fs-field-at" type="number" min={0} step={1 / Math.max(1, Math.min(60, clip.fps || 30))} value={form.at} disabled={readOnly}
            onChange={(event) => update("at", event.currentTarget.value)} />
        </label>
        {hasMediaControls && (
          <>
            <label className="fs-inspector-field">
              速度
              <input id="fs-field-speed" type="number" min={0.25} max={4} step={0.05} value={form.speed} disabled={readOnly}
                onChange={(event) => update("speed", event.currentTarget.value)} />
            </label>
            <div className="fs-speed-presets">
              <span>预设倍率</span>
              <div>{[0.5, 1, 1.5, 2, 4].map((value) => (
                <button type="button" key={value}
                  className={"fs-speed-chip" + (Math.abs(speed - value) < 0.001 ? " fs-speed-chip-active" : "")}
                  data-speed-preset={value} title={"设置速度为 " + value + " 倍"} disabled={readOnly}
                  onClick={() => update("speed", String(value))}
                >{value}×</button>
              ))}</div>
            </div>
            <label className="fs-inspector-field">
              音量
              <input id="fs-field-volume" type="number" min={0} max={2} step={0.05} value={form.volume} disabled={readOnly}
                onChange={(event) => update("volume", event.currentTarget.value)} />
            </label>
            <label className="fs-inspector-field">
              静音
              <select id="fs-field-muted" value={form.muted} disabled={readOnly}
                onChange={(event) => update("muted", event.currentTarget.value === "true" ? "true" : "false")}>
                <option value="false">否</option><option value="true">是</option>
              </select>
            </label>
          </>
        )}
        {clip.duration != null && (
          <>
            <label className="fs-inspector-field">
              淡入（秒）
              <input id="fs-field-fade-in" type="number" min={0} max={60} step={0.05} value={form.fadeIn} disabled={readOnly}
                onChange={(event) => update("fadeIn", event.currentTarget.value)} />
            </label>
            <label className="fs-inspector-field">
              淡出（秒）
              <input id="fs-field-fade-out" type="number" min={0} max={60} step={0.05} value={form.fadeOut} disabled={readOnly}
                onChange={(event) => update("fadeOut", event.currentTarget.value)} />
            </label>
          </>
        )}
        <label className="fs-inspector-field">
          片段标题
          <input id="fs-field-title" type="text" maxLength={200} value={form.title} disabled={readOnly}
            onChange={(event) => update("title", event.currentTarget.value)} />
        </label>
      </div>
      {clip.tag!=="audio" ? <section className="fs-inspector-section fs-keyframe-editor" data-film-keyframe-editor="true">
        <h4>视觉关键帧</h4>
        <p className="fs-inspector-description">关键帧使用片段相对时间。拖动曲线节点调整时间和值；缓动由右侧节点决定。修改后点击“应用片段属性”。</p>
        <input type="hidden" id="fs-field-keyframes" readOnly value={keyframeDirty?JSON.stringify(keyframeChannels):(clip.attrs["data-film-keyframes"]||"")} />
        <label className="fs-inspector-field">动画属性
          <select id="fs-keyframe-property" value={animationProperty} disabled={readOnly||clip.duration==null}
            onChange={event=>{const property=event.currentTarget.value as FilmKeyframeProperty;setAnimationProperty(property);setNewKeyframeValue(String(keyframeInfo(property).initial));setKeyframeFeedback("");}}>
            {FILM_KEYFRAME_PROPERTIES.map(item=><option key={item.value} value={item.value}>{item.label}</option>)}
          </select>
        </label>
        <div className="fs-keyframe-range">{animationInfo.min} 至 {animationInfo.max} · {clip.duration==null?"时长未知":clip.duration.toFixed(3)+" 秒"}</div>
        {clip.duration!=null&&clip.duration>0 ? <>
          <svg className="fs-keyframe-graph" viewBox="0 0 360 96" preserveAspectRatio="none" role="img"
            aria-label={animationInfo.label+" 关键帧曲线；拖动圆点修改"}
            onPointerMove={handleKeyframePointerMove} onPointerUp={()=>setDraggingKeyframe(null)} onPointerCancel={()=>setDraggingKeyframe(null)}>
            {[0,.25,.5,.75,1].map(ratio=><line key={"h"+ratio} x1="8" x2="352" y1={8+ratio*72} y2={8+ratio*72} className="fs-keyframe-grid"/>)}
            {[0,.25,.5,.75,1].map(ratio=><line key={"v"+ratio} x1={8+ratio*344} x2={8+ratio*344} y1="8" y2="80" className="fs-keyframe-grid"/>)}
            {typeof clip.playhead==="number"&&<line x1={8+clampKeyframe((clip.playhead-clip.at)/clip.duration,0,1)*344} x2={8+clampKeyframe((clip.playhead-clip.at)/clip.duration,0,1)*344} y1="4" y2="84" className="fs-keyframe-playhead"/>}
            {activeChannel&&activeChannel.keyframes.length>1&&<path d={keyframePath(activeChannel,clip.duration,animationInfo.min,animationInfo.max)} className="fs-keyframe-curve" fill="none"/>}
            {activeChannel?.keyframes.map((point,index)=><circle key={point.at}
              cx={8+clampKeyframe(point.at/clipDuration,0,1)*344}
              cy={8+(1-(clampKeyframe(point.value,animationInfo.min,animationInfo.max)-animationInfo.min)/Math.max(1e-9,animationInfo.max-animationInfo.min))*72}
              r="4.5" className={"fs-keyframe-point"+(draggingKeyframe===index?" is-dragging":"")}
              tabIndex={readOnly?-1:0} role="slider" aria-label={"关键帧 "+(index+1)}
              onPointerDown={event=>{if(readOnly)return;event.stopPropagation();event.currentTarget.setPointerCapture(event.pointerId);setDraggingKeyframe(index);}}
              onKeyDown={event=>{
                if(readOnly)return;const delta=event.shiftKey?10:1;
                if(event.key==="ArrowLeft"||event.key==="ArrowRight"){event.preventDefault();updateKeyframePoint(index,{at:point.at+(event.key==="ArrowRight"?delta:-delta)*frameStep});}
                else if(event.key==="ArrowUp"||event.key==="ArrowDown"){event.preventDefault();updateKeyframePoint(index,{value:point.value+(event.key==="ArrowUp"?1:-1)*(animationInfo.max-animationInfo.min)/100});}
              }}/>)}
          </svg>
          <div className="fs-keyframe-labels"><span>0.000s</span><span>{clip.duration.toFixed(3)}s</span></div>
          <div className="fs-keyframe-list">
            {activeChannel?activeChannel.keyframes.map((point,index)=>{
              const bezier=bezierCurveFor(point.easing);
              const pointEasingValue=typeof point.easing==="string"?point.easing:(bezier?"bezier":"window");
              const pointWindow=typeof point.easing!=="string"&&point.easing.type==="window";
              return <div className="fs-keyframe-point-editor" key={point.at}>
                <div className="fs-keyframe-row">
                  <label>时间<input type="number" min={0} max={clipDuration} step={frameStep} value={point.at.toFixed(3)} disabled={readOnly} aria-label={"关键帧 "+(index+1)+" 时间"}
                    onChange={event=>{const value=Number(event.currentTarget.value);if(Number.isFinite(value))updateKeyframePoint(index,{at:value});}}/></label>
                  <label>数值<input type="number" min={animationInfo.min} max={animationInfo.max} step="0.01" value={point.value} disabled={readOnly} aria-label={"关键帧 "+(index+1)+" 数值"}
                    onChange={event=>{const value=Number(event.currentTarget.value);if(Number.isFinite(value))updateKeyframePoint(index,{value});}}/></label>
                  <label>缓动<select value={pointEasingValue} disabled={readOnly} aria-label={"关键帧 "+(index+1)+" 缓动"}
                    onChange={event=>{
                      const selected=event.currentTarget.value;
                      if(selected==="bezier"){
                        if(!bezier)updateKeyframePoint(index,{easing:{type:"bezier",...DEFAULT_FILM_BEZIER}});
                      }else if(selected!=="window")updateKeyframePoint(index,{easing:selected as FilmKeyframePreset});
                    }}>
                    <option value="linear">线性</option><option value="ease_in">缓入</option><option value="ease_out">缓出</option><option value="ease_in_out">缓入缓出</option>
                    <option value="bezier">自定义贝塞尔</option>
                    <option value="window" disabled>裁剪保真的曲线分段</option>
                  </select></label>
                  <button type="button" className="fs-btn" aria-label={"删除关键帧 "+(index+1)} disabled={readOnly} onClick={()=>removeKeyframe(index)}>删除</button>
                </div>
                {bezier?<div className="fs-keyframe-bezier-editor" data-bezier-editor={"keyframe-"+(index+1)}>
                  <svg className="fs-keyframe-bezier-graph" viewBox="0 0 100 100" preserveAspectRatio="none" role="img"
                    aria-label={"关键帧 "+(index+1)+" 贝塞尔控制柄"}
                    onPointerMove={handleBezierPointerMove} onPointerUp={()=>setDraggingBezier(null)} onPointerCancel={()=>setDraggingBezier(null)}>
                    <line x1="10" y1="90" x2="90" y2="10" className="fs-keyframe-bezier-diagonal"/>
                    <line x1="10" y1="90" x2={10+80*bezier.x1} y2={90-80*bezier.y1} className="fs-keyframe-bezier-handle-line"/>
                    <line x1="90" y1="10" x2={10+80*bezier.x2} y2={90-80*bezier.y2} className="fs-keyframe-bezier-handle-line"/>
                    <path d={"M 10 90 C "+(10+80*bezier.x1)+" "+(90-80*bezier.y1)+", "+(10+80*bezier.x2)+" "+(90-80*bezier.y2)+", 90 10"} className="fs-keyframe-bezier-curve"/>
                    <circle data-bezier-handle="p1" cx={10+80*bezier.x1} cy={90-80*bezier.y1} r="4.5" className="fs-keyframe-bezier-handle"
                      tabIndex={readOnly?-1:0} role="slider" aria-label={"关键帧 "+(index+1)+" 控制柄 1"}
                      onPointerDown={event=>{if(readOnly)return;event.preventDefault();event.stopPropagation();event.currentTarget.setPointerCapture(event.pointerId);setDraggingBezier({index,handle:"p1"});}}/>
                    <circle data-bezier-handle="p2" cx={10+80*bezier.x2} cy={90-80*bezier.y2} r="4.5" className="fs-keyframe-bezier-handle"
                      tabIndex={readOnly?-1:0} role="slider" aria-label={"关键帧 "+(index+1)+" 控制柄 2"}
                      onPointerDown={event=>{if(readOnly)return;event.preventDefault();event.stopPropagation();event.currentTarget.setPointerCapture(event.pointerId);setDraggingBezier({index,handle:"p2"});}}/>
                  </svg>
                  <div className="fs-keyframe-bezier-values">
                    {(["x1","y1","x2","y2"] as const).map(field=><label key={field}>{field}
                      <input type="number" min="0" max="1" step="0.01" value={bezier[field]} disabled={readOnly}
                        aria-label={"关键帧 "+(index+1)+" "+field}
                        onChange={event=>{const value=Number(event.currentTarget.value);if(Number.isFinite(value))updateBezierControl(index,field==="x1"||field==="y1"?"p1":"p2",field==="x1"||field==="x2"?"x":"y",value);}}/>
                    </label>)}
                  </div>
                  {pointWindow?<span className="fs-keyframe-bezier-note">此曲线保留了分割/修剪前的窗口区间；调整控制柄会继续保留该区间。</span>:null}
                </div>:null}
              </div>;
            }):<div className="fs-empty">该属性还没有关键帧。</div>}
          </div>
          <div className="fs-keyframe-add">
            <label className="fs-inspector-field">时间（相对秒）<input id="fs-keyframe-new-at" type="number" min={0} max={clip.duration} step={frameStep} value={newKeyframeAt} disabled={readOnly} onChange={event=>setNewKeyframeAt(event.currentTarget.value)}/></label>
            <label className="fs-inspector-field">数值<input id="fs-keyframe-new-value" type="number" min={animationInfo.min} max={animationInfo.max} step="0.01" value={newKeyframeValue} disabled={readOnly} onChange={event=>setNewKeyframeValue(event.currentTarget.value)}/></label>
            <label className="fs-inspector-field">缓动<select id="fs-keyframe-new-easing" value={newKeyframeEasing} disabled={readOnly} onChange={event=>setNewKeyframeEasing(event.currentTarget.value as FilmKeyframePreset|"bezier")}>
              <option value="linear">线性</option><option value="ease_in">缓入</option><option value="ease_out">缓出</option><option value="ease_in_out">缓入缓出</option><option value="bezier">自定义贝塞尔</option>
            </select></label>
            {newKeyframeEasing==="bezier"?<div className="fs-keyframe-bezier-values fs-keyframe-new-bezier">
              {(["x1","y1","x2","y2"] as const).map(field=><label key={field}>{field}
                <input type="number" min="0" max="1" step="0.01" value={newBezierCurve[field]} disabled={readOnly} aria-label={"新关键帧 "+field}
                  onChange={event=>{const value=Number(event.currentTarget.value);if(Number.isFinite(value))updateNewBezierControl(field==="x1"||field==="y1"?"p1":"p2",field==="x1"||field==="x2"?"x":"y",value);}}/>
              </label>)}
            </div>:null}
            <button type="button" className="fs-btn" id="fs-keyframe-add" disabled={readOnly} onClick={addKeyframe}>添加关键帧</button>
          </div>
        </>:<div className="fs-empty">该片段没有可验证的明确时长，暂不能编辑动画关键帧。</div>}
        {keyframeFeedback?<div className="fs-alert fs-visible fs-error" role="status">{keyframeFeedback}</div>:null}
        <button type="button" className="fs-btn" id="fs-keyframe-clear" disabled={readOnly||!keyframeChannels.length}
          onClick={()=>{setKeyframeChannels([]);setKeyframeDirty(true);setKeyframeFeedback("");}}>清空全部视觉关键帧</button>
      </section> : null}
      <div className="fs-inspector-description fs-inspector-section">
        更改经统一编辑 API 写入 film.html。锁定轨道会拒绝修改；同轨片段不可重叠。
      </div>
      {trackLocked ? <div className="fs-alert fs-visible fs-error" role="status">所在轨道已锁定；解锁轨道后才能编辑该片段。</div> : clipLocked ? <div className="fs-alert fs-visible fs-error" role="status">该片段来自已锁定图层，只读保留其原始时间线位置与画面。</div> : null}
      {feedback ? <div id="fs-clip-inspector-feedback" className="fs-alert fs-visible fs-error" role="status">{feedback}</div> : null}
      <button type="button" id="fs-apply-clip-inspector" className="fs-btn fs-primary" disabled={readOnly}
        onClick={() => {
          const result = buildClipInspectorOperations(clip, {...form});
          if (!result.ok) { setFeedback(result.message); return; }
          const operations=[...result.ops];
          const existingChannels=parseFilmKeyframes(String(attrs["data-film-keyframes"]||""));
          if(JSON.stringify(existingChannels)!==JSON.stringify(keyframeChannels)){
            operations.push({op:"set",clip:clip.id,field:"data-film-keyframes",
              value:keyframeChannels.length?JSON.stringify(keyframeChannels.map(channel=>({
                property:channel.property,keyframes:channel.keyframes.slice().sort((a,b)=>a.at-b.at)
                  .map(point=>({at:Number(point.at.toFixed(6)),value:Number(point.value.toFixed(6)),easing:point.easing}))
              }))):null});
          }
          if (!operations.length) { setFeedback("属性没有变化。"); return; }
          const commit = window.filmStudioCommitInspectorOperations;
          if (!commit) { setFeedback("Studio 编辑提交接口不可用。"); return; }
          setFeedback("");
          void Promise.resolve(commit(operations)).catch((error: unknown) => {
            setFeedback("属性提交失败：" + String(error instanceof Error ? error.message : error));
          });
        }}
      >应用片段属性</button>
      <button type="button" id="fs-toggle-clip-lock" className="fs-btn" disabled={trackLocked}
        onClick={() => { void window.filmStudioSetClipLock?.(clip.id, !clipLocked); }}
      >{clipLocked ? "解锁片段" : "锁定片段"}</button>
    </div>
  );
};

const StudioAiDirector: React.FC<{model: FilmStudioAiDirectorModel}> = ({model}) => {
  const messages = model.messages.length ? model.messages : [{
    role: "assistant",
    text: "可以直接指挥当前影片，例如：“第二镜节奏太慢，缩短一秒，音乐不变，字幕继续跟着旁白走”。AI 修改会先验证目标、时间和音画依赖，再反馈操作结果。"
  }];
  const proposal = model.proposal;
  const proposalIsStale = !!proposal &&
    (!model.currentRevision || proposal.baseRevision !== model.currentRevision);
  return (
    <>
      {proposal ? (
        <div className="fs-proposal-panel" id="fs-proposal-panel" data-react-ai-proposal="true">
          <strong id="fs-proposal-summary">{proposal.summary || "AI 候选修改"}</strong>
          <p id="fs-proposal-detail">{proposal.operationCount} 项操作 · 基于 revision {proposal.baseRevision} · 尚未写入正式文档</p>
          {proposalIsStale ? (
            <p id="fs-proposal-stale-warning" className="fs-proposal-stale-warning" role="alert">
              当前影片 revision 已变化（当前：{model.currentRevision || "未知"}）。此提案基于旧 revision {proposal.baseRevision}，不能预览或应用。请重新生成提案，或拒绝此提案。
            </p>
          ) : null}
          <details className="fs-proposal-operation-list" data-react-proposal-details="true">
            <summary>查看具体改动（{proposal.operationCount} 项）</summary>
            <ol>
              {proposal.operationPreviews.map((item) => (
                <li key={item.id}>
                  <strong>{item.action}</strong> · {item.target}
                  <p>{item.detail}</p>
                  {item.before ? <p className="fs-proposal-before">修改前：{item.before}</p> : null}
                  {item.after ? <p className="fs-proposal-after">修改后：{item.after}</p> : null}
                </li>
              ))}
            </ol>
            {proposal.truncatedCount ? <p>另有 {proposal.truncatedCount} 项操作未在此展开。</p> : null}
            {proposal.affectedClips.length ? <p className="fs-proposal-affected">影响对象：{proposal.affectedClips.join("；")}</p> : null}
            {proposal.affectedTruncatedCount ? <p>另有 {proposal.affectedTruncatedCount} 个受影响对象未列出。</p> : null}
          </details>
          <div className="fs-proposal-actions">
            <button className="fs-btn" type="button" data-film-action="preview-proposal"
              disabled={proposalIsStale} title={proposalIsStale ? "提案基于旧 revision，请重新生成" : undefined}>重新预览</button>
            <button className="fs-btn" type="button" data-film-action="reject-proposal">拒绝</button>
            <button className="fs-btn fs-primary" type="button" data-film-action="apply-proposal"
              disabled={proposalIsStale} title={proposalIsStale ? "提案基于旧 revision，请重新生成" : undefined}>确认应用</button>
          </div>
        </div>
      ) : null}
      <div id="fs-chat-log" className="fs-chat-log" aria-live="polite" data-react-ai-chat-log="true">
        {messages.map((message, index) => (
          <div
            className={"fs-chat-message " + (message.role === "user" ? "fs-user" : "fs-assistant")}
            key={index}
          >{message.text}</div>
        ))}
      </div>
      <div className="fs-chat-input-row">
        <input
          id="fs-chat-input"
          type="text"
          placeholder="描述你要修改的影片……"
          aria-label="给 AI 导演的修改指令"
          onKeyDown={(event) => { if (event.key === "Enter") void window.filmStudioAsk?.(); }}
        />
        <button className="fs-btn" type="button" data-film-action="cancel-ai" title="停止本轮生成；生成中再次发送新指令会中止并按新方向重新规划">停止 / 转向</button>
        <button className="fs-btn fs-primary" type="button" data-film-action="ask">发送指令</button>
      </div>
    </>
  );
};

const StudioTimelineContextMenu: React.FC<{model: FilmTimelineContextMenu}> = ({model}) => {
  const left = Math.max(8, Math.min(model.x, window.innerWidth - 204));
  const top = Math.max(8, Math.min(model.y, window.innerHeight - 292));
  const action = (name: string) => (event: React.MouseEvent<HTMLButtonElement>) => {
    event.preventDefault();
    event.stopPropagation();
    window.filmStudioContextMenuAction?.(name, model.clipId);
  };
  return createPortal(
    <div className="fs-context-menu" role="menu" data-react-context-menu="true"
      style={{position:"fixed",left,top}}
      onContextMenu={event => { event.preventDefault(); event.stopPropagation(); }}
      onPointerDown={event => event.stopPropagation()}>
      <button type="button" role="menuitem" data-context-action="copy" onClick={action("copy")}>复制所选片段</button>
      <button type="button" role="menuitem" data-context-action="duplicate" onClick={action("duplicate")}>重复所选片段</button>
      <button type="button" role="menuitem" data-context-action="paste" onClick={action("paste")}>粘贴到播放头</button>
      <div className="fs-context-separator" />
      <button type="button" role="menuitem" data-context-action="split" onClick={action("split")}>在播放头切分</button>
      <button type="button" role="menuitem" data-context-action="delete" onClick={action("delete")} title="只移除所选片段，后续内容时间不变">删除并保留空隙</button>
      <button type="button" role="menuitem" data-context-action="ripple" onClick={action("ripple")} title="删除所选时间段，并收拢所有受影响轨道">波纹删除（收拢时间线）</button>
      <div className="fs-context-separator" />
      <button type="button" role="menuitem" data-context-action="inspector" onClick={action("inspector")}>打开属性检查器</button>
    </div>,
    document.body
  );
};

const StudioToolbar: React.FC = () => {
  const rendering = useFilmStoreField("rendering");
  return (
    <>
      <button className="fs-btn" type="button" data-film-action="undo">撤销</button>
      <button className="fs-btn" type="button" data-film-action="redo">重做</button>
      <button className="fs-btn" type="button" data-film-action="reload">重新载入</button>
      <button className="fs-btn fs-primary" type="button" data-film-action="render"
        disabled={rendering} aria-busy={rendering}
        title={rendering ? "导出任务进行中" : undefined}>导出 MP4</button>
      <button className="fs-btn" type="button" data-film-action="close" title="退出全屏 Studio，不切回旧编辑状态">退出 Studio</button>
    </>
  );
};

type AudioWaveformCanvasProps = {
  clipId: string;
  waveformKey: string;
  duration: number;
  pixelsPerSecond: number;
  sourceStart: number;
  sourceEnd: number | null;
  source: FilmStudioTimelineWaveformSource | undefined;
};

const AudioWaveformCanvas: React.FC<AudioWaveformCanvasProps> = ({
  clipId,waveformKey,duration,pixelsPerSecond,sourceStart,sourceEnd,source,
}) => {
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  useLayoutEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    delete canvas.dataset.reactWaveformPainted;
    const width = filmTimelineMath.clipWidth(duration || 1,pixelsPerSecond,16,1,1800,true);
    const height = Math.max(18,Math.min(40,canvas.parentElement ? canvas.parentElement.clientHeight - 6 : 30));
    canvas.width = width;
    canvas.height = height;
    const ctx = canvas.getContext("2d");
    if (!ctx) return;
    ctx.clearRect(0,0,width,height);
    if (!source || !Number.isFinite(source.duration) || source.duration <= 0 || !source.peaks.length) {
      canvas.title = "音频波形等待解码";
      return;
    }
    const start = Math.max(0,Math.min(source.duration,sourceStart || 0));
    const end = sourceEnd == null || !Number.isFinite(sourceEnd)
      ? source.duration
      : Math.max(start,Math.min(source.duration,sourceEnd));
    const first = Math.max(0,Math.min(source.peaks.length - 1,Math.floor(start / source.duration * source.peaks.length)));
    const last = Math.max(first + 1,Math.min(source.peaks.length,Math.ceil(end / source.duration * source.peaks.length)));
    const count = Math.max(1,last - first), mid = height / 2;
    ctx.fillStyle = "rgba(211,250,241,.72)";
    for (let x = 0; x < width; x++) {
      const index = first + Math.min(count - 1,Math.floor(x / width * count));
      const amplitude = (source.peaks[index] || 0) / 255;
      const bar = Math.max(1,amplitude * (height - 4));
      ctx.fillRect(x,mid - bar / 2,1,bar);
    }
    canvas.title = "素材波形 · 源区间 " + start.toFixed(2) + "–" + end.toFixed(2) + " 秒";
    canvas.dataset.reactWaveformPainted = "true";
  },[clipId,waveformKey,duration,pixelsPerSecond,sourceStart,sourceEnd,source]);
  return <canvas className="fs-clip-waveform" data-waveform-clip={clipId} data-waveform-key={waveformKey}
    aria-label="音频波形" ref={canvasRef} />;
};

const drawVolumeEnvelopeCanvas = (
  canvas: HTMLCanvasElement,
  duration: number,
  pixelsPerSecond: number,
  points: readonly FilmStudioTimelineEnvelopePoint[],
): void => {
  if (!Number.isFinite(duration) || duration <= 0) return;
  const width = filmTimelineMath.clipWidth(duration,pixelsPerSecond,24,1,1800,true);
  const height = Math.max(24,canvas.parentElement ? canvas.parentElement.clientHeight - 2 : 40);
  canvas.width = width;
  canvas.height = height;
  const ctx = canvas.getContext("2d");
  if (!ctx) return;
  ctx.clearRect(0,0,width,height);
  const xy = (point: FilmStudioTimelineEnvelopePoint) => ({
    x:Math.max(0,Math.min(width,point.t / duration * width)),
    y:height - 4 - Math.max(0,Math.min(1,point.v)) * (height - 8),
  });
  const plotted = points
    .filter(point => Number.isFinite(point.t) && Number.isFinite(point.v) && point.t >= 0 && point.t <= duration)
    .slice()
    .sort((a,b) => a.t - b.t);
  if (!plotted.length) plotted.push({t:0,v:1},{t:duration,v:1});
  const first = plotted[0], last = plotted[plotted.length - 1];
  if (first.t > 0) plotted.unshift({t:0,v:first.v});
  if (last.t < duration) plotted.push({t:duration,v:last.v});
  ctx.strokeStyle = "rgba(255,220,134,.95)";
  ctx.lineWidth = 1.5;
  ctx.beginPath();
  plotted.forEach((point,index) => {
    const p = xy(point);
    if (index === 0) ctx.moveTo(p.x,p.y);
    else ctx.lineTo(p.x,p.y);
  });
  ctx.stroke();
  ctx.fillStyle = "#fff0b3";
  ctx.strokeStyle = "#4a3620";
  ctx.lineWidth = 1;
  points.filter(point => point.t >= 0 && point.t <= duration).forEach(point => {
    const p = xy(point);
    ctx.beginPath();
    ctx.arc(p.x,p.y,3,0,Math.PI * 2);
    ctx.fill();
    ctx.stroke();
  });
  canvas.title = "音量包络倍率 0–1 · " + points.length + " 个关键帧。拖动曲线添加/调整，Shift 或右键删除内部点。";
  canvas.dataset.reactEnvelopePainted = "true";
};

type VolumeEnvelopeCanvasProps = {
  clipId: string;
  duration: number;
  pixelsPerSecond: number;
  points: FilmStudioTimelineEnvelopePoint[];
  preview: FilmStudioTimelineEnvelopePreview;
  onContextMenu?: React.MouseEventHandler<HTMLCanvasElement>;
  onClick?: React.MouseEventHandler<HTMLCanvasElement>;
  onPointerDown?: React.PointerEventHandler<HTMLCanvasElement>;
  onPointerMove?: React.PointerEventHandler<HTMLCanvasElement>;
  onPointerUp?: React.PointerEventHandler<HTMLCanvasElement>;
  onPointerCancel?: React.PointerEventHandler<HTMLCanvasElement>;
};

const VolumeEnvelopeCanvas: React.FC<VolumeEnvelopeCanvasProps> = ({
  clipId,duration,pixelsPerSecond,points,preview,
  onContextMenu,onClick,onPointerDown,onPointerMove,onPointerUp,onPointerCancel,
}) => {
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const isPreview = preview?.clipId === clipId;
  const visiblePoints = isPreview ? preview.points : points;
  useLayoutEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    delete canvas.dataset.reactEnvelopePainted;
    drawVolumeEnvelopeCanvas(canvas,duration,pixelsPerSecond,visiblePoints);
  },[clipId,duration,pixelsPerSecond,visiblePoints]);
  return (
    <canvas
      ref={canvasRef}
      className="fs-clip-envelope"
      data-envelope-clip={clipId}
      data-react-envelope-source={isPreview ? "preview" : "document"}
      aria-label="音量包络：拖动添加或调整关键帧；Shift 或右键删除内部点"
      onContextMenu={onContextMenu}
      onClick={onClick}
      onPointerDown={onPointerDown}
      onPointerMove={onPointerMove}
      onPointerUp={onPointerUp}
      onPointerCancel={onPointerCancel}
    />
  );
};

const TimelineSurface: React.FC<{model: FilmTimelineSurfaceModel}> = ({model}) => {
  const gesturePreview = useFilmStoreField("timelineGesturePreview");
  const snapGuide = useFilmStoreField("timelineSnapGuide");
  const dropTarget = useFilmStoreField("timelineDropTarget");
  const activeDragIds = useFilmStoreField("timelineActiveDragIds");
  const envelopePreview = useFilmStoreField("timelineEnvelopePreview");
  const waveformSources = useFilmStoreField("timelineWaveformSources");
  const envelopeMode = useFilmStoreField("envelopeMode");
  const slideMode = useFilmStoreField("slideMode");
  const slipMode = useFilmStoreField("slipMode");
  const selectedIdsVersion=useFilmStoreField("selectedIdsVersion");
  const playhead=useFilmStoreField("playhead");
  const duration=useFilmStoreField("duration");
  const selectedIds=filmStore.getSnapshot().selectedIds;
  // The version is the scalar subscription token; the array proxy is stable by design.
  void selectedIdsVersion;
  const previewForClip = (clipId: string): FilmTimelineGesturePreviewGeometry | null => {
    const entry = gesturePreview?.[clipId];
    if (typeof entry === "number") return {left:entry};
    return entry || null;
  };
  const previewForTrack = (trackId: string): FilmStudioTimelineGesturePreviewGeometry | null => {
    const entry = gesturePreview?.["__proposal_track_removed__:" + trackId];
    return typeof entry === "number" ? null : (entry || null);
  };
  const proposalAddedTracks: Array<{id:string;preview:FilmStudioTimelineGesturePreviewGeometry}> = [];
  const crossTrackGhostsByTrack = new Map<string, FilmTimelineSurfaceClip[]>();
  const proposalAdditionsByTrack = new Map<string, Array<{id:string; preview:FilmStudioTimelineGesturePreviewGeometry}>>();
  for (const [previewKey,rawEntry] of Object.entries(gesturePreview || {})) {
    if (typeof rawEntry === "number" || !rawEntry) continue;
    if (rawEntry.previewTrack === true && rawEntry.added === true && rawEntry.targetTrackId) {
      proposalAddedTracks.push({id:rawEntry.targetTrackId,preview:rawEntry});
    }
    if (rawEntry.added !== true) continue;
    const id = previewKey.startsWith("__proposal_added__:") ? previewKey.slice("__proposal_added__:".length) : previewKey;
    const targetTrackId = rawEntry.targetTrackId;
    if (!id || !targetTrackId || rawEntry.at == null) continue;
    const additions = proposalAdditionsByTrack.get(targetTrackId) || [];
    additions.push({id,preview:rawEntry});
    proposalAdditionsByTrack.set(targetTrackId,additions);
  }
  for (const sourceTrack of model.tracks) {
    for (const clip of sourceTrack.clips) {
      const targetTrackId = previewForClip(clip.id)?.targetTrackId;
      if (!targetTrackId || targetTrackId === clip.trackId) continue;
      const ghosts = crossTrackGhostsByTrack.get(targetTrackId) || [];
      ghosts.push(clip);
      crossTrackGhostsByTrack.set(targetTrackId, ghosts);
    }
  }
  proposalAddedTracks.sort((left,right) =>
    Number(left.preview.trackIndex ?? model.tracks.length) - Number(right.preview.trackIndex ?? model.tracks.length));
  const proposalTracksBeforeIndex = new Map<number, Array<{id:string;preview:FilmStudioTimelineGesturePreviewGeometry}>>();
  proposalAddedTracks.forEach(item => {
    const rawIndex = item.preview.trackIndex;
    const index = Number.isInteger(rawIndex) ? Math.max(0,Math.min(model.tracks.length,Number(rawIndex))) : model.tracks.length;
    const rows = proposalTracksBeforeIndex.get(index) || [];
    rows.push(item);
    proposalTracksBeforeIndex.set(index,rows);
  });
  const renderProposalTrack = ({id,preview}: {id:string;preview:FilmStudioTimelineGesturePreviewGeometry}) => {
    const additions = proposalAdditionsByTrack.get(id) || [];
    const movedGhosts = crossTrackGhostsByTrack.get(id) || [];
    return (
      <div
        className="fs-track fs-track-proposal-added"
        data-track={id}
        data-proposal-track-added={id}
        key={"proposal-track-added-" + id}
        aria-hidden="true"
        style={{opacity:0.92,outline:"2px dashed #65a7dc",outlineOffset:"-2px",background:"rgba(80,145,200,.08)"}}
      >
        <div className="fs-track-head">
          <span className="fs-track-label" title={String(preview.trackLabel || id)}>
            {String(preview.trackLabel || id)} · 候选新增轨道
          </span>
          <span className="fs-track-controls"><span className="fs-proposal-track-state">候选</span></span>
        </div>
        <div className="fs-track-body" style={{width:model.laneWidth}}>
          {movedGhosts.map(clip => {
            const clipPreview = previewForClip(clip.id);
            if (!clipPreview) return null;
            return (
              <div
                className="fs-clip fs-clip-gesture-ghost"
                key={"proposal-track-moved-" + clip.id}
                data-gesture-ghost-for={clip.id}
                data-gesture-target-track={id}
                data-kind={clip.tag}
                aria-hidden="true"
                title={"候选移动：" + clip.title}
                style={{
                  left:clipPreview.at != null ? filmTimelineMath.clipLeft(Number(clipPreview.at),model.pixelsPerSecond)
                    : clipPreview.left ?? filmTimelineMath.clipLeft(clip.at,model.pixelsPerSecond),
                  width:clipPreview.duration !== undefined
                    ? filmTimelineMath.clipWidth(clipPreview.duration ?? null,model.pixelsPerSecond,24,2,0,false)
                    : clipPreview.width ?? filmTimelineMath.clipWidth(clip.duration,model.pixelsPerSecond,24,2),
                  opacity:0.88,outline:"2px dashed #65a7dc",outlineOffset:"-2px",
                  background:"rgba(80,145,200,.2)",
                }}
              >
                <span className="fs-clip-title">{clip.title} · 候选移动</span>
                <span className="fs-clip-subtitle">{formatFilmTime(Number(clipPreview.at ?? clip.at))} · {clipPreview.duration == null ? "时长未知" : Number(clipPreview.duration ?? clip.duration).toFixed(2)+"s"}</span>
              </div>
            );
          })}
          {additions.map(({id:clipId,preview:clipPreview}) => (
            <div
              className="fs-clip fs-clip-gesture-ghost fs-clip-proposal-added"
              key={"proposal-track-clip-" + clipId}
              data-proposal-added={clipId}
              data-gesture-target-track={id}
              data-kind={clipPreview.previewTag || "video"}
              aria-hidden="true"
              title={"候选新增：" + String(clipPreview.previewTitle || clipId)}
              style={{
                left:filmTimelineMath.clipLeft(Number(clipPreview.at || 0),model.pixelsPerSecond),
                width:filmTimelineMath.clipWidth(clipPreview.duration ?? null,model.pixelsPerSecond,24,2,0,false),
                opacity:0.88,outline:"2px dashed #65a7dc",outlineOffset:"-2px",
                background:"rgba(80,145,200,.2)",
              }}
            >
              <span className="fs-clip-title">{String(clipPreview.previewTitle || clipId)} · 候选新增</span>
              <span className="fs-clip-subtitle">{formatFilmTime(Number(clipPreview.at || 0))} · {clipPreview.duration == null ? "时长未知" : Number(clipPreview.duration).toFixed(2)+"s"}</span>
            </div>
          ))}
          {additions.length === 0 && movedGhosts.length === 0 ? <span className="fs-empty fs-proposal-empty-track">候选新增空轨道</span> : null}
        </div>
      </div>
    );
  };
  const activePointerId = useRef<number | null>(null);
  const activeEnvelopePointerId = useRef<number | null>(null);
  const activeScrubPointerId = useRef<number | null>(null);
  const pendingScrubPointer = useRef<{
    pointerId: number; startX: number; startY: number; startedOnTick: boolean; tickAt: number | null;
  } | null>(null);
  const seekFromRulerPointer = (clientX: number, body: HTMLDivElement) => {
    const rect = body.getBoundingClientRect();
    const localX = clientX - rect.left;
    const seconds = Math.max(0, Math.min(duration, localX / Math.max(0.000001, model.pixelsPerSecond)));
    window.filmStudioSeek?.(seconds);
  };
  useLayoutEffect(() => {
    const ownsPointer = (pointerId:number) => activePointerId.current === pointerId;
    window.filmStudioOwnsClipGesturePointer = ownsPointer;
    return () => {
      if (window.filmStudioOwnsClipGesturePointer === ownsPointer) {
        window.filmStudioOwnsClipGesturePointer = undefined;
      }
    };
  }, []);
  useLayoutEffect(() => {
    const ownsEnvelopePointer = (pointerId:number) => activeEnvelopePointerId.current === pointerId;
    window.filmStudioOwnsEnvelopeGesturePointer = ownsEnvelopePointer;
    return () => {
      if (window.filmStudioOwnsEnvelopeGesturePointer === ownsEnvelopePointer) {
        window.filmStudioOwnsEnvelopeGesturePointer = undefined;
      }
    };
  }, []);
  useEffect(() => {
    const scroll = document.getElementById("fs-timeline-scroll");
    if (!scroll) return;
    const onWheel = (event: WheelEvent) => {
      if (!(event.ctrlKey || event.metaKey)) return;
      event.preventDefault();
      window.filmStudioZoom?.(event.deltaY < 0 ? 1 : -1, event.clientX);
    };
    scroll.addEventListener("wheel", onWheel, {passive:false});
    scroll.dataset.reactZoomReady = "true";
    return () => {
      scroll.removeEventListener("wheel", onWheel);
      delete scroll.dataset.reactZoomReady;
    };
  }, []);
  return (
  <div
    className={"fs-react-timeline-surface" +
      (envelopeMode ? " fs-envelope-mode" : "") +
      (slideMode ? " fs-slide-mode" : "") +
      (slipMode ? " fs-slip-mode" : "")}
    data-react-timeline-surface="true"
    data-envelope-mode={envelopeMode ? "true" : "false"}
    data-slide-mode={slideMode ? "true" : "false"}
    data-slip-mode={slipMode ? "true" : "false"}
    style={{width:model.totalWidth,position:"relative"}}>
    <div className="fs-ruler" style={{width:model.totalWidth}}>
      <div className="fs-ruler-head">时码</div>
      <div
        className="fs-ruler-body"
        id="fs-ruler-body"
        data-react-scrub-ready="true"
        role="slider"
        aria-label="时间尺播放头位置；点击刻度跳转，拖动或使用方向键定位"
        aria-valuemin={0}
        aria-valuemax={duration}
        aria-valuenow={Math.min(duration, Math.max(0, playhead))}
        aria-valuetext={Math.min(duration, Math.max(0, playhead)).toFixed(2) + " 秒"}
        tabIndex={0}
        style={{width:model.laneWidth}}
        onKeyDown={event => {
          if (event.key !== "ArrowLeft" && event.key !== "ArrowRight") return;
          event.preventDefault();
          event.stopPropagation();
          const step = event.shiftKey ? 0.1 : 1;
          window.filmStudioSeek?.(playhead + (event.key === "ArrowRight" ? step : -step));
        }}
        onPointerDown={event => {
          if (event.button !== 0) return;
          const target = event.target;
          const tick = target instanceof Element ? target.closest(".fs-ruler-mark") : null;
          const rawTickAt = tick ? Number(tick.getAttribute("data-at")) : NaN;
          const tickAt = Number.isFinite(rawTickAt) ? rawTickAt : null;
          pendingScrubPointer.current = {
            pointerId:event.pointerId,startX:event.clientX,startY:event.clientY,
            startedOnTick:!!tick,tickAt
          };
          // Capture once, at the owning surface. Clicks on marks are resolved by
          // pointerup using data-at; a real drag is activated only after 4px.
          event.currentTarget.setPointerCapture(event.pointerId);
          if (!tick) seekFromRulerPointer(event.clientX, event.currentTarget);
        }}
        onPointerMove={event => {
          const pending = pendingScrubPointer.current;
          if (!pending || pending.pointerId !== event.pointerId) return;
          if (activeScrubPointerId.current !== event.pointerId) {
            const dx = event.clientX - pending.startX;
            const dy = event.clientY - pending.startY;
            if (Math.hypot(dx, dy) < 4) return;
            activeScrubPointerId.current = event.pointerId;
          }
          if (activeScrubPointerId.current === event.pointerId) {
            seekFromRulerPointer(event.clientX, event.currentTarget);
          }
        }}
        onPointerUp={event => {
          const pending = pendingScrubPointer.current;
          if (pending?.pointerId === event.pointerId) {
            // Manual tick resolution avoids depending on synthetic child click after
            // pointer capture, and prevents a drag started on a tick from snapping back.
            if (activeScrubPointerId.current !== event.pointerId &&
                pending.startedOnTick && pending.tickAt !== null) {
              window.filmStudioSeek?.(pending.tickAt);
            }
            pendingScrubPointer.current = null;
          }
          if (activeScrubPointerId.current !== event.pointerId) return;
          activeScrubPointerId.current = null;
        }}
        onPointerCancel={event => {
          if (pendingScrubPointer.current?.pointerId === event.pointerId) {
            pendingScrubPointer.current = null;
          }
          if (activeScrubPointerId.current !== event.pointerId) return;
          activeScrubPointerId.current = null;
        }}
      >
        {model.ticks.map((tick) => (
          <div className="fs-ruler-mark" data-at={tick.at} style={{left:tick.left}} key={tick.at}>{tick.label}</div>
        ))}
      </div>
    </div>
    {model.tracks.map((track,trackIndex) => {
      const trackProposal = previewForTrack(track.id);
      return (
      <React.Fragment key={"timeline-track-group-" + track.id}>
      {(proposalTracksBeforeIndex.get(trackIndex) || []).map(renderProposalTrack)}
      <div
        className={"fs-track" + (track.locked ? " fs-track-locked" : "") +
          (track.syncLocked ? " fs-sync-locked" : "") + (track.hidden ? " fs-hidden-track" : "") +
          (track.muted ? " fs-muted-track" : "") +
          (trackProposal?.removed ? " fs-track-proposal-removed" : "") +
          (dropTarget?.trackId === track.id ? (dropTarget.invalid ? " fs-track-drop-invalid" : " fs-track-drop-target") : "")}
        data-track={track.id}
        data-proposal-track-removed={trackProposal?.removed ? track.id : undefined}
        key={track.id}
        style={trackProposal?.removed ? {opacity:0.48,outline:"2px dashed #e5a23d",outlineOffset:"-2px"} : undefined}
      >
        <div className="fs-track-head">
          <span className="fs-track-label" title={track.label}>{track.label}{trackProposal?.removed ? " · 候选删除" : ""}</span>
          <span className="fs-track-controls">
            <button type="button" className="fs-btn" data-track-lock={track.id}
              title={track.locked ? "解锁轨道" : "锁定轨道"}
              onClick={(event) => { event.stopPropagation(); void window.filmStudioToggleTrackLock?.(track.id); }}
            >{track.locked ? "🔒" : "锁"}</button>
            <button type="button" className="fs-btn" data-track-flag="sync-lock" data-track-key={track.id} aria-pressed={track.syncLocked ? "true" : "false"} title={track.syncLocked ? "取消同步锁定" : "启用同步锁定"} onClick={(event) => { event.stopPropagation(); void window.filmStudioToggleTrackFlag?.(track.id, "sync-lock"); }}>{track.syncLocked ? "⇄" : "同步"}</button>
            <button type="button" className="fs-btn" data-track-flag="hidden" data-track-key={track.id} title={track.hidden ? "显示轨道" : "隐藏轨道"} onClick={(event) => { event.stopPropagation(); void window.filmStudioToggleTrackFlag?.(track.id, "hidden"); }}>{track.hidden ? "显" : "隐"}</button>
            <button type="button" className="fs-btn" data-track-flag="muted" data-track-key={track.id} title={track.muted ? "取消静音" : "静音轨道"} onClick={(event) => { event.stopPropagation(); void window.filmStudioToggleTrackFlag?.(track.id, "muted"); }}>{track.muted ? "🔊" : "🔇"}</button>
          </span>
        </div>
        <div className="fs-track-body" style={{width:model.laneWidth}}>
          {track.clips.map((clip) => (
            <div
              className={"fs-clip" +
                (selectedIds.includes(clip.id) ? " fs-clip-selected" : "") +
                (clip.locked ? " fs-clip-locked" : "") +
                (activeDragIds?.includes(clip.id) ? " fs-dragging" : "") +
                (previewForClip(clip.id)?.mode === "roll" ? " fs-rolling" : "") +
                (previewForClip(clip.id)?.mode === "slide" ? " fs-sliding" : "") +
                (previewForClip(clip.id)?.mode === "slip" ? " fs-slipping" : "") +
                (previewForClip(clip.id)?.removed ? " fs-clip-proposal-removed" : "")}
              data-clip={clip.id}
              data-clip-locked={clip.locked ? "true" : "false"}
              data-track={clip.trackId}
              data-kind={clip.tag}
              data-react-timeline-clip={clip.id}
              data-react-gesture-preview={previewForClip(clip.id) ? "true" : "false"}
              data-preview-fade-in={previewForClip(clip.id)?.fadeIn}
              data-preview-fade-out={previewForClip(clip.id)?.fadeOut}
              data-proposal-removed={previewForClip(clip.id)?.removed ? "true" : undefined}
              data-roll-delta={previewForClip(clip.id)?.mode === "roll" ? previewForClip(clip.id)?.deltaLabel : undefined}
              data-slide-delta={previewForClip(clip.id)?.mode === "slide" ? previewForClip(clip.id)?.deltaLabel : undefined}
              data-slip-delta={previewForClip(clip.id)?.mode === "slip" ? previewForClip(clip.id)?.deltaLabel : undefined}
              style={{
                left:previewForClip(clip.id)?.at != null
                  ? filmTimelineMath.clipLeft(Number(previewForClip(clip.id)?.at),model.pixelsPerSecond)
                  : previewForClip(clip.id)?.left ?? filmTimelineMath.clipLeft(clip.at,model.pixelsPerSecond),
                width:previewForClip(clip.id)?.duration !== undefined
                  ? filmTimelineMath.clipWidth(previewForClip(clip.id)?.duration ?? null,model.pixelsPerSecond,24,2,0,false)
                  : previewForClip(clip.id)?.width ?? filmTimelineMath.clipWidth(clip.duration,model.pixelsPerSecond,24,2),
                opacity:previewForClip(clip.id)?.removed ? 0.32 : undefined,
                outline:previewForClip(clip.id)?.removed ? "2px dashed #e5a23d" : undefined,
                outlineOffset:previewForClip(clip.id)?.removed ? "-2px" : undefined,
              }}
              title={clip.title}
              key={clip.id}
              onClick={event => window.filmStudioClickClip?.(clip.id,event.nativeEvent)}
              onContextMenu={event => {
                event.preventDefault();
                event.stopPropagation();
                window.filmStudioOpenTimelineContextMenu?.(clip.id,event.clientX,event.clientY);
              }}
              onPointerDown={event => {
                const target = event.target;
                const elementTarget = target instanceof Element ? target : null;
                const trim = elementTarget?.closest("[data-trim]");
                const rawTrimEdge = trim?.getAttribute("data-trim");
                const started=window.filmStudioBeginClipGesture?.(event.nativeEvent,{
                  clipId:clip.id,
                  trimEdge:rawTrimEdge === "start" || rawTrimEdge === "end" ? rawTrimEdge : null,
                  targetIsButton:!!elementTarget?.closest("button"),
                });
                if(started){
                  activePointerId.current=event.nativeEvent.pointerId;
                  try { event.currentTarget.setPointerCapture(event.nativeEvent.pointerId); } catch { /* synthetic events have no active pointer to capture */ }
                }
              }}
              onPointerMove={event => {
                if(activePointerId.current===event.nativeEvent.pointerId)window.filmStudioMoveClipGesture?.(event.nativeEvent);
              }}
              onPointerUp={event => {
                const pointerId=event.nativeEvent.pointerId;
                if(activePointerId.current!==pointerId)return;
                const pending=window.filmStudioEndClipGesture?.(event.nativeEvent);
                const clear=()=>{if(activePointerId.current===pointerId)activePointerId.current=null;};
                Promise.resolve(pending).then(clear,clear);
              }}
              onPointerCancel={event => {
                const pointerId=event.nativeEvent.pointerId;
                if(activePointerId.current!==pointerId)return;
                window.filmStudioCancelClipGesture?.(event.nativeEvent);
                Promise.resolve().then(() => {
                  if(activePointerId.current===pointerId)activePointerId.current=null;
                });
              }}
            >
              {clip.thumbnail ? <img className="fs-clip-thumb" src={clip.thumbnail} loading="lazy" alt="" onError={(event) => { event.currentTarget.style.display = "none"; }} /> : null}
              {clip.waveform ? <AudioWaveformCanvas
                clipId={clip.id}
                waveformKey={clip.waveformKey}
                duration={clip.duration ?? 1}
                pixelsPerSecond={model.pixelsPerSecond}
                sourceStart={clip.sourceStart}
                sourceEnd={clip.sourceEnd}
                source={waveformSources[clip.waveformKey]}
              /> : null}
              {clip.envelope ? <VolumeEnvelopeCanvas
                clipId={clip.id}
                duration={clip.duration ?? 0}
                pixelsPerSecond={model.pixelsPerSecond}
                points={clip.envelopePoints}
                preview={envelopePreview}
                onContextMenu={event => event.preventDefault()}
                onClick={event => event.stopPropagation()}
                onPointerDown={event => {
                  if (event.button !== 0 && event.button !== 2) return;
                  event.preventDefault();
                  event.stopPropagation();
                  const pointerId = event.nativeEvent.pointerId;
                  const bounds = event.currentTarget.getBoundingClientRect();
                  const started = window.filmStudioBeginEnvelopeGesture?.(event.nativeEvent, {
                    clipId:clip.id,
                    rect:{left:bounds.left,top:bounds.top,width:bounds.width,height:bounds.height},
                  });
                  if (started) {
                    activeEnvelopePointerId.current = pointerId;
                    try { event.currentTarget.setPointerCapture(pointerId); } catch { /* synthetic events have no active pointer to capture */ }
                  }
                }}
                onPointerMove={event => {
                  if (activeEnvelopePointerId.current === event.nativeEvent.pointerId) {
                    const bounds = event.currentTarget.getBoundingClientRect();
                    window.filmStudioMoveEnvelopeGesture?.(event.nativeEvent, {
                      left:bounds.left,top:bounds.top,width:bounds.width,height:bounds.height,
                    });
                  }
                }}
                onPointerUp={event => {
                  const pointerId = event.nativeEvent.pointerId;
                  if (activeEnvelopePointerId.current !== pointerId) return;
                  const pending = window.filmStudioEndEnvelopeGesture?.(event.nativeEvent);
                  const clear = () => { if (activeEnvelopePointerId.current === pointerId) activeEnvelopePointerId.current = null; };
                  Promise.resolve(pending).then(clear, clear);
                }}
                onPointerCancel={event => {
                  const pointerId = event.nativeEvent.pointerId;
                  if (activeEnvelopePointerId.current !== pointerId) return;
                  window.filmStudioCancelEnvelopeGesture?.(event.nativeEvent);
                  Promise.resolve().then(() => {
                    if (activeEnvelopePointerId.current === pointerId) activeEnvelopePointerId.current = null;
                  });
                }}
              /> : null}
              {clip.keyframeMarks.map(mark => (
                <button
                  type="button"
                  className="fs-clip-keyframe-marker"
                  data-film-keyframe-marker={clip.id}
                  data-keyframe-at={mark.at}
                  title={"关键帧 "+mark.at.toFixed(3)+"s · "+mark.properties.join(", ")}
                  aria-label={"跳转到 "+mark.at.toFixed(3)+" 秒关键帧"}
                  style={{left:(clip.duration && clip.duration>0 ? Math.max(0,Math.min(100,mark.at/clip.duration*100)) : 0)+"%"}}
                  key={mark.at}
                  onPointerDown={(event) => {
                    // A marker is a seek target, never the parent clip's drag surface.
                    event.stopPropagation();
                  }}
                  onClick={(event) => {
                    event.preventDefault();
                    event.stopPropagation();
                    window.filmStudioJumpToKeyframe?.(clip.id, mark.at);
                  }}
                />
              ))}
              {previewForClip(clip.id)?.removed ? <span
                data-proposal-remove-label={clip.id}
                aria-hidden="true"
                style={{position:"absolute",zIndex:6,right:4,top:2,pointerEvents:"none",fontSize:10,fontWeight:700,color:"#6b3710",background:"rgba(255,238,208,.96)",border:"1px dashed #e5a23d",padding:"1px 3px"}}
              >候选删除</span> : null}
              {clip.locked ? <span className="fs-clip-lock-mark" aria-label="片段已锁定">🔒</span> : null}
              {clip.trimHandles ? <>
                <span className="fs-trim-handle fs-trim-left" data-trim="start" title="修剪入点" />
                <span className="fs-trim-handle fs-trim-right" data-trim="end" title="修剪出点" />
              </> : null}
              {(previewForClip(clip.id)?.fadeIn ?? 0) > 0 ? <span
                className="fs-clip-fade-preview fs-clip-fade-in" data-fade-preview="in" aria-hidden="true"
                title={"候选淡入 " + Number(previewForClip(clip.id)?.fadeIn || 0).toFixed(2) + "s"}
                style={{
                  position:"absolute",zIndex:5,pointerEvents:"none",top:0,bottom:0,left:0,
                  width:(Math.max(0,Math.min(100,(previewForClip(clip.id)?.fadeIn || 0)/Math.max(0.001,clip.duration ?? 1)*100)))+"%",
                  background:"linear-gradient(to right, rgba(255,255,255,.72), rgba(255,255,255,0))",
                  borderLeft:"2px solid rgba(255,255,255,.82)",
                }}
              /> : null}
              {(previewForClip(clip.id)?.fadeOut ?? 0) > 0 ? <span
                className="fs-clip-fade-preview fs-clip-fade-out" data-fade-preview="out" aria-hidden="true"
                title={"候选淡出 " + Number(previewForClip(clip.id)?.fadeOut || 0).toFixed(2) + "s"}
                style={{
                  position:"absolute",zIndex:5,pointerEvents:"none",top:0,bottom:0,right:0,
                  width:(Math.max(0,Math.min(100,(previewForClip(clip.id)?.fadeOut || 0)/Math.max(0.001,clip.duration ?? 1)*100)))+"%",
                  background:"linear-gradient(to left, rgba(255,255,255,.72), rgba(255,255,255,0))",
                  borderRight:"2px solid rgba(255,255,255,.82)",
                }}
              /> : null}
              <span className="fs-clip-title">{clip.title}</span>
              <span className="fs-clip-subtitle">{clip.atLabel}{clip.durationLabel}</span>
            </div>
          ))}
          {(crossTrackGhostsByTrack.get(track.id) || []).map((clip) => {
            const preview = previewForClip(clip.id);
            if (!preview) return null;
            return (
              <div
                className="fs-clip fs-clip-gesture-ghost"
                key={"gesture-ghost-" + clip.id}
                data-gesture-ghost-for={clip.id}
                data-gesture-target-track={track.id}
                data-kind={clip.tag}
                aria-hidden="true"
                style={{
                  left:preview.at != null
                    ? filmTimelineMath.clipLeft(Number(preview.at),model.pixelsPerSecond)
                    : preview.left ?? filmTimelineMath.clipLeft(clip.at,model.pixelsPerSecond),
                  width:preview.duration !== undefined
                    ? filmTimelineMath.clipWidth(preview.duration ?? null,model.pixelsPerSecond,24,2,0,false)
                    : preview.width ?? filmTimelineMath.clipWidth(clip.duration,model.pixelsPerSecond,24,2),
                }}
              >
                <span className="fs-clip-title">{clip.title}</span>
                <span className="fs-clip-subtitle">{clip.atLabel}{clip.durationLabel}</span>
              </div>
            );
          })}
          {(proposalAdditionsByTrack.get(track.id) || []).map(({id,preview}) => (
            <div
              className="fs-clip fs-clip-gesture-ghost fs-clip-proposal-added"
              key={"proposal-added-" + id}
              data-proposal-added={id}
              data-gesture-target-track={track.id}
              data-kind={preview.previewTag || "video"}
              aria-hidden="true"
              title={"候选新增：" + String(preview.previewTitle || id)}
              style={{
                left:filmTimelineMath.clipLeft(Number(preview.at || 0),model.pixelsPerSecond),
                width:filmTimelineMath.clipWidth(preview.duration ?? null,model.pixelsPerSecond,24,2,0,false),
                opacity:0.88,
                outline:"2px dashed #65a7dc",
                outlineOffset:"-2px",
                background:"rgba(80,145,200,.2)",
              }}
            >
              <span className="fs-clip-title">{String(preview.previewTitle || id)} · 候选新增</span>
              <span className="fs-clip-subtitle">{formatFilmTime(Number(preview.at || 0))} · {preview.duration == null ? "时长未知" : Number(preview.duration).toFixed(2)+"s"}</span>
            </div>
          ))}
        </div>
      </div>
      </React.Fragment>
      );
    })}
    {(proposalTracksBeforeIndex.get(model.tracks.length) || []).map(renderProposalTrack)}
    {snapGuide ? <div id="fs-snap-guide" className="fs-snap-guide"
      style={{left:132 + filmTimelineMath.clipLeft(snapGuide.at,model.pixelsPerSecond)}}
      title={snapGuide.title} data-snap-kind={snapGuide.kind} /> : null}
    <div className="fs-playhead" id="fs-playhead-line"
      style={{left:132 + filmTimelineMath.clipLeft(playhead,model.pixelsPerSecond)}} />
  </div>
  );
};

const StudioAssets: React.FC<{model: FilmStudioAssetsModel; mode: FilmStudioAssetMode}> = ({model,mode}) => {
  const libraryAssetsSource = useFilmStoreField("libraryAssets");
  const libraryAssets = useMemo<FilmStudioLibraryAsset[]>(() => libraryAssetsSource
    .filter((asset): asset is Record<string, unknown> =>
      !!asset && typeof asset === "object" && !Array.isArray(asset))
    .filter(asset => ["video","image","audio"].includes(String(asset.type || "").toLowerCase()))
    .map(asset => {
      const id=String(asset.asset_id || asset.id || "");
      const kind=String(asset.type || "").toLowerCase();
      const label=String(asset.description || asset.name || asset.filename || id);
      const duration=asset.duration;
      const source=asset.source;
      return {
        id,kind,label,
        meta:kind.toUpperCase()+(duration ? " · "+Number(duration).toFixed(2)+"s" : "")+(source ? " · "+String(source) : ""),
      };
    }), [libraryAssetsSource]);
  if (mode === "director") return null;
  if (mode === "library") {
    if (!libraryAssets.length) {
      return <div className="fs-react-asset-items" data-react-asset-list="true">
        <div className="fs-empty">素材库没有可直接加入时间线的视频或图片。已上传的项目素材和全局素材会在此显示。</div>
      </div>;
    }
    return <div className="fs-react-asset-items" data-react-asset-list="true">
      {libraryAssets.map((asset) => (
        <div className="fs-asset-item" data-react-library-asset={asset.id} key={asset.id}>
          <span className="fs-asset-icon">{asset.kind.toUpperCase()}</span>
          <span className="fs-asset-name" title={asset.label}>
            {asset.label}<span className="fs-asset-meta">{asset.meta}</span>
          </span>
          <button
            type="button"
            className="fs-btn fs-primary"
            data-add-asset={asset.id}
            title="在播放头位置新建轨道并添加"
            onClick={(event) => {
              event.stopPropagation();
              void window.filmStudioAddAsset?.(asset.id);
            }}
          >添加</button>
        </div>
      ))}
    </div>;
  }
  if (!model.clips.length) {
    return <div className="fs-react-asset-items" data-react-asset-list="true">
      <div className="fs-empty">此影片没有片段。可从素材库添加视频或图片。</div>
    </div>;
  }
  return <div className="fs-react-asset-items" data-react-asset-list="true">
    {model.clips.map((clip) => (
      <button
        type="button"
        key={clip.id}
        className={"fs-asset-item" + (clip.selected ? " fs-selected" : "")}
        data-clip={clip.id}
        data-react-asset-clip={clip.id}
        title={clip.title}
        onClick={() => window.filmStudioSelectClip?.(clip.id)}
      >
        <span className="fs-asset-icon">
          {clip.thumbnail
            ? <img className="fs-asset-thumb" src={clip.thumbnail} loading="lazy" alt="" onError={(event) => { event.currentTarget.style.display = "none"; }} />
            : clip.tag.toUpperCase()}
        </span>
        <span className="fs-asset-name">
          {clip.title}<span className="fs-asset-meta">{clip.meta}</span>
        </span>
      </button>
    ))}
  </div>;
};

const StudioScrubRow: React.FC = () => {
  const playhead=useFilmStoreField("playhead");
  const playing=useFilmStoreField("playing");
  const duration=useFilmStoreField("duration");
  return (
  <>
    <button className="fs-btn" type="button" data-film-action="frame-prev">上一帧</button>
    <button className="fs-btn" type="button" id="fs-play-toggle" data-film-action="play-toggle"
      aria-pressed={playing} title={playing ? "暂停预览" : "播放预览"}>{playing ? "暂停" : "播放"}</button>
    <input
      id="fs-playhead"
      type="range"
      min={0}
      max={Math.max(0.001,duration)}
      step={0.033333}
      value={Math.max(0,Math.min(duration,playhead))}
      onChange={(event) => window.filmStudioSeek?.(event.currentTarget.value)}
      aria-label="播放头"
    />
    <button className="fs-btn" type="button" data-film-action="frame-next">下一帧</button>
    <button className="fs-btn" id="fs-element-inspect-toggle" type="button" data-film-action="canvas-inspect" aria-pressed="false" title="在节目监视器中点选 HTML 镜头里的文字或图层">选择画面元素</button>
    <span id="fs-playhead-value">{formatFilmTime(playhead)}</span>
  </>
  );
};

const StudioTimelineToolbar: React.FC = () => {
  const insertMode=useFilmStoreField("insertMode");
  const overwriteMode=useFilmStoreField("overwriteMode");
  const snapEnabled=useFilmStoreField("snapEnabled");
  const envelopeMode=useFilmStoreField("envelopeMode");
  const slideMode=useFilmStoreField("slideMode");
  const slipMode=useFilmStoreField("slipMode");
  return (
  <>
    <button className={"fs-btn" + (insertMode ? " fs-primary" : "")} type="button" data-film-action="insert-mode" aria-pressed={insertMode} title="开启后，素材插入所选轨道并推移后续片段及同步锁定轨道">插入编辑：{insertMode ? "开" : "关"}</button>
    <button className={"fs-btn" + (overwriteMode ? " fs-primary" : "")} type="button" data-film-action="overwrite-mode" aria-pressed={overwriteMode} title="覆盖编辑只替换所选轨道的重叠区间，不推移其他片段或音频轨道">覆盖编辑：{overwriteMode ? "开" : "关"}</button>
    <button className="fs-btn" type="button" data-film-action="toggle-snap" aria-pressed={snapEnabled} title="吸附到播放头、片头和其他片段边缘；吸附距离按屏幕像素计算">吸附：{snapEnabled ? "开" : "关"}</button>
    <button className="fs-btn" type="button" data-film-action="split" title="在播放头切分（S）">在播放头切分</button>
    <button className="fs-btn" type="button" data-film-action="copy-clips" title="复制选中片段（Ctrl/⌘+C）">复制</button>
    <button className="fs-btn" type="button" data-film-action="paste-clips" title="按原相对布局粘贴到播放头附近的可用区间（Ctrl/⌘+V）">粘贴</button>
    <button className="fs-btn" type="button" data-film-action="join-cut" title="合并同轨相邻、同源连续且属性一致的切分片段">愈合切口</button>
    <button className="fs-btn" type="button" data-film-action="remove">删除片段</button>
    <button className="fs-btn" type="button" data-film-action="ripple-delete" title="删除选中片段对应的时间段，并收拢全部轨道">波纹删除</button>
    <button className={"fs-btn" + (envelopeMode ? " fs-envelope-mode-active" : "")} type="button" data-film-action="envelope-mode" aria-pressed={envelopeMode} title="切换音量关键帧编辑模式；普通模式下可正常拖动片段">音量曲线：{envelopeMode ? "开" : "关"}</button>
    <button className={"fs-btn" + (slideMode ? " fs-slide-mode-active" : "")} type="button" data-film-action="slide-mode" aria-pressed={slideMode} title="切换滑移修剪模式；拖动中间视频/音频片段主体，外侧时间线边界保持不变">滑移修剪：{slideMode ? "开" : "关"}</button>
    <button className={"fs-btn" + (slipMode ? " fs-slip-mode-active" : "")} type="button" data-film-action="slip-mode" aria-pressed={slipMode} title="切换源区间滑移模式；只改变素材源范围，片段时间线位置与长度不变">源区间滑移：{slipMode ? "开" : "关"}</button>
    <button className="fs-btn" type="button" data-film-action="add-track">＋轨道</button>
    <button className="fs-btn" type="button" data-film-action="remove-empty-track">删空轨</button>
    <button className="fs-btn" type="button" data-film-action="zoom-out">−</button>
    <button className="fs-btn" type="button" data-film-action="zoom-in">＋</button>
    <button className="fs-btn fs-primary" type="button" data-film-action="apply-inspector">应用属性</button>
  </>
  );
};

const editableCssLength = (value: unknown): string => {
  const text=String(value||"").trim();
  return /^-?(?:\d+(?:\.\d+)?|\.\d+)(?:px|%|em|rem|vh|vw)?$/i.test(text) ? text : "";
};

const CanvasInspector: React.FC<{element: CanvasElementSelection}> = ({element}) => {
  const rect = element.localRect || element.rect || {};
  const styles = element.styles || {};
  const backgroundColor = String(styles.backgroundColor || "transparent");
  const currentTextAlign = ["left", "center", "right", "justify", "start", "end"].includes(String(styles.textAlign || "left"))
    ? String(styles.textAlign || "left") : "left";
  return (
    <section className="fs-inspector-element focus-within:ring-2 focus-within:ring-teal-400" data-react-canvas-inspector="true">
      <h4>画布元素 · {element.tag} · {element.selector}</h4>
      <label className="fs-inspector-field">
        文本
        <textarea
          id="fs-element-text"
          rows={2}
          disabled={!element.textEditable}
          title={!element.textEditable ? "该元素包含子元素，为避免破坏内部结构，暂不允许整体替换文本" : undefined}
          defaultValue={element.text || ""}
        />
      </label>
      <label className="fs-inspector-field">
        左坐标
        <input id="fs-element-left" type="number" step={1} defaultValue={asNumber(rect.x, 0).toFixed(1)} />
      </label>
      <label className="fs-inspector-field">
        上坐标
        <input id="fs-element-top" type="number" step={1} defaultValue={asNumber(rect.y, 0).toFixed(1)} />
      </label>
      <label className="fs-inspector-field">
        宽度
        <input id="fs-element-width" type="number" min={1} max={10000} step={1} defaultValue={asNumber(rect.width, 1).toFixed(1)} />
      </label>
      <label className="fs-inspector-field">
        高度
        <input id="fs-element-height" type="number" min={1} max={10000} step={1} defaultValue={asNumber(rect.height, 1).toFixed(1)} />
      </label>
      <label className="fs-inspector-field">
        文字颜色
        <input id="fs-element-color" type="color" defaultValue={toHexColor(styles.color || "#ffffff")} />
      </label>
      <div className="fs-inspector-field">
        <span>背景填色</span>
        <div className="flex items-center gap-2">
          <label className="inline-flex items-center gap-1">
            <input id="fs-element-background-enabled" type="checkbox" defaultChecked={!isTransparentBackground(backgroundColor)} />
            启用
          </label>
          <input id="fs-element-background-color" type="color" aria-label="背景颜色" defaultValue={toHexColor(backgroundColor)} />
        </div>
      </div>
      <label className="fs-inspector-field">
        不透明度（0–1）
        <input id="fs-element-opacity" type="number" min={0} max={1} step={0.05} defaultValue={asNumber(styles.opacity, 1)} />
      </label>
      <label className="fs-inspector-field">
        字重
        <select id="fs-element-font-weight" defaultValue={String(normalizedFontWeight(styles.fontWeight))}>
          {[100, 200, 300, 400, 500, 600, 700, 800, 900].map((weight) => (
            <option key={weight} value={weight}>{weight}{weight === 400 ? "（常规）" : weight === 700 ? "（粗体）" : ""}</option>
          ))}
        </select>
      </label>
      <label className="fs-inspector-field">
        文字对齐
        <select id="fs-element-text-align" defaultValue={currentTextAlign}>
          {["left", "center", "right", "justify", "start", "end"].map((align) => (
            <option key={align} value={align}>{({left:"左对齐",center:"居中",right:"右对齐",justify:"两端对齐",start:"起始对齐",end:"末端对齐"} as Record<string,string>)[align]}</option>
          ))}
        </select>
      </label>
      <label className="fs-inspector-field">
        行高（CSS 长度）
        <input id="fs-element-line-height" type="text" maxLength={120} inputMode="decimal"
          placeholder={"留空不修改；当前值："+String(styles.lineHeight||"未提供")}
          defaultValue={editableCssLength(styles.lineHeight)} />
      </label>
      <label className="fs-inspector-field">
        字间距（CSS 长度）
        <input id="fs-element-letter-spacing" type="text" maxLength={120} inputMode="decimal"
          placeholder={"留空不修改；当前值："+String(styles.letterSpacing||"未提供")}
          defaultValue={editableCssLength(styles.letterSpacing)} />
      </label>
      <label className="fs-inspector-field">
        字号（px）
        <input id="fs-element-font-size" type="number" min={1} max={1000} step={1} defaultValue={asNumber(parseFloat(styles.fontSize), 24)} />
      </label>
      <label className="fs-inspector-field">
        旋转（度）
        <input id="fs-element-rotate" type="number" min={-3600} max={3600} step={1} defaultValue={rotateDegrees(styles)} />
      </label>
      <button
        type="button"
        className="fs-btn fs-primary transition-colors duration-150 hover:brightness-110 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-teal-400"
        id="fs-apply-element"
        onClick={() => { void window.filmStudioSaveSelectedElement?.(); }}
      >
        应用画布修改
      </button>
    </section>
  );
};

let studioRoot: Root | null = null;
let studioRootMount: HTMLElement | null = null;

const studioPortal = (targetId: string, child: React.ReactNode): React.ReactPortal | null => {
  const target = document.getElementById(targetId);
  return target ? createPortal(child, target, targetId) : null;
};

/**
 * A single React root owns every React-rendered Studio surface.
 * Portals preserve the existing layout slots while preventing independent roots
 * from racing over the same editor state or remounting shared surfaces.
 */
const FilmStoreDiagnostics: React.FC = () => {
  const filmSnapshot=useSyncExternalStore(filmStore.subscribe,filmStore.getSnapshot,filmStore.getSnapshot);
  return (
      <div id="fs-react-film-store-state" hidden aria-hidden="true"
        data-film-project-id={filmSnapshot.projectId}
        data-film-revision={filmSnapshot.revision}
        data-selected-clip={filmSnapshot.selectedId}
        data-selected-clips={filmSnapshot.selectedIds.join(",")}
        data-asset-mode={filmSnapshot.assetMode}
        data-pending-revision={filmSnapshot.pendingExternalRevision}
        data-insert-mode={filmSnapshot.insertMode ? "true" : "false"}
        data-overwrite-mode={filmSnapshot.overwriteMode ? "true" : "false"}
        data-snap-enabled={filmSnapshot.snapEnabled ? "true" : "false"}
        data-envelope-mode={filmSnapshot.envelopeMode ? "true" : "false"}
        data-slide-mode={filmSnapshot.slideMode ? "true" : "false"}
        data-slip-mode={filmSnapshot.slipMode ? "true" : "false"}
        data-canvas-element-inspect={filmSnapshot.canvasElementInspectMode ? "true" : "false"}
        data-busy={filmSnapshot.busy ? "true" : "false"}
        data-broken={filmSnapshot.broken ? "true" : "false"}
        data-pending-transaction-kind={filmSnapshot.pendingTransaction?.kind || ""}
        data-pending-transaction-base={filmSnapshot.pendingTransaction?.baseRevision || ""}
        data-pending-transaction-operation-count={String(filmSnapshot.pendingTransaction?.operationCount || 0)}
      />
  );
};

const StudioReactOwner: React.FC = () => {
  const assetsModel = useFilmStoreField("assetsModel") as FilmStudioAssetsModel | null;
  const assetMode = useFilmStoreField("assetMode");
  const aiModel = useFilmStoreField("aiDirectorModel") as FilmStudioAiDirectorModel | null;
  const clipInspectorEmptyMessage = useFilmStoreField("clipInspectorEmptyMessage");
  const toolbarVisible = useFilmStoreField("toolbarVisible");
  const timelineToolbarVisible = useFilmStoreField("timelineToolbarVisible");
  const scrubVisible = useFilmStoreField("scrubVisible");
  const clipInspectorVisible = useFilmStoreField("clipInspectorVisible");
  const canvasInspectorVisible = useFilmStoreField("canvasInspectorVisible");
  const timelineVisible = useFilmStoreField("timelineVisible");
  const timelineFilm = useFilmStoreField("film");
  const timelineProjectId = useFilmStoreField("projectId");
  const timelineRevision = useFilmStoreField("revision");
  const timelineDuration = useFilmStoreField("duration");
  const timelinePixelsPerSecond = useFilmStoreField("pixelsPerSecond");
  const timelineSelectedIdsVersion = useFilmStoreField("selectedIdsVersion");
  const clipInspectorSelectedId = useFilmStoreField("selectedId");
  const clipInspectorPlayhead = useFilmStoreField("playhead");
  const clip = useMemo(
    () => buildClipInspectorModel(timelineFilm,clipInspectorSelectedId,clipInspectorPlayhead),
    [timelineFilm,timelineRevision,clipInspectorSelectedId,clipInspectorPlayhead],
  );
  const timelineSurface = useMemo(
    () => timelineVisible
      ? buildTimelineSurfaceModel(
          timelineFilm,
          timelineProjectId,
          timelineDuration,
          timelinePixelsPerSecond,
          filmStore.getSnapshot().selectedIds,
        )
      : null,
    [timelineVisible,timelineFilm,timelineProjectId,timelineRevision,timelineDuration,timelinePixelsPerSecond,timelineSelectedIdsVersion],
  );
  const timelineContextMenu = useFilmStoreField("timelineContextMenu");
  const canvasElement = useFilmStoreField("canvasElementSelection");
  const clipKey = clip
    ? clip.id + ":" + JSON.stringify(clip.attrs || {})
    : "empty:" + (clipInspectorEmptyMessage || "");
  const element = canvasElement;
  return (
    <>
      <FilmStoreDiagnostics />
      {assetsModel ? studioPortal("fs-assets-list", <StudioAssets model={assetsModel} mode={assetMode} />) : null}
      {timelineSurface ? studioPortal("fs-timeline", <TimelineSurface model={timelineSurface} />) : null}
      {toolbarVisible ? studioPortal("fs-react-toolbar", <StudioToolbar />) : null}
      {timelineToolbarVisible ? studioPortal("fs-react-timeline-toolbar", <StudioTimelineToolbar />) : null}
      {scrubVisible ? studioPortal("fs-react-scrub-row", <StudioScrubRow />) : null}
      {aiModel ? studioPortal("fs-react-ai-director", <StudioAiDirector key="ai-director" model={aiModel} />) : null}
      {clipInspectorVisible ? studioPortal(
        "fs-inspector",
        <ClipInspector key={clipKey} clip={clip} emptyMessage={clipInspectorEmptyMessage ?? undefined} />
      ) : null}
      {canvasInspectorVisible ? studioPortal(
        "fs-react-canvas-inspector",
        element ? <CanvasInspector key={element.clipId + "::" + element.selector} element={element} /> : null
      ) : null}
      {timelineContextMenu ? <StudioTimelineContextMenu model={timelineContextMenu} /> : null}
    </>
  );
};

function renderStudioReactOwner(): void {
  const mount = document.getElementById("fs-react-root");
  if (!mount) return;
  if (!studioRoot || studioRootMount !== mount) {
    studioRoot = createRoot(mount);
    studioRootMount = mount;
  }
  flushSync(() => {
    studioRoot?.render(<StudioReactOwner />);
  });
}

window.filmStudioTimelineMath = filmTimelineMath;

window.filmStudioReactAssets = {
  render(model) {
    filmStore.setField("assetsModel", model);
    renderStudioReactOwner();
  },
};

window.filmStudioReactTimeline = {
  renderSurface() {
    const mount = document.getElementById("fs-react-root");
    if (mount && (!studioRoot || studioRootMount !== mount)) {
      filmStore.setField("timelineVisible", true);
      renderStudioReactOwner();
      return;
    }
    flushSync(() => {
      filmStore.setField("timelineVisible", true);
    });
  },
  setGesturePreview(preview) {
    filmStore.setField("timelineGesturePreview", preview);
  },
  setSnapGuide(guide) {
    filmStore.setField("timelineSnapGuide", guide);
  },
  setTrackDropTarget(target) {
    filmStore.setField("timelineDropTarget", target);
  },
  setActiveDragIds(ids) {
    filmStore.setField("timelineActiveDragIds", ids);
  },
  setEnvelopePreview(preview) {
    filmStore.setField("timelineEnvelopePreview", preview);
  },
  clearSurface() {
    // The legacy project-switch path may immediately replace #fs-timeline children
    // after this bridge returns. Flush the portal unmount first so React never tries
    // to remove DOM nodes that the imperative fallback has already removed.
    flushSync(() => {
      filmStore.setField("timelineGesturePreview", null);
      filmStore.setField("timelineSnapGuide", null);
      filmStore.setField("timelineDropTarget", null);
      filmStore.setField("timelineActiveDragIds", null);
      filmStore.setField("timelineGestureContext", null);
      filmStore.setField("timelineEnvelopeGesture", null);
      filmStore.setField("timelineEnvelopePreview", null);
      filmStore.setField("timelineContextMenu", null);
      filmStore.setField("timelineSkipNextClipClick", false);
      filmStore.setField("timelineVisible", false);
    });
  },
  hasSurface() {
    return filmStore.getField("timelineVisible") === true;
  },
  openContextMenu(model) {
    filmStore.setField("timelineContextMenu", model);
  },
  closeContextMenu() {
    if (filmStore.getField("timelineContextMenu") !== null) filmStore.setField("timelineContextMenu", null);
  },
  isContextMenuOpen() {
    return filmStore.getField("timelineContextMenu") !== null;
  },
};

window.filmStudioReactToolbar = {
  render() {
    filmStore.setField("toolbarVisible", true);
    renderStudioReactOwner();
  },
  renderTimeline() {
    filmStore.setField("timelineToolbarVisible", true);
    renderStudioReactOwner();
  },
  renderScrub() {
    filmStore.setField("scrubVisible", true);
    renderStudioReactOwner();
  },
};

window.filmStudioReactAi = {
  render(model) {
    filmStore.setField("aiDirectorModel", model);
    renderStudioReactOwner();
  },
};

window.filmStudioReactInspector = {
  saveSelected() {
    const element=filmStore.getField("canvasElementSelection");
    if(!element){window.filmStudioShowInspectorError?.("请先在预览画布中选中一个 HTML 元素。");return;}
    const form=readCanvasElementForm();
    if(!form){window.filmStudioShowInspectorError?.("画布检查器尚未挂载完成，请重新选择元素。");return;}
    const result=buildCanvasElementOperation(element,form);
    if(!result.ok){window.filmStudioShowInspectorError?.(result.message);return;}
    if(!result.changed)return;
    if(!window.filmStudioCommitCanvasElement){
      window.filmStudioShowInspectorError?.("画布编辑提交桥尚未就绪；本次没有写入 film.html。");return;
    }
    return window.filmStudioCommitCanvasElement(result.operation, {allowInspectorDraft:true});
  },
  renderClip(emptyMessage) {
    filmStore.setField("clipInspectorVisible", true);
    filmStore.setField("clipInspectorEmptyMessage", emptyMessage ?? null);
    renderStudioReactOwner();
  },
  render(element) {
    filmStore.setField("canvasElementSelection", element);
    filmStore.setField("canvasInspectorVisible", true);
    renderStudioReactOwner();
    if (element) this.sync(element);
  },
  sync(element) {
    const rect = element.localRect || element.rect || {};
    const styles = element.styles || {};
    const values: Array<[string, string]> = [
      ["fs-element-text", element.text || ""],
      ["fs-element-left", asNumber(rect.x, 0).toFixed(1)],
      ["fs-element-top", asNumber(rect.y, 0).toFixed(1)],
      ["fs-element-width", asNumber(rect.width, 1).toFixed(1)],
      ["fs-element-height", asNumber(rect.height, 1).toFixed(1)],
      ["fs-element-color", toHexColor(styles.color || "#ffffff")],
      ["fs-element-background-color", toHexColor(styles.backgroundColor || "#ffffff")],
      ["fs-element-opacity", String(asNumber(styles.opacity, 1))],
      ["fs-element-font-weight", String(normalizedFontWeight(styles.fontWeight))],
      ["fs-element-text-align", ["left", "center", "right", "justify", "start", "end"].includes(String(styles.textAlign || "left")) ? String(styles.textAlign || "left") : "left"],
      ["fs-element-font-size", String(asNumber(parseFloat(styles.fontSize), 24))],
      ["fs-element-rotate", String(rotateDegrees(styles))],
    ];
    for (const [id, value] of values) {
      const input = document.getElementById(id) as HTMLInputElement | HTMLTextAreaElement | HTMLSelectElement | null;
      if (input && !(id === "fs-element-text" && input.disabled)) input.value = value;
    }
    const backgroundToggle = document.getElementById("fs-element-background-enabled") as HTMLInputElement | null;
    if (backgroundToggle) backgroundToggle.checked = !isTransparentBackground(String(styles.backgroundColor || "transparent"));
  },
};
