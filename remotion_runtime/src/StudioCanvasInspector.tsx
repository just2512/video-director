import React, {useState} from "react";
import {flushSync} from "react-dom";
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
}
interface CanvasInspectorBridge {
  render(element: CanvasElementSelection | null): void;
  renderClip(clip: FilmClipInspectorModel | null, emptyMessage?: string): void;
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
  left: number; width: number; atLabel: string; durationLabel: string;
  selected: boolean; locked: boolean; thumbnail: string; waveform: boolean; envelope: boolean; trimHandles: boolean;
};
type FilmTimelineSurfaceTrack = {
  id: string; label: string; locked: boolean; syncLocked: boolean; hidden: boolean; muted: boolean;
  clips: FilmTimelineSurfaceClip[];
};
type FilmTimelineSurfaceModel = {
  renderKey: string; laneWidth: number; totalWidth: number; pixelsPerSecond: number;
  ticks: Array<{at:number;label:string;left:number}>;
  tracks: FilmTimelineSurfaceTrack[];
};
interface FilmStudioReactTimelineBridge {
  renderSurface(model: FilmTimelineSurfaceModel): void;
}
type FilmStudioAiMessage = {text: string; role: string};
type FilmStudioAiProposal = {summary: string; operationCount: number; baseRevision: string} | null;
type FilmStudioAiDirectorModel = {messages: FilmStudioAiMessage[]; proposal: FilmStudioAiProposal};
interface FilmStudioReactAiBridge {
  render(model: FilmStudioAiDirectorModel): void;
}

declare global {
  interface Window {
    filmStudioReactInspector?: CanvasInspectorBridge;
    filmStudioReactToolbar?: FilmStudioReactToolbarBridge;
    filmStudioSaveSelectedElement?: () => Promise<void> | void;
    filmStudioSaveInspector?: (values?: FilmClipInspectorValues) => Promise<void> | void;
    filmStudioSetClipLock?: (clipId: string, locked: boolean) => Promise<void> | void;
    filmStudioSeek?: (value: string | number) => void;
    filmStudioReactAssets?: FilmStudioReactAssetsBridge;
    filmStudioReactTimeline?: FilmStudioReactTimelineBridge;
    filmStudioReactAi?: FilmStudioReactAiBridge;
    filmStudioAsk?: () => Promise<void> | void;
    filmStudioSelectClip?: (clipId: string) => void;
    filmStudioShowAssetMode?: (mode: string) => void;
    filmStudioAddAsset?: (assetId: string) => Promise<void> | void;
  }
}

const asNumber = (value: unknown, fallback: number): number => {
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : fallback;
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

const rotateDegrees = (styles: Record<string, string>): number => {
  const match = String(styles.rotate || "").match(/-?\d+(?:\.\d+)?/);
  return match ? Number(match[0]) : 0;
};

const ClipInspector: React.FC<{clip: FilmClipInspectorModel | null; emptyMessage?: string}> = ({clip, emptyMessage}) => {
  const [form, setForm] = useState<FilmClipInspectorValues>(() => {
    const attrs = clip?.attrs || {};
    const fade = String(attrs.fade || "0,0").split(",");
    return {
      at: String(attrs.at ?? clip?.at ?? 0),
      speed: String(attrs.speed || 1),
      volume: String(attrs.volume == null ? 1 : attrs.volume),
      muted: Object.prototype.hasOwnProperty.call(attrs, "muted") ? "true" : "false",
      fadeIn: fade[0] || "0",
      fadeOut: fade[1] || "0",
      title: String(attrs.title || ""),
    };
  });
  const update = <K extends keyof FilmClipInspectorValues>(field: K, value: FilmClipInspectorValues[K]) => {
    setForm((current) => ({...current, [field]: value}));
  };
  if (!clip) {
    return <div className="fs-empty" data-react-clip-inspector-empty="true">{emptyMessage || "选择时间线片段后编辑属性。"}</div>;
  }
  const attrs = clip.attrs || {};
  const clipLocked = Object.prototype.hasOwnProperty.call(attrs, "data-clip-locked");
  const speed = Number(form.speed || 1);
  const hasMediaControls = clip.tag === "video" || clip.tag === "audio";
  return (
    <div data-react-clip-inspector="true">
      <div className="fs-inspector-description">
        <strong>{clip.id}</strong><br />{clip.tag}<br />{attrs.src || ""}
      </div>
      <div className="fs-inspector-section">
        <label className="fs-inspector-field">
          时间位置（秒）
          <input id="fs-field-at" type="number" min={0} step={0.033333} value={form.at} disabled={clipLocked}
            onChange={(event) => update("at", event.currentTarget.value)} />
        </label>
        {hasMediaControls && (
          <>
            <label className="fs-inspector-field">
              速度
              <input id="fs-field-speed" type="number" min={0.25} max={4} step={0.05} value={form.speed} disabled={clipLocked}
                onChange={(event) => update("speed", event.currentTarget.value)} />
            </label>
            <div className="fs-speed-presets">
              <span>预设倍率</span>
              <div>{[0.5, 1, 1.5, 2, 4].map((value) => (
                <button type="button" key={value}
                  className={"fs-speed-chip" + (Math.abs(speed - value) < 0.001 ? " fs-speed-chip-active" : "")}
                  data-speed-preset={value} title={"设置速度为 " + value + " 倍"} disabled={clipLocked}
                  onClick={() => update("speed", String(value))}
                >{value}×</button>
              ))}</div>
            </div>
            <label className="fs-inspector-field">
              音量
              <input id="fs-field-volume" type="number" min={0} max={2} step={0.05} value={form.volume} disabled={clipLocked}
                onChange={(event) => update("volume", event.currentTarget.value)} />
            </label>
            <label className="fs-inspector-field">
              静音
              <select id="fs-field-muted" value={form.muted} disabled={clipLocked}
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
              <input id="fs-field-fade-in" type="number" min={0} max={60} step={0.05} value={form.fadeIn} disabled={clipLocked}
                onChange={(event) => update("fadeIn", event.currentTarget.value)} />
            </label>
            <label className="fs-inspector-field">
              淡出（秒）
              <input id="fs-field-fade-out" type="number" min={0} max={60} step={0.05} value={form.fadeOut} disabled={clipLocked}
                onChange={(event) => update("fadeOut", event.currentTarget.value)} />
            </label>
          </>
        )}
        <label className="fs-inspector-field">
          片段标题
          <input id="fs-field-title" type="text" maxLength={200} value={form.title} disabled={clipLocked}
            onChange={(event) => update("title", event.currentTarget.value)} />
        </label>
      </div>
      <div className="fs-inspector-description fs-inspector-section">
        更改经统一编辑 API 写入 film.html。锁定轨道会拒绝修改；同轨片段不可重叠。
      </div>
      {clipLocked ? <div className="fs-alert fs-visible fs-error" role="status">该片段来自已锁定图层，只读保留其原始时间线位置与画面。</div> : null}
      <button type="button" id="fs-apply-clip-inspector" className="fs-btn fs-primary" disabled={clipLocked}
        onClick={() => { void window.filmStudioSaveInspector?.({...form}); }}
      >应用片段属性</button>
      <button type="button" id="fs-toggle-clip-lock" className="fs-btn"
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
            >
              {clip.thumbnail ? <img className="fs-clip-thumb" src={clip.thumbnail} loading="lazy" alt="" onError={(event) => { event.currentTarget.style.display = "none"; }} /> : null}
              {clip.waveform ? <canvas className="fs-clip-waveform" data-waveform-clip={clip.id} aria-label="音频波形" /> : null}
              {clip.envelope ? <canvas className="fs-clip-envelope" data-envelope-clip={clip.id} aria-label="音量包络：拖动添加或调整关键帧；Shift 或右键删除内部点" /> : null}
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
    <button className="fs-btn" type="button" data-film-action="split" title="在播放头切分（S）">在播放头切分</button>
    <button className="fs-btn" type="button" data-film-action="join-cut" title="合并同轨相邻、同源连续且属性一致的切分片段">愈合切口</button>
    <button className="fs-btn" type="button" data-film-action="remove">删除片段</button>
    <button className="fs-btn" type="button" data-film-action="ripple-delete" title="删除选中片段对应的时间段，并收拢全部轨道">波纹删除</button>
    <button className="fs-btn" type="button" data-film-action="envelope-mode" aria-pressed="false" title="切换音量关键帧编辑模式；普通模式下可正常拖动片段">音量曲线：关</button>
    <button className="fs-btn" type="button" data-film-action="slide-mode" aria-pressed="false" title="切换滑移修剪模式；拖动中间视频/音频片段主体，外侧时间线边界保持不变">滑移修剪：关</button>
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
  return (
    <section className="fs-inspector-element" data-react-canvas-inspector="true">
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
        className="fs-btn fs-primary"
        id="fs-apply-element"
        onClick={() => { void window.filmStudioSaveSelectedElement?.(); }}
      >
        应用画布修改
      </button>
    </section>
  );
};

let assetsRoot: Root | null = null;
let assetsMount: HTMLElement | null = null;
window.filmStudioReactAssets = {
  render(model) {
    const mount = document.getElementById("fs-assets-list");
    if (!mount) return;
    if (!assetsRoot || assetsMount !== mount) {
      assetsRoot = createRoot(mount);
      assetsMount = mount;
    }
    flushSync(() => {
      assetsRoot?.render(<StudioAssets model={model} />);
    });
  },
};

let timelineSurfaceRoot: Root | null = null;
let timelineSurfaceMount: HTMLElement | null = null;
window.filmStudioReactTimeline = {
  renderSurface(model) {
    const mount = document.getElementById("fs-timeline");
    if (!mount) return;
    if (!timelineSurfaceRoot || timelineSurfaceMount !== mount) {
      timelineSurfaceRoot = createRoot(mount);
      timelineSurfaceMount = mount;
    }
    flushSync(() => {
      timelineSurfaceRoot?.render(<TimelineSurface key={model.renderKey} model={model} />);
    });
  },
};

let toolbarRoot: Root | null = null;
let toolbarMount: HTMLElement | null = null;
let timelineToolbarRoot: Root | null = null;
let timelineToolbarMount: HTMLElement | null = null;
let scrubRoot: Root | null = null;
let scrubMount: HTMLElement | null = null;
window.filmStudioReactToolbar = {
  render() {
    const mount = document.getElementById("fs-react-toolbar");
    if (!mount) return;
    if (!toolbarRoot || toolbarMount !== mount) {
      toolbarRoot = createRoot(mount);
      toolbarMount = mount;
    }
    flushSync(() => {
      toolbarRoot?.render(<StudioToolbar />);
    });
  },
  renderTimeline() {
    const mount = document.getElementById("fs-react-timeline-toolbar");
    if (!mount) return;
    if (!timelineToolbarRoot || timelineToolbarMount !== mount) {
      timelineToolbarRoot = createRoot(mount);
      timelineToolbarMount = mount;
    }
    flushSync(() => {
      timelineToolbarRoot?.render(<StudioTimelineToolbar />);
    });
  },
  renderScrub() {
    const mount = document.getElementById("fs-react-scrub-row");
    if (!mount) return;
    if (!scrubRoot || scrubMount !== mount) {
      scrubRoot = createRoot(mount);
      scrubMount = mount;
    }
    flushSync(() => {
      scrubRoot?.render(<StudioScrubRow />);
    });
  },
};

let aiRoot: Root | null = null;
let aiMount: HTMLElement | null = null;
window.filmStudioReactAi = {
  render(model) {
    const mount = document.getElementById("fs-react-ai-director");
    if (!mount) return;
    if (!aiRoot || aiMount !== mount) {
      aiRoot = createRoot(mount);
      aiMount = mount;
    }
    flushSync(() => {
      aiRoot?.render(<StudioAiDirector key="ai-director" model={model} />);
    });
  },
};

let activeRoot: Root | null = null;
let activeMount: HTMLElement | null = null;
let clipRoot: Root | null = null;
let clipMount: HTMLElement | null = null;

window.filmStudioReactInspector = {
  renderClip(clip, emptyMessage) {
    const mount = document.getElementById("fs-inspector");
    if (!mount) return;
    if (!clipRoot || clipMount !== mount) {
      clipRoot = createRoot(mount);
      clipMount = mount;
    }
    const key = clip ? clip.id + ":" + JSON.stringify(clip.attrs || {}) : "empty:" + (emptyMessage || "");
    flushSync(() => {
      clipRoot?.render(<ClipInspector key={key} clip={clip} emptyMessage={emptyMessage} />);
    });
  },
  render(element) {
    const mount = document.getElementById("fs-react-canvas-inspector");
    if (!mount) return;
    if (!activeRoot || activeMount !== mount) {
      activeRoot = createRoot(mount);
      activeMount = mount;
    }
    flushSync(() => {
      activeRoot?.render(
        element ? <CanvasInspector key={element.clipId + "::" + element.selector} element={element} /> : null
      );
    });
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
      ["fs-element-font-size", String(asNumber(parseFloat(styles.fontSize), 24))],
      ["fs-element-rotate", String(rotateDegrees(styles))],
    ];
    for (const [id, value] of values) {
      const input = document.getElementById(id) as HTMLInputElement | HTMLTextAreaElement | null;
      if (input && !(id === "fs-element-text" && input.disabled)) input.value = value;
    }
  },
};
