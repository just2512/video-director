import React from "react";
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

declare global {
  interface Window {
    filmStudioReactInspector?: CanvasInspectorBridge;
    filmStudioReactToolbar?: FilmStudioReactToolbarBridge;
    filmStudioSaveSelectedElement?: () => Promise<void> | void;
    filmStudioSaveInspector?: () => Promise<void> | void;
    filmStudioSeek?: (value: string | number) => void;
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
  if (!clip) {
    return <div className="fs-empty" data-react-clip-inspector-empty="true">{emptyMessage || "选择时间线片段后编辑属性。"}</div>;
  }
  const attrs = clip.attrs || {};
  const speed = Number(attrs.speed || 1);
  const fade = String(attrs.fade || "0,0").split(",");
  const muted = Object.prototype.hasOwnProperty.call(attrs, "muted");
  return (
    <div data-react-clip-inspector="true">
      <div className="fs-inspector-description">
        <strong>{clip.id}</strong><br />{clip.tag}<br />{attrs.src || ""}
      </div>
      <div className="fs-inspector-section">
        <label className="fs-inspector-field">
          时间位置（秒）
          <input id="fs-field-at" type="number" min={0} step={0.033333} defaultValue={String(attrs.at || clip.at || 0)} />
        </label>
        {(clip.tag === "video" || clip.tag === "audio") && (
          <>
            <label className="fs-inspector-field">
              速度
              <input id="fs-field-speed" type="number" min={0.25} max={4} step={0.05} defaultValue={String(attrs.speed || 1)} />
            </label>
            <div className="fs-speed-presets">
              <span>预设倍率</span>
              <div>{[0.5, 1, 1.5, 2, 4].map((value) => (
                <button
                  type="button"
                  key={value}
                  className={"fs-speed-chip" + (Math.abs(speed - value) < 0.001 ? " fs-speed-chip-active" : "")}
                  data-speed-preset={value}
                  title={"设置速度为 " + value + " 倍"}
                >{value}×</button>
              ))}</div>
            </div>
            <label className="fs-inspector-field">
              音量
              <input id="fs-field-volume" type="number" min={0} max={2} step={0.05} defaultValue={String(attrs.volume == null ? 1 : attrs.volume)} />
            </label>
            <label className="fs-inspector-field">
              静音
              <select id="fs-field-muted" defaultValue={muted ? "true" : "false"}>
                <option value="false">否</option><option value="true">是</option>
              </select>
            </label>
          </>
        )}
        {clip.duration != null && (
          <>
            <label className="fs-inspector-field">
              淡入（秒）
              <input id="fs-field-fade-in" type="number" min={0} max={60} step={0.05} defaultValue={fade[0] || "0"} />
            </label>
            <label className="fs-inspector-field">
              淡出（秒）
              <input id="fs-field-fade-out" type="number" min={0} max={60} step={0.05} defaultValue={fade[1] || "0"} />
            </label>
          </>
        )}
        <label className="fs-inspector-field">
          片段标题
          <input id="fs-field-title" type="text" maxLength={200} defaultValue={attrs.title || ""} />
        </label>
      </div>
      <div className="fs-inspector-description fs-inspector-section">
        更改经统一编辑 API 写入 film.html。锁定轨道会拒绝修改；同轨片段不可重叠。
      </div>
      <button
        type="button"
        id="fs-apply-clip-inspector"
        className="fs-btn fs-primary"
        onClick={() => { void window.filmStudioSaveInspector?.(); }}
      >应用片段属性</button>
    </div>
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
