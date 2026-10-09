import React from "react";
import {
  AbsoluteFill,
  Audio,
  Easing,
  Img,
  OffthreadVideo,
  Sequence,
  interpolate,
  staticFile,
  useCurrentFrame,
  useVideoConfig,
} from "remotion";

type TimingChar = {
  text: string;
  script_index: number;
  start: number;
  end: number;
  timing_source: string;
  review_required?: boolean;
};
type Beat = {
  id: string;
  sem: string[];
  need: string[];
  entities: string[];
  weight: "main" | "sub";
  intent: string;
  visual_intent: string;
  semantic_role: string;
};
type LayerPlan = {
  index: number;
  type: string;
  src: string | null;
  asset_id: string | null;
  start: number;
  duration: number;
  source_in: number;
  speed: number;
  fit_mode: "cover" | "contain";
  text: string | null;
  motion_recipe_id: string | null;
  motion_instance: Record<string, unknown> | null;
  transform: {x?: number; y?: number; scale?: number; opacity?: number; rotation?: number} | null;
};
type ShotPlan = {
  id: string;
  start: number;
  duration: number;
  text: string;
  intent: string;
  visual_intent: string;
  semantic_role: string;
  transition_out: string;
  layers: LayerPlan[];
  beat: Beat;
  characters: TimingChar[];
};
type AudioPlan = {
  id: string;
  type: string;
  src: string;
  start: number;
  duration: number;
  source_in: number;
  volume: number;
  muted: boolean;
  fadeIn: number;
  fadeOut: number;
  ducking: boolean;
};
type RecipeChannel = {
  property: string;
  keyframes: Array<{at: number; value: number; easing?: {type?: string; mode?: string; parameters?: Record<string, number>} | null}>;
};
type RecipePlan = {
  id: string;
  duration?: {min?: number; recommended?: number; max?: number};
  channels: RecipeChannel[];
  parameters?: Array<{name: string; default: number; min?: number; max?: number}>;
};
export type TalkCraftProps = {
  width: number;
  height: number;
  fps: number;
  durationInFrames: number;
  scenes: ShotPlan[];
  audio: AudioPlan[];
  recipes: Record<string, RecipePlan>;
  narrationSrc: string | null;
  duckingWindows: Array<[number, number]>;
  title: string;
};

const COLORS = {
  canvas: "#10131b",
  panel: "#171d29",
  ink: "#f3f5f8",
  muted: "#a9b4c4",
  accent: "#b8f36a",
  line: "rgba(255,255,255,.18)",
  quote: "#f6cf71",
};

const frames = (sec: number, fps: number) => Math.max(0, Math.round(sec * fps));
const src = (path: string) => staticFile(path.replace(/^\/+/, ""));

function easingProgress(t: number, easing?: RecipeChannel["keyframes"][number]["easing"]): number {
  const mode = easing?.mode || "inOut";
  const type = easing?.type || "linear";
  const base = Math.max(0, Math.min(1, t));
  let value = base;
  if (type === "sine") value = mode === "out" ? Math.sin(base * Math.PI / 2) : mode === "in" ? 1 - Math.cos(base * Math.PI / 2) : -(Math.cos(Math.PI * base) - 1) / 2;
  else if (type === "quad") value = mode === "out" ? 1 - (1 - base) ** 2 : mode === "in" ? base ** 2 : base < .5 ? 2 * base ** 2 : 1 - (-2 * base + 2) ** 2 / 2;
  else if (type === "cubic") value = mode === "out" ? 1 - (1 - base) ** 3 : mode === "in" ? base ** 3 : base < .5 ? 4 * base ** 3 : 1 - (-2 * base + 2) ** 3 / 2;
  else if (type === "expo") value = mode === "out" ? (base === 1 ? 1 : 1 - 2 ** (-10 * base)) : mode === "in" ? (base === 0 ? 0 : 2 ** (10 * base - 10)) : base < .5 ? 2 ** (20 * base - 10) / 2 : (2 - 2 ** (-20 * base + 10)) / 2;
  else if (type === "back") {
    const overshoot = easing?.parameters?.overshoot ?? 1.2;
    value = mode === "out" ? 1 + (overshoot + 1) * (base - 1) ** 3 + overshoot * (base - 1) ** 2 : base ** 2 * ((overshoot + 1) * base - overshoot);
  }
  return value;
}

function channelValue(channel: RecipeChannel | undefined, progress: number, fallback: number): number {
  if (!channel?.keyframes?.length) return fallback;
  const kfs = [...channel.keyframes].sort((a, b) => a.at - b.at);
  if (progress <= kfs[0].at) return kfs[0].value;
  if (progress >= kfs[kfs.length - 1].at) return kfs[kfs.length - 1].value;
  const idx = kfs.findIndex((key, i) => i < kfs.length - 1 && progress >= key.at && progress <= kfs[i + 1].at);
  if (idx < 0) return fallback;
  const a = kfs[idx], b = kfs[idx + 1];
  const span = Math.max(.000001, b.at - a.at);
  const t = easingProgress((progress - a.at) / span, b.easing);
  return a.value + (b.value - a.value) * t;
}

function MotionLayer({layer, scene, recipes}: {layer: LayerPlan; scene: ShotPlan; recipes: Record<string, RecipePlan>}) {
  const frame = useCurrentFrame();
  const {fps} = useVideoConfig();
  const relSec = frame / fps;
  const recipe = layer.motion_recipe_id ? recipes[layer.motion_recipe_id] : undefined;
  const recipeDuration = Math.max(.12, Number((layer.motion_instance as any)?.duration || recipe?.duration?.recommended || .5));
  const progress = Math.min(1, Math.max(0, relSec / recipeDuration));
  const opacityChannel = recipe?.channels.find(c => c.property === "opacity");
  const scaleChannel = recipe?.channels.find(c => c.property === "scale");
  const xChannel = recipe?.channels.find(c => c.property === "x");
  const yChannel = recipe?.channels.find(c => c.property === "y");
  const base = layer.transform || {};
  const opacity = Math.max(0, Math.min(1, channelValue(opacityChannel, progress, base.opacity ?? 1)));
  const scale = Math.max(.1, Math.min(5, channelValue(scaleChannel, progress, base.scale ?? 1)));
  const x = channelValue(xChannel, progress, base.x ?? 0);
  const y = channelValue(yChannel, progress, base.y ?? 0);
  const rotation = base.rotation ?? 0;
  const style: React.CSSProperties = {
    position: "absolute", inset: 0, width: "100%", height: "100%",
    objectFit: layer.fit_mode || "cover",
    opacity, transform: `translate(${x}px,${y}px) scale(${scale}) rotate(${rotation}deg)`,
    transformOrigin: "center center",
  };

  if (layer.type === "image" && layer.src) {
    return <Img src={src(layer.src)} style={{...style, objectFit: layer.fit_mode || "cover"}} />;
  }
  if (layer.type === "video" && layer.src) {
    return <OffthreadVideo src={src(layer.src)} startFrom={frames(layer.source_in || 0, fps)} playbackRate={Math.max(.1, layer.speed || 1)} muted style={style} />;
  }
  if (layer.type === "text" || layer.type === "title") {
    return <AbsoluteFill style={{justifyContent: "center", alignItems: "center", padding: "8%"}}>
      <div style={{...typography.title, textAlign: "center", opacity, transform: `translate(${x}px,${y}px) scale(${scale})`}}>{layer.text || scene.text}</div>
    </AbsoluteFill>;
  }
  // An asset-less graphic is rendered as an original, deterministic semantic card.
  // An unsupported media layer without an asset is rejected before render by Python.
  if (layer.type === "graphic" || !layer.src) {
    return <SemanticCard scene={scene} opacity={opacity} scale={scale} />;
  }
  return null;
}

function SemanticCard({scene, opacity = 1, scale = 1}: {scene: ShotPlan; opacity?: number; scale?: number}) {
  const frame = useCurrentFrame();
  const {fps} = useVideoConfig();
  const p = Math.min(1, Math.max(0, frame / Math.max(1, frames(.55, fps))));
  const enter = Easing.out(Easing.back(1.04))(p);
  const sem = scene.beat.sem || [];
  const need = scene.beat.need || [];
  const mainEntity = (scene.beat.entities || []).find(x => x.length >= 2) || "";
  const isQuote = sem.includes("引用");
  const isData = sem.includes("数据");
  const isIdentity = sem.includes("自我介绍") || need.includes("身份");
  const isSteps = sem.includes("步骤") || sem.includes("列举");
  const eyebrow = (sem[0] || scene.semantic_role || "解说").toUpperCase();
  return <AbsoluteFill style={{justifyContent: "center", padding: "9%", background: COLORS.canvas, opacity, transform: `scale(${scale})`}}>
    <div style={{position: "absolute", left: "9%", right: "9%", top: "13%", height: 2, background: COLORS.line}} />
    <div style={{color: COLORS.accent, letterSpacing: 3, fontSize: 24, fontWeight: 700, marginBottom: 34}}>{eyebrow}</div>
    {isQuote ? <div style={{fontSize: 54, fontWeight: 650, lineHeight: 1.25, color: COLORS.quote}}>“{scene.text}”</div> :
     isIdentity ? <div>
       <div style={{fontSize: 26, color: COLORS.muted, marginBottom: 16}}>认识一下</div>
       <div style={{fontSize: 74, fontWeight: 800, lineHeight: 1.1}}>{mainEntity || scene.text}</div>
       <div style={{marginTop: 25, width: 120, height: 7, background: COLORS.accent}} />
     </div> :
     isData ? <div>
       <div style={{fontSize: 68, fontWeight: 850, lineHeight: 1.08, color: COLORS.accent}}>{mainEntity || scene.text.slice(0, 22)}</div>
       <div style={{fontSize: 34, marginTop: 28, lineHeight: 1.35, color: COLORS.ink}}>{scene.text}</div>
     </div> :
     isSteps ? <div style={{borderLeft: `5px solid ${COLORS.accent}`, paddingLeft: 28}}>
       <div style={{fontSize: 25, color: COLORS.muted, marginBottom: 14}}>核心步骤</div>
       <div style={{fontSize: 52, fontWeight: 750, lineHeight: 1.25}}>{scene.text}</div>
     </div> :
     <div style={{fontSize: 61, fontWeight: 780, lineHeight: 1.25, overflowWrap: "anywhere"}}>{scene.text}</div>}
    {!!scene.intent && <div style={{marginTop: 44, fontSize: 24, lineHeight: 1.45, color: COLORS.muted, maxWidth: "90%"}}>{scene.intent}</div>}
    <div style={{position: "absolute", right: "9%", bottom: "12%", color: COLORS.muted, fontSize: 19}}>{scene.id}</div>
  </AbsoluteFill>;
}

const typography: Record<string, React.CSSProperties> = {
  title: {fontSize: 64, fontWeight: 800, color: COLORS.ink, lineHeight: 1.2, overflowWrap: "anywhere"},
};

function SceneVisual({scene, recipes, fps}: {scene: ShotPlan; recipes: Record<string, RecipePlan>; fps: number}) {
  const frame = useCurrentFrame();
  const t = scene.start + frame / fps;
  const duration = Math.max(.001, scene.duration);
  const cameraScale = interpolate(frame, [0, Math.max(1, frames(duration, fps) - 1)], [1, 1.035], {extrapolateLeft: "clamp", extrapolateRight: "clamp"});
  const charByIndex = new Map((scene.characters || []).map(ch => [ch.script_index, ch]));
  return <AbsoluteFill style={{background: COLORS.canvas, color: COLORS.ink, overflow: "hidden"}}>
    <AbsoluteFill style={{transform: `scale(${cameraScale})`, transformOrigin: "center center", overflow: "hidden"}}>
      {scene.layers.map((layer, idx) => {
        const from = frames(layer.start || 0, fps);
        const length = Math.max(1, frames(layer.duration, fps));
        return <Sequence key={scene.id + ":" + layer.index + ":" + idx} from={from} durationInFrames={length} premountFor={Math.min(12, from)}>
          <MotionLayer layer={layer} scene={scene} recipes={recipes} />
        </Sequence>;
      })}
      {!scene.layers.length && <SemanticCard scene={scene} />}
    </AbsoluteFill>
    <AbsoluteFill style={{justifyContent: "flex-end", padding: "0 6% 6%", pointerEvents: "none"}}>
      <div style={{
        alignSelf: "center", display: "flex", flexWrap: "wrap", justifyContent: "center",
        maxWidth: "100%", padding: "12px 18px", borderRadius: 12,
        background: "rgba(5,8,13,.78)", fontSize: 33, fontWeight: 650, lineHeight: 1.55,
        textAlign: "center", textShadow: "0 2px 8px rgba(0,0,0,.3)",
      }}>
        {Array.from(scene.text).map((char, index) => {
          const timing = charByIndex.get(index);
          const active = !!timing && t >= timing.start && t < timing.end;
          const evidenced = timing && timing.timing_source === "interpolated_from_word";
          return <span key={scene.id + ":char:" + index} style={{
            color: active ? COLORS.accent : COLORS.ink,
            background: active ? "rgba(184,243,106,.12)" : "transparent",
            borderRadius: 3,
            textDecoration: active && !evidenced ? "underline dotted" : "none",
            textDecorationColor: COLORS.quote,
          }}>{char}</span>;
        })}
      </div>
    </AbsoluteFill>
    <div style={{position: "absolute", left: "4%", top: "4%", color: "rgba(255,255,255,.64)", fontSize: 17, letterSpacing: 1.3}}>{scene.semantic_role || "BODY"}</div>
  </AbsoluteFill>;
}

export const TalkCraftVideo: React.FC<TalkCraftProps> = (props) => {
  const {fps} = useVideoConfig();
  return <AbsoluteFill style={{background: COLORS.canvas, color: COLORS.ink, fontFamily: "'Microsoft YaHei', 'PingFang SC', system-ui, sans-serif"}}>
    {props.scenes.map((scene) => (
      <Sequence key={scene.id} from={frames(scene.start, fps)} durationInFrames={Math.max(1, frames(scene.duration, fps))}>
        <SceneVisual scene={scene} recipes={props.recipes} fps={fps} />
      </Sequence>
    ))}
    {props.audio.filter(a => !a.muted && a.volume > 0).map(track => (
      <Sequence key={track.id} from={frames(track.start, fps)} durationInFrames={Math.max(1, frames(track.duration, fps))}>
        <Audio
          src={src(track.src)}
          startFrom={frames(track.source_in || 0, fps)}
          volume={(f) => {
            const seconds = f / fps;
            const projectTime = track.start + seconds;
            const speechActive = (props.duckingWindows || []).some(([a, b]) => projectTime >= a && projectTime <= b);
            const duckGain = track.ducking && speechActive ? 0.28 : 1;
            const fadeIn = Math.max(0, track.fadeIn || 0);
            const fadeOut = Math.max(0, track.fadeOut || 0);
            const inGain = fadeIn > 0 ? interpolate(seconds, [0, frames(fadeIn, fps) / fps], [0, 1], {extrapolateLeft: "clamp", extrapolateRight: "clamp"}) : 1;
            const outStart = Math.max(0, track.duration - fadeOut);
            const outGain = fadeOut > 0 ? interpolate(seconds, [outStart, track.duration], [1, 0], {extrapolateLeft: "clamp", extrapolateRight: "clamp"}) : 1;
            return Math.max(0, Math.min(2, track.volume * inGain * outGain * duckGain));
          }}
        />
      </Sequence>
    ))}
    {props.narrationSrc && !props.audio.some(a => a.type === "narration" && !a.muted) && (
      <Audio src={src(props.narrationSrc)} volume={1} />
    )}
  </AbsoluteFill>;
};
