import React, {useEffect, useState} from "react";
import type {FilmOperation} from "./film-timeline-contract";
import {createPortal, flushSync} from "react-dom";
import {createRoot, type Root} from "react-dom/client";

type Rect = {x?: number; y?: number; width?: number; height?: number};
export interface CanvasElementSelection {
  clipId: string;
  selector: string;
  tag: string;
  text: string;
  textEditable: boolean;
  styles: Record<string, string>;
  rect: Rect;
  localRect: Rect;
  baseRevision: string;
}
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
type FilmKeyframeWindow = {type:"window";preset:FilmKeyframePreset;start:number;end:number};
type FilmKeyframeEasing = FilmKeyframePreset|FilmKeyframeWindow;
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
          }else if(rawEasing&&typeof rawEasing==="object"&&rawEasing.type==="window"
            &&["linear","ease_in","ease_out","ease_in_out"].includes(rawEasing.preset)
            &&Number.isFinite(Number(rawEasing.start))&&Number.isFinite(Number(rawEasing.end))
            &&Number(rawEasing.start)>=0&&Number(rawEasing.end)<=1&&Number(rawEasing.end)>Number(rawEasing.start)){
            easing={type:"window",preset:rawEasing.preset as FilmKeyframePreset,start:Number(rawEasing.start),end:Number(rawEasing.end)};
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
  const base=(value:number,preset:FilmKeyframePreset):number=>{
    const v=clampKeyframe(value,0,1);
    if(preset==="ease_in")return v*v;
    if(preset==="ease_out")return 1-(1-v)*(1-v);
    if(preset==="ease_in_out")return v<.5?2*v*v:1-Math.pow(-2*v+2,2)/2;
    return v;
  };
  if(typeof easing==="string")return base(x,easing);
  const start=clampKeyframe(easing.start,0,1),end=clampKeyframe(easing.end,0,1);
  if(end<=start)return x;
  const low=base(start,easing.preset),high=base(end,easing.preset);
  if(Math.abs(high-low)<1e-12)return x;
  return clampKeyframe((base(start+(end-start)*x,easing.preset)-low)/(high-low),0,1);
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
  renderClip(clip: FilmClipInspectorModel | null, emptyMessage?: string): void;
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
  mode: "clips" | "library";
  clips: FilmStudioAssetClip[];
  libraryAssets: FilmStudioLibraryAsset[];
};
interface FilmStudioReactAssetsBridge {
  render(model: FilmStudioAssetsModel): void;
}
type FilmTimelineSurfaceClip = {
  id: string; trackId: string; tag: string; title: string;
  left: number; width: number; duration:number|null; at:number; atLabel: string; durationLabel: string;
  selected: boolean; locked: boolean; thumbnail: string; waveform: boolean; envelope: boolean;
  keyframeMarks:Array<{at:number;properties:string[]}>; trimHandles: boolean;
};
type FilmTimelineSurfaceTrack = {
  id: string; label: string; locked: boolean; syncLocked: boolean; hidden: boolean; muted: boolean;
  clips: FilmTimelineSurfaceClip[];
};
type FilmTimelineSurfaceModel = {
  laneWidth: number; totalWidth: number; pixelsPerSecond: number;
  ticks: Array<{at:number;label:string;left:number}>;
  tracks: FilmTimelineSurfaceTrack[];
};
interface FilmStudioReactTimelineBridge {
  renderSurface(model: FilmTimelineSurfaceModel): void;
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
type FilmStudioAiDirectorModel = {messages: FilmStudioAiMessage[]; proposal: FilmStudioAiProposal};
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
    filmStudioReactAssets?: FilmStudioReactAssetsBridge;
    filmStudioReactTimeline?: FilmStudioReactTimelineBridge;
    filmStudioReactAi?: FilmStudioReactAiBridge;
    filmStudioAsk?: () => Promise<void> | void;
    filmStudioSelectClip?: (clipId: string) => void;
    filmStudioClickClip?: (clipId: string, event: MouseEvent) => void;
    filmStudioBeginClipDrag?: (event: PointerEvent, element: HTMLElement) => void;
    filmStudioMoveClipGesture?: (event: PointerEvent) => void;
    filmStudioEndClipGesture?: (event: PointerEvent) => Promise<void> | void;
    filmStudioShowAssetMode?: (mode: string) => void;
    filmStudioAddAsset?: (assetId: string) => Promise<void> | void;
  }
}

const asNumber = (value: unknown, fallback: number): number => {
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : fallback;
};

type ClipInspectorBuildResult =
  | {ok: true; ops: FilmOperation[]}
  | {ok: false; message: string};

type CanvasElementFormValues = {
  text: string; left: string; top: string; width: string; height: string;
  color: string; backgroundEnabled: boolean; backgroundColor: string;
  opacity: string; fontWeight: string; textAlign: string; fontSize: string; rotate: string;
};
type CanvasElementBuildResult =
  | {ok:true; operation:Extract<FilmOperation,{op:"element_set"}>}
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
  const style:Record<string,string>={
    position:"fixed",left:values["左坐标"].toFixed(2)+"px",top:values["上坐标"].toFixed(2)+"px",
    width:values["宽度"].toFixed(2)+"px",height:values["高度"].toFixed(2)+"px",color:form.color,
    fontSize:values["字号"].toFixed(2)+"px",rotate:values["旋转角度"].toFixed(2)+"deg",
  };
  const currentBackground = String(element.styles.backgroundColor || "transparent");
  const currentBackgroundTransparent = isTransparentBackground(currentBackground);
  if(form.backgroundEnabled) {
    if(currentBackgroundTransparent || toHexColor(currentBackground).toLowerCase()!==form.backgroundColor.toLowerCase()) {
      style.backgroundColor=form.backgroundColor;
    }
  } else if(!currentBackgroundTransparent) {
    // Unchecking an existing fill means explicitly restoring transparency.
    style.backgroundColor="transparent";
  }
  const currentOpacity=asNumber(element.styles.opacity,1);
  if(Math.abs(Number(form.opacity)-currentOpacity)>1e-5) style.opacity=Number(form.opacity).toFixed(3);
  if(normalizedFontWeight(element.styles.fontWeight)!==Number(form.fontWeight)) {
    style.fontWeight=String(Number(form.fontWeight));
  }
  const currentTextAlign=String(element.styles.textAlign||"left");
  if(currentTextAlign!==form.textAlign) style.textAlign=form.textAlign;
  const operation:Extract<FilmOperation,{op:"element_set"}>={op:"element_set",clip:element.clipId,selector:element.selector,style};
  if(element.textEditable)operation.text=form.text;
  return {ok:true,operation};
};
const readCanvasElementForm=():CanvasElementFormValues|null=>{
  const ids=["fs-element-text","fs-element-left","fs-element-top","fs-element-width","fs-element-height","fs-element-color",
    "fs-element-background-enabled","fs-element-background-color","fs-element-opacity","fs-element-font-weight","fs-element-text-align",
    "fs-element-font-size","fs-element-rotate"];
  const fields=ids.map(id=>document.getElementById(id) as HTMLInputElement|HTMLTextAreaElement|HTMLSelectElement|null);
  if(fields.some(field=>!field))return null;
  const [text,left,top,width,height,color,backgroundEnabled,backgroundColor,opacity,fontWeight,textAlign,fontSize,rotate]=fields as Array<HTMLInputElement|HTMLTextAreaElement|HTMLSelectElement>;
  return {
    text:text.value,left:left.value,top:top.value,width:width.value,height:height.value,color:color.value,
    backgroundEnabled:(backgroundEnabled as HTMLInputElement).checked,backgroundColor:backgroundColor.value,
    opacity:opacity.value,fontWeight:fontWeight.value,textAlign:textAlign.value,fontSize:fontSize.value,rotate:rotate.value
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
  const [newKeyframeEasing,setNewKeyframeEasing] = useState<FilmKeyframePreset>("linear");
  const [draggingKeyframe,setDraggingKeyframe] = useState<number|null>(null);
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
      const points=[...(existing?.keyframes||[]),{at:snapped,value,easing:newKeyframeEasing}].sort((a,b)=>a.at-b.at);
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
            {activeChannel?activeChannel.keyframes.map((point,index)=><div className="fs-keyframe-row" key={point.at}>
              <label>时间<input type="number" min={0} max={clipDuration} step={frameStep} value={point.at.toFixed(3)} disabled={readOnly} aria-label={"关键帧 "+(index+1)+" 时间"}
                onChange={event=>{const value=Number(event.currentTarget.value);if(Number.isFinite(value))updateKeyframePoint(index,{at:value});}}/></label>
              <label>数值<input type="number" min={animationInfo.min} max={animationInfo.max} step="0.01" value={point.value} disabled={readOnly} aria-label={"关键帧 "+(index+1)+" 数值"}
                onChange={event=>{const value=Number(event.currentTarget.value);if(Number.isFinite(value))updateKeyframePoint(index,{value});}}/></label>
              <label>缓动<select value={typeof point.easing==="string"?point.easing:"window"} disabled={readOnly} aria-label={"关键帧 "+(index+1)+" 缓动"}
                onChange={event=>{if(event.currentTarget.value!=="window")updateKeyframePoint(index,{easing:event.currentTarget.value as FilmKeyframePreset});}}>
                <option value="linear">线性</option><option value="ease_in">缓入</option><option value="ease_out">缓出</option><option value="ease_in_out">缓入缓出</option>
                <option value="window" disabled>裁剪保真的曲线分段</option>
              </select></label>
              <button type="button" className="fs-btn" aria-label={"删除关键帧 "+(index+1)} disabled={readOnly} onClick={()=>removeKeyframe(index)}>删除</button>
            </div>):<div className="fs-empty">该属性还没有关键帧。</div>}
          </div>
          <div className="fs-keyframe-add">
            <label className="fs-inspector-field">时间（相对秒）<input id="fs-keyframe-new-at" type="number" min={0} max={clip.duration} step={frameStep} value={newKeyframeAt} disabled={readOnly} onChange={event=>setNewKeyframeAt(event.currentTarget.value)}/></label>
            <label className="fs-inspector-field">数值<input id="fs-keyframe-new-value" type="number" min={animationInfo.min} max={animationInfo.max} step="0.01" value={newKeyframeValue} disabled={readOnly} onChange={event=>setNewKeyframeValue(event.currentTarget.value)}/></label>
            <label className="fs-inspector-field">缓动<select id="fs-keyframe-new-easing" value={newKeyframeEasing} disabled={readOnly} onChange={event=>setNewKeyframeEasing(event.currentTarget.value as FilmKeyframePreset)}>
              <option value="linear">线性</option><option value="ease_in">缓入</option><option value="ease_out">缓出</option><option value="ease_in_out">缓入缓出</option>
            </select></label>
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
  return (
    <>
      {proposal ? (
        <div className="fs-proposal-panel" id="fs-proposal-panel" data-react-ai-proposal="true">
          <strong id="fs-proposal-summary">{proposal.summary || "AI 候选修改"}</strong>
          <p id="fs-proposal-detail">{proposal.operationCount} 项操作 · 基于 revision {proposal.baseRevision} · 尚未写入正式文档</p>
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
            <button className="fs-btn" type="button" data-film-action="preview-proposal">重新预览</button>
            <button className="fs-btn" type="button" data-film-action="reject-proposal">拒绝</button>
            <button className="fs-btn fs-primary" type="button" data-film-action="apply-proposal">确认应用</button>
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

const StudioToolbar: React.FC = () => (
  <>
    <button className="fs-btn" type="button" data-film-action="undo">撤销</button>
    <button className="fs-btn" type="button" data-film-action="redo">重做</button>
    <button className="fs-btn" type="button" data-film-action="reload">重新载入</button>
    <button className="fs-btn fs-primary" type="button" data-film-action="render">导出 MP4</button>
    <button className="fs-btn" type="button" data-film-action="close" title="退出全屏 Studio，不切回旧编辑状态">退出 Studio</button>
  </>
);

const TimelineSurface: React.FC<{model: FilmTimelineSurfaceModel}> = ({model}) => (
  <>
    <div className="fs-ruler" style={{width:model.totalWidth}} data-react-timeline-surface="true">
      <div className="fs-ruler-head">时码</div>
      <div className="fs-ruler-body" id="fs-ruler-body" style={{width:model.laneWidth}}>
        {model.ticks.map((tick) => (
          <div className="fs-ruler-mark" data-at={tick.at} style={{left:tick.left}} key={tick.at}>{tick.label}</div>
        ))}
      </div>
    </div>
    {model.tracks.map((track) => (
      <div
        className={"fs-track" + (track.locked ? " fs-track-locked" : "") +
          (track.syncLocked ? " fs-sync-locked" : "") + (track.hidden ? " fs-hidden-track" : "") +
          (track.muted ? " fs-muted-track" : "")}
        data-track={track.id}
        key={track.id}
      >
        <div className="fs-track-head">
          <span className="fs-track-label" title={track.label}>{track.label}</span>
          <span className="fs-track-controls">
            <button type="button" className="fs-btn" data-track-lock={track.id} title={track.locked ? "解锁轨道" : "锁定轨道"}>{track.locked ? "🔒" : "锁"}</button>
            <button type="button" className="fs-btn" data-track-flag="sync-lock" data-track-key={track.id} aria-pressed={track.syncLocked ? "true" : "false"} title={track.syncLocked ? "取消同步锁定" : "启用同步锁定"}>{track.syncLocked ? "⇄" : "同步"}</button>
            <button type="button" className="fs-btn" data-track-flag="hidden" data-track-key={track.id} title={track.hidden ? "显示轨道" : "隐藏轨道"}>{track.hidden ? "显" : "隐"}</button>
            <button type="button" className="fs-btn" data-track-flag="muted" data-track-key={track.id} title={track.muted ? "取消静音" : "静音轨道"}>{track.muted ? "🔊" : "🔇"}</button>
          </span>
        </div>
        <div className="fs-track-body" style={{width:model.laneWidth}}>
          {track.clips.map((clip) => (
            <div
              className={"fs-clip" + (clip.selected ? " fs-clip-selected" : "") + (clip.locked ? " fs-clip-locked" : "")}
              data-clip={clip.id}
              data-clip-locked={clip.locked ? "true" : "false"}
              data-track={clip.trackId}
              data-kind={clip.tag}
              data-react-timeline-clip={clip.id}
              style={{left:clip.left,width:clip.width}}
              title={clip.title}
              key={clip.id}
              onClick={event => window.filmStudioClickClip?.(clip.id,event.nativeEvent)}
              onPointerDown={event => window.filmStudioBeginClipDrag?.(event.nativeEvent,event.currentTarget)}
              onPointerMove={event => window.filmStudioMoveClipGesture?.(event.nativeEvent)}
              onPointerUp={event => window.filmStudioEndClipGesture?.(event.nativeEvent)}
            >
              {clip.thumbnail ? <img className="fs-clip-thumb" src={clip.thumbnail} loading="lazy" alt="" onError={(event) => { event.currentTarget.style.display = "none"; }} /> : null}
              {clip.waveform ? <canvas className="fs-clip-waveform" data-waveform-clip={clip.id} aria-label="音频波形" /> : null}
              {clip.envelope ? <canvas className="fs-clip-envelope" data-envelope-clip={clip.id} aria-label="音量包络：拖动添加或调整关键帧；Shift 或右键删除内部点" /> : null}
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
                />
              ))}
              {clip.locked ? <span className="fs-clip-lock-mark" aria-label="片段已锁定">🔒</span> : null}
              {clip.trimHandles ? <>
                <span className="fs-trim-handle fs-trim-left" data-trim="start" title="修剪入点" />
                <span className="fs-trim-handle fs-trim-right" data-trim="end" title="修剪出点" />
              </> : null}
              <span className="fs-clip-title">{clip.title}</span>
              <span className="fs-clip-subtitle">{clip.atLabel}{clip.durationLabel}</span>
            </div>
          ))}
        </div>
      </div>
    ))}
    <div className="fs-playhead" id="fs-playhead-line" />
  </>
);

const StudioAssets: React.FC<{model: FilmStudioAssetsModel}> = ({model}) => {
  if (model.mode === "library") {
    if (!model.libraryAssets.length) {
      return <div className="fs-react-asset-items" data-react-asset-list="true">
        <div className="fs-empty">素材库没有可直接加入时间线的视频或图片。已上传的项目素材和全局素材会在此显示。</div>
      </div>;
    }
    return <div className="fs-react-asset-items" data-react-asset-list="true">
      {model.libraryAssets.map((asset) => (
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

const StudioScrubRow: React.FC = () => (
  <>
    <button className="fs-btn" type="button" data-film-action="frame-prev">上一帧</button>
    <button className="fs-btn" type="button" id="fs-play-toggle" data-film-action="play-toggle">播放</button>
    <input
      id="fs-playhead"
      type="range"
      min={0}
      max={10}
      step={0.033333}
      defaultValue={0}
      onInput={(event) => window.filmStudioSeek?.(event.currentTarget.value)}
      aria-label="播放头"
    />
    <button className="fs-btn" type="button" data-film-action="frame-next">下一帧</button>
    <button className="fs-btn" id="fs-element-inspect-toggle" type="button" data-film-action="canvas-inspect" aria-pressed="false" title="在节目监视器中点选 HTML 镜头里的文字或图层">选择画面元素</button>
    <span id="fs-playhead-value">00:00.000</span>
  </>
);

const StudioTimelineToolbar: React.FC = () => (
  <>
    <button className="fs-btn" type="button" data-film-action="insert-mode" aria-pressed="false" title="开启后，素材插入所选轨道并推移后续片段及同步锁定轨道">插入编辑：关</button>
    <button className="fs-btn" type="button" data-film-action="toggle-snap" aria-pressed="true" title="吸附到播放头、片头和其他片段边缘；吸附距离按屏幕像素计算">吸附：开</button>
    <button className="fs-btn" type="button" data-film-action="split" title="在播放头切分（S）">在播放头切分</button>
    <button className="fs-btn" type="button" data-film-action="copy-clips" title="复制选中片段（Ctrl/⌘+C）">复制</button>
    <button className="fs-btn" type="button" data-film-action="paste-clips" title="按原相对布局粘贴到播放头附近的可用区间（Ctrl/⌘+V）">粘贴</button>
    <button className="fs-btn" type="button" data-film-action="join-cut" title="合并同轨相邻、同源连续且属性一致的切分片段">愈合切口</button>
    <button className="fs-btn" type="button" data-film-action="remove">删除片段</button>
    <button className="fs-btn" type="button" data-film-action="ripple-delete" title="删除选中片段对应的时间段，并收拢全部轨道">波纹删除</button>
    <button className="fs-btn" type="button" data-film-action="envelope-mode" aria-pressed="false" title="切换音量关键帧编辑模式；普通模式下可正常拖动片段">音量曲线：关</button>
    <button className="fs-btn" type="button" data-film-action="slide-mode" aria-pressed="false" title="切换滑移修剪模式；拖动中间视频/音频片段主体，外侧时间线边界保持不变">滑移修剪：关</button>
    <button className="fs-btn" type="button" data-film-action="slip-mode" aria-pressed="false" title="切换源区间滑移模式；只改变素材源范围，片段时间线位置与长度不变">源区间滑移：关</button>
    <button className="fs-btn" type="button" data-film-action="add-track">＋轨道</button>
    <button className="fs-btn" type="button" data-film-action="remove-empty-track">删空轨</button>
    <button className="fs-btn" type="button" data-film-action="zoom-out">−</button>
    <button className="fs-btn" type="button" data-film-action="zoom-in">＋</button>
    <button className="fs-btn fs-primary" type="button" data-film-action="apply-inspector">应用属性</button>
  </>
);

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

type StudioReactState = {
  assets: FilmStudioAssetsModel | null;
  timeline: FilmTimelineSurfaceModel | null;
  toolbarVisible: boolean;
  timelineToolbarVisible: boolean;
  scrubVisible: boolean;
  aiModel: FilmStudioAiDirectorModel | null;
  clipInspectorMounted: boolean;
  clipInspector: FilmClipInspectorModel | null;
  clipInspectorEmptyMessage?: string;
  canvasInspectorMounted: boolean;
  canvasElement: CanvasElementSelection | null;
};

const studioReactState: StudioReactState = {
  assets: null,
  timeline: null,
  toolbarVisible: false,
  timelineToolbarVisible: false,
  scrubVisible: false,
  aiModel: null,
  clipInspectorMounted: false,
  clipInspector: null,
  clipInspectorEmptyMessage: undefined,
  canvasInspectorMounted: false,
  canvasElement: null,
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
const StudioReactOwner: React.FC<{state: StudioReactState}> = ({state}) => {
  const clip = state.clipInspector;
  const clipKey = clip
    ? clip.id + ":" + JSON.stringify(clip.attrs || {})
    : "empty:" + (state.clipInspectorEmptyMessage || "");
  const element = state.canvasElement;
  return (
    <>
      {state.assets ? studioPortal("fs-assets-list", <StudioAssets model={state.assets} />) : null}
      {state.timeline ? studioPortal("fs-timeline", <TimelineSurface model={state.timeline} />) : null}
      {state.toolbarVisible ? studioPortal("fs-react-toolbar", <StudioToolbar />) : null}
      {state.timelineToolbarVisible ? studioPortal("fs-react-timeline-toolbar", <StudioTimelineToolbar />) : null}
      {state.scrubVisible ? studioPortal("fs-react-scrub-row", <StudioScrubRow />) : null}
      {state.aiModel ? studioPortal("fs-react-ai-director", <StudioAiDirector key="ai-director" model={state.aiModel} />) : null}
      {state.clipInspectorMounted ? studioPortal(
        "fs-inspector",
        <ClipInspector key={clipKey} clip={clip} emptyMessage={state.clipInspectorEmptyMessage} />
      ) : null}
      {state.canvasInspectorMounted ? studioPortal(
        "fs-react-canvas-inspector",
        element ? <CanvasInspector key={element.clipId + "::" + element.selector} element={element} /> : null
      ) : null}
    </>
  );
};

function renderStudioReactOwner(patch: Partial<StudioReactState>): void {
  Object.assign(studioReactState, patch);
  const mount = document.getElementById("fs-react-root");
  if (!mount) return;
  if (!studioRoot || studioRootMount !== mount) {
    studioRoot = createRoot(mount);
    studioRootMount = mount;
  }
  const snapshot = {...studioReactState};
  flushSync(() => {
    studioRoot?.render(<StudioReactOwner state={snapshot} />);
  });
}

window.filmStudioReactAssets = {
  render(model) {
    renderStudioReactOwner({assets:model});
  },
};

window.filmStudioReactTimeline = {
  renderSurface(model) {
    renderStudioReactOwner({timeline:model});
  },
};

window.filmStudioReactToolbar = {
  render() {
    renderStudioReactOwner({toolbarVisible:true});
  },
  renderTimeline() {
    renderStudioReactOwner({timelineToolbarVisible:true});
  },
  renderScrub() {
    renderStudioReactOwner({scrubVisible:true});
  },
};

window.filmStudioReactAi = {
  render(model) {
    renderStudioReactOwner({aiModel:model});
  },
};

let activeCanvasSelection: CanvasElementSelection | null = null;

window.filmStudioReactInspector = {
  saveSelected() {
    const element=activeCanvasSelection;
    if(!element){window.filmStudioShowInspectorError?.("请先在预览画布中选中一个 HTML 元素。");return;}
    const form=readCanvasElementForm();
    if(!form){window.filmStudioShowInspectorError?.("画布检查器尚未挂载完成，请重新选择元素。");return;}
    const result=buildCanvasElementOperation(element,form);
    if(!result.ok){window.filmStudioShowInspectorError?.(result.message);return;}
    if(!window.filmStudioCommitCanvasElement){
      window.filmStudioShowInspectorError?.("画布编辑提交桥尚未就绪；本次没有写入 film.html。");return;
    }
    return window.filmStudioCommitCanvasElement(result.operation, {allowInspectorDraft:true});
  },
  renderClip(clip, emptyMessage) {
    renderStudioReactOwner({
      clipInspectorMounted:true,
      clipInspector:clip,
      clipInspectorEmptyMessage:emptyMessage,
    });
  },
  render(element) {
    activeCanvasSelection=element;
    renderStudioReactOwner({
      canvasInspectorMounted:true,
      canvasElement:element,
    });
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
