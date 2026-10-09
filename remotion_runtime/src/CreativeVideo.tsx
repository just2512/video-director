import React from "react";
import {
  AbsoluteFill, Audio, Easing, Img, OffthreadVideo, Sequence,
  interpolate, useCurrentFrame, useVideoConfig,
} from "remotion";

export type MotionKeyframe = {at: number; value: number | string; easing?: {type?: string; mode?: string}};
export type MotionChannel = {property: string; keyframes: MotionKeyframe[]; interpolation?: string};
export type CreativeLayer = {
  id: string; kind: string; text?: string; src?: string | null;
  background?: string; color?: string; zIndex?: number; motion?: MotionChannel[];
  sourceDurationFrames?: number; trimStartFrames?: number;
};
export type CreativeCharacter = {
  index: number; text: string; startMs: number; endMs: number; source: string; reviewRequired: boolean;
};
export type CreativeShot = {
  id: string; recipeId?: string; category?: string; intent?: string; text: string;
  startFrame: number; durationFrames: number; startMs: number; transition?: string | null;
  layers: CreativeLayer[]; characters?: CreativeCharacter[];
};
export type CreativeVideoProps = {
  width: number; height: number; fps: number; durationInFrames: number;
  shots: CreativeShot[]; audioUrl?: string | null; audioStartFrame?: number;
};

const clamp = (v: number, min: number, max: number) => Math.min(max, Math.max(min, v));

const easingFor = (type?: string, mode?: string) => {
  const base =
    type === "linear" ? Easing.linear :
    type === "sine" ? Easing.sin :
    type === "quad" ? Easing.quad :
    type === "cubic" ? Easing.cubic :
    type === "quart" ? Easing.poly(4) :
    type === "expo" ? Easing.exp :
    type === "circ" ? Easing.circle :
    type === "back" ? Easing.back : Easing.cubic;
  if (mode === "in") return Easing.in(base);
  if (mode === "inOut") return Easing.inOut(base);
  return Easing.out(base);
};

const channelAt = (frame: number, duration: number, channel: MotionChannel, fallback: number) => {
  const points = [...(channel.keyframes || [])].sort((a, b) => a.at - b.at);
  if (!points.length) return fallback;
  if (points.length === 1) return Number(points[0].value) || fallback;
  const progress = duration <= 1 ? 1 : clamp(frame / (duration - 1), 0, 1);
  if (progress <= points[0].at) return Number(points[0].value) || 0;
  for (let i = 1; i < points.length; i++) {
    const left = points[i - 1], right = points[i];
    if (progress <= right.at) {
      const span = right.at - left.at;
      const local = span <= 0 ? 1 : clamp((progress - left.at) / span, 0, 1);
      const eased = easingFor(right.easing?.type, right.easing?.mode)(local);
      const a = Number(left.value), b = Number(right.value);
      return a + (b - a) * eased;
    }
  }
  return Number(points[points.length - 1].value) || fallback;
};

const styleForLayer = (layer: CreativeLayer, frame: number, duration: number): React.CSSProperties => {
  const values: Record<string, number> = {
    opacity: 1, x: 0, y: 0, scale: 1, scaleX: 1, scaleY: 1, rotation: 0, blur: 0,
  };
  for (const channel of layer.motion || []) {
    if (!(channel.property in values)) continue;
    values[channel.property] = channelAt(frame, duration, channel, values[channel.property]);
  }
  return {
    position: "absolute", inset: 0, zIndex: Number(layer.zIndex || 0),
    opacity: clamp(values.opacity, 0, 1),
    transform: "translate3d(" + values.x + "px," + values.y + "px,0) scale(" +
      (values.scale * values.scaleX) + "," + (values.scale * values.scaleY) + ") rotate(" +
      values.rotation + "deg)",
    filter: values.blur > 0 ? "blur(" + Math.min(24, values.blur) + "px)" : undefined,
    willChange: "transform, opacity, filter",
  };
};

const paletteFor = (shot: CreativeShot) => {
  const key = (String(shot.recipeId || "") + " " + String(shot.category || "")).toLowerCase();
  if (/spotlight|cinematic|hero|documentary/.test(key)) {
    return {bg: "#111318", fg: "#f8f7f2", accent: "#f3c969", secondary: "#b8c2d1"};
  }
  if (/data|number|product/.test(key)) {
    return {bg: "#f4f1e9", fg: "#17191f", accent: "#ef6a47", secondary: "#4f5968"};
  }
  return {bg: "#f4f3ef", fg: "#20232a", accent: "#5468ff", secondary: "#5e6571"};
};

const MotionLayer: React.FC<{
  layer: CreativeLayer; frame: number; duration: number; palette: ReturnType<typeof paletteFor>;
}> = ({layer, frame, duration, palette}) => {
  const motionStyle = styleForLayer(layer, frame, duration);
  const kind = (layer.kind || "").toLowerCase();
  if (kind === "background") {
    return <AbsoluteFill style={{...motionStyle, background: layer.background || palette.bg}} />;
  }
  if ((kind === "video" || kind === "person" || kind === "screen") && layer.src) {
    return <AbsoluteFill style={motionStyle}>
      <OffthreadVideo src={layer.src} muted startFrom={Math.max(0, Number(layer.trimStartFrames || 0))}
        style={{width: "100%", height: "100%", objectFit: "cover"}} />
    </AbsoluteFill>;
  }
  if ((kind === "image" || kind === "logo" || kind === "graphic" || kind === "product") && layer.src) {
    return <AbsoluteFill style={motionStyle}>
      <Img src={layer.src} style={{width: "100%", height: "100%", objectFit: "contain"}} />
    </AbsoluteFill>;
  }
  if (kind === "shape") {
    const dataShape = /data|number/.test((layer.id + " " + (layer.text || "")).toLowerCase());
    return <AbsoluteFill style={motionStyle}>
      <div style={{
        position: "absolute", width: dataShape ? "54%" : "28%", height: dataShape ? "8%" : "28%",
        left: dataShape ? "23%" : "36%", top: dataShape ? "68%" : "35%",
        background: layer.background || palette.accent, borderRadius: dataShape ? 12 : 999,
        opacity: 0.9, boxShadow: "0 20px 70px rgba(0,0,0,.16)",
      }} />
    </AbsoluteFill>;
  }
  if (["text", "title", "subtitle", "lower_third"].includes(kind)) {
    const isTitle = kind === "title" || /title|hero|number/.test(layer.id.toLowerCase());
    return <AbsoluteFill style={{...motionStyle, padding: "10% 8%", justifyContent: "center", alignItems: "center"}}>
      <div style={{
        maxWidth: "92%", color: layer.color || palette.fg,
        fontSize: isTitle ? "clamp(32px, 7.4vw, 104px)" : "clamp(22px, 3vw, 44px)",
        fontWeight: 800, lineHeight: 1.12, letterSpacing: "-0.035em",
        textAlign: "center", overflowWrap: "anywhere",
        textShadow: palette.bg === "#111318" ? "0 8px 35px rgba(0,0,0,.45)" : "none",
      }}>{layer.text || ""}</div>
    </AbsoluteFill>;
  }
  return <AbsoluteFill style={{...motionStyle, justifyContent: "center", alignItems: "center", padding: 36}}>
    <div style={{border: "1px dashed #9ca3af", color: palette.secondary, padding: 20, borderRadius: 12}}>
      Unsupported visual layer: {kind || "unknown"}
    </div>
  </AbsoluteFill>;
};

const TimedSubtitle: React.FC<{
  shot: CreativeShot; frame: number; fps: number; palette: ReturnType<typeof paletteFor>;
}> = ({shot, frame, fps, palette}) => {
  const chars = shot.characters || [];
  if (!shot.text) return null;
  const nowMs = shot.startMs + frame * 1000 / fps;
  const byIndex = new Map(chars.map((c) => [c.index, c]));
  const spans: React.ReactNode[] = [];
  let lastSpoken = false;
  for (let i = 0; i < shot.text.length; i++) {
    const ch = shot.text[i];
    const timing = byIndex.get(i);
    const punctuation = /[\s\p{P}\p{S}]/u.test(ch);
    const spoken = timing ? nowMs >= timing.startMs : punctuation && lastSpoken;
    if (timing && nowMs >= timing.startMs) lastSpoken = true;
    if (!punctuation && !timing) lastSpoken = false;
    const active = !!timing && nowMs >= timing.startMs && nowMs <= timing.endMs;
    spans.push(<span key={i} style={{
      opacity: chars.length ? (spoken ? 1 : 0.2) : 1,
      color: active ? palette.accent : palette.fg,
      background: active ? "rgba(255,255,255,.08)" : "transparent", borderRadius: 3,
    }}>{ch}</span>);
  }
  return <div style={{
    position: "absolute", zIndex: 1000, left: "6%", right: "6%", bottom: "6%",
    padding: "13px 18px", borderRadius: 9,
    background: palette.bg === "#111318" ? "rgba(10,12,16,.70)" : "rgba(255,255,255,.84)",
    color: palette.fg, fontSize: "clamp(20px, 3.2vw, 39px)", fontWeight: 650,
    lineHeight: 1.25, textAlign: "center", backdropFilter: "blur(8px)", overflowWrap: "anywhere",
  }}>{spans}</div>;
};

const ShotStage: React.FC<{shot: CreativeShot; fps: number}> = ({shot, fps}) => {
  const frame = useCurrentFrame();
  const palette = paletteFor(shot);
  const duration = Math.max(1, shot.durationFrames);
  const cameraScale = 1 + 0.032 * interpolate(frame, [0, Math.max(1, duration - 1)], [0, 1], {
    extrapolateLeft: "clamp", extrapolateRight: "clamp",
  });
  const crossfade = shot.transition === "crossfade";
  const edge = crossfade ? Math.min(
    1, frame / Math.max(1, Math.round(fps * 0.25)),
    (duration - 1 - frame) / Math.max(1, Math.round(fps * 0.25)),
  ) : 1;
  const layers = [...(shot.layers || [])].sort((a, b) => Number(a.zIndex || 0) - Number(b.zIndex || 0));
  return <AbsoluteFill style={{background: palette.bg, color: palette.fg, overflow: "hidden"}}>
    <AbsoluteFill style={{transform: "scale(" + cameraScale + ")", opacity: clamp(edge, 0, 1), overflow: "hidden"}}>
      {layers.map((layer) => <MotionLayer key={layer.id} layer={layer} frame={frame} duration={duration} palette={palette} />)}
      {!layers.length && <AbsoluteFill style={{justifyContent: "center", alignItems: "center", padding: "10%"}}>
        <div style={{fontSize: "clamp(28px, 6vw, 76px)", fontWeight: 800, textAlign: "center"}}>{shot.text}</div>
      </AbsoluteFill>}
    </AbsoluteFill>
    <TimedSubtitle shot={shot} frame={frame} fps={fps} palette={palette} />
  </AbsoluteFill>;
};

export const CreativeVideo: React.FC<CreativeVideoProps> = (props) => {
  const shots = [...(props.shots || [])].sort((a, b) => a.startFrame - b.startFrame);
  return <AbsoluteFill style={{
    background: "#111318",
    fontFamily: "'Noto Sans CJK SC', 'Microsoft YaHei', system-ui, sans-serif",
  }}>
    {shots.map((shot) => <Sequence key={shot.id} from={Math.max(0, shot.startFrame)}
      durationInFrames={Math.max(1, shot.durationFrames)} name={shot.id}>
      <ShotStage shot={shot} fps={props.fps} />
    </Sequence>)}
    {props.audioUrl ? <Sequence from={Math.max(0, props.audioStartFrame || 0)}
      durationInFrames={Math.max(1, props.durationInFrames - Math.max(0, props.audioStartFrame || 0))}>
      <Audio src={props.audioUrl} />
    </Sequence> : null}
  </AbsoluteFill>;
};
