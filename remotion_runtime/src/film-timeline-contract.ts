/** Typed read/write contract shared by the React editor and the FilmDocument API. */
export type FilmClipTag = "iframe" | "video" | "img" | "audio";
export interface FilmClip {
  id: string;
  tag: FilmClipTag;
  at: number;
  end: number | null;
  duration: number | null;
  attrs: Record<string, string>;
  src_in: number;
  src_out: number | null;
  media_url: string | null;
}
export interface FilmTrack {
  id: string;
  attrs: Record<string, string>;
  clips: FilmClip[];
}
export interface FilmStage {
  width: number;
  height: number;
}
export interface FilmDocumentPayload {
  ok: boolean;
  exists: boolean;
  project_id: string;
  rev: string;
  stage?: FilmStage;
  duration?: number;
  tracks?: FilmTrack[];
  warnings?: string[];
  migration?: {
    source_composition_version?: number;
    warnings?: string[];
    selected_variant_id?: string | null;
  };
}
export type FilmOperation =
  | { op: "set"; clip: string; field: "at" | "speed" | "volume"; value: number }
  | { op: "move"; clip: string; at: number; track_id?: string }
  | { op: "trim"; clip: string; edge: "start"; at: number }
  | { op: "trim"; clip: string; edge: "end"; end: number }
  | { op: "split"; clip: string; at: number; new_id?: string }
  | { op: "slip"; clip: string; delta_source: number }
  | { op: "roll"; clip: string; other_clip: string; delta: number }
  | { op: "slide"; clip: string; delta: number }
  | { op: "element_set"; clip: string; selector: string; text?: string; style?: Record<string, string> }
  | { op: "clip_lock"; clip: string; locked: boolean };
export type FilmEditResponse =
  | { ok: true; rev: string; ops: FilmOperation[]; rebased?: boolean }
  | { ok: false; code: string; error: string; current_revision?: string };
