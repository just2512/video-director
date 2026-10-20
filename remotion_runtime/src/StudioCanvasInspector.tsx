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
interface CanvasInspectorBridge {
  render(element: CanvasElementSelection | null): void;
  sync(element: CanvasElementSelection): void;
}

declare global {
  interface Window {
    filmStudioReactInspector?: CanvasInspectorBridge;
    filmStudioSaveSelectedElement?: () => Promise<void> | void;
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

let activeRoot: Root | null = null;
let activeMount: HTMLElement | null = null;

window.filmStudioReactInspector = {
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
