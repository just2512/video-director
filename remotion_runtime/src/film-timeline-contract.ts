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
export type FilmStringField =
  | "src" | "title" | "fade" | "class" | "alt" | "lang" | "dir" | "size"
  | "style" | "overrides" | "volume_envelope";
export type FilmOperation =
  | { op: "set"; clip: string; field: "at" | "speed" | "volume"; value: number }
  | { op: "set"; clip: string; field: "muted"; value: boolean }
  | { op: "set"; clip: string; field: FilmStringField; value: string | null }
  | { op: "move"; clip: string; at: number; track_id?: string }
  | { op: "trim"; clip: string; edge: "start"; at: number }
  | { op: "trim"; clip: string; edge: "end"; end: number }
  | { op: "split"; clip: string; at: number; new_id?: string }
  | { op: "slip"; clip: string; delta_source: number }
  | { op: "roll"; clip: string; other_clip: string; delta: number }
  | { op: "slide"; clip: string; delta: number }
  | { op: "join"; clip: string; other_clip: string }
  | { op: "remove"; clip: string }
  | { op: "ripple_delete"; clip: string; scope: "all"; split_ids?: string[] }
  | { op: "insert"; track_id: string; clip: FilmClipInsert }
  | { op: "insert_ripple"; track_id: string; at: number; clip: FilmClipInsert }
  | { op: "track_add"; track_id: string; label?: string; index?: number; kind?: string }
  | { op: "track_remove"; track_id: string }
  | { op: "track"; track_id: string; field: "locked" | "hidden" | "muted" | "sync-lock"; value: boolean }
  | { op: "element_set"; clip: string; selector: string; text?: string; style?: Record<string, string> }
  | { op: "clip_lock"; clip: string; locked: boolean };

export interface FilmClipInsert {
  id: string;
  tag: FilmClipTag;
  attrs: Record<string, string>;
}
export type FilmEditResponse =
  | { ok: true; rev: string; ops: FilmOperation[]; rebased?: boolean }
  | { ok: false; code: string; error: string; current_revision?: string };
