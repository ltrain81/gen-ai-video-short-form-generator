/**
 * Timeline model — the single source of truth shared by the agent (tools), the browser preview
 * compositor and the server renderer (FFmpeg). Expresses every editing level L0–L5.
 *
 * Conventions
 *  - Time in seconds (float). Item `start` is timeline time; `srcIn`/`srcOut` are source media time.
 *  - Spatial values are normalized to the canvas: x,y ∈ [0,1] (0,0 = top-left), scale 1 = fit per `fit`.
 *  - Tracks render bottom → top in array order (index 0 = base video).
 *  - `pins` hold JSON-pointer-ish paths ("items/<id>/srcIn") the user edited by hand; the agent must not change them.
 */

export const TIMELINE_VERSION = 1;

export type Aspect = '9:16' | '16:9' | '1:1' | '4:5' | '4:3' | 'custom';
export type Ease = 'linear' | 'easeIn' | 'easeOut' | 'easeInOut' | 'hold';

export interface Timeline {
  version: typeof TIMELINE_VERSION;
  id: string;
  projectId: string;
  settings: TimelineSettings;
  tracks: Track[];
  markers: Marker[];
  meta: OutputMeta;
  /** Paths pinned by the user (L2+ manual edits). */
  pins: string[];
}

export interface TimelineSettings {
  aspect: Aspect;
  width: number;
  height: number;
  fps: number;
  background: string; // CSS color
  /** Target integrated loudness for final mix (LUFS). */
  loudness: number;
  /** Platform UI safe-zone overlay for preview + caption placement. */
  safeZone?: 'none' | 'shorts' | 'tiktok' | 'reels';
  recipe?: string;
  brandKitId?: string;
}

export interface Marker { id: string; t: number; label: string; kind: 'chapter' | 'note' | 'beat' }

export interface OutputMeta {
  title?: string;
  description?: string;
  hashtags?: string[];
  thumbnailAt?: number;
  thumbnailAssetId?: string;
}

export type TrackKind = 'video' | 'overlay' | 'text' | 'caption' | 'audio';

export interface Track {
  id: string;
  kind: TrackKind;
  name: string;
  hidden?: boolean;
  muted?: boolean;
  locked?: boolean;
  volume?: number; // 0..2, audio-bearing tracks
  /** Audio role for automatic mixing (ducking music under voice). */
  audioRole?: 'dialogue' | 'music' | 'voiceover' | 'sfx';
  items: Item[];
}

export type Item = ClipItem | TextItem | CaptionItem | ShapeItem;

export interface ItemBase {
  id: string;
  start: number;
  duration: number;
  /** Why the AI chose this (L2 inspectability). */
  provenance?: Provenance;
  transitionIn?: Transition;
  transitionOut?: Transition;
  keyframes?: Partial<Record<AnimatableProp, Keyframe[]>>;
  opacity?: number;
}

export interface Provenance {
  by: 'agent' | 'user' | 'recipe';
  reason?: string;
  searchQuery?: string;
  score?: number;
  analysisRef?: { assetId: string; startSec: number; endSec: number; kind: 'scene' | 'chapter' | 'transcript' | 'search' };
}

export type AnimatableProp = 'x' | 'y' | 'scale' | 'rotation' | 'opacity' | 'volume' | 'cropL' | 'cropT' | 'cropR' | 'cropB' | 'blur';

export interface Keyframe { t: number; v: number; ease?: Ease } // t relative to item start

export interface Transform {
  x: number; // center x
  y: number; // center y
  scale: number;
  rotation: number; // degrees
  fit: 'cover' | 'contain' | 'fill';
  crop?: { l: number; t: number; r: number; b: number }; // fraction of source removed per side
  flipH?: boolean;
}

/** Video, image, document page or audio clip referencing an archive asset. */
export interface ClipItem extends ItemBase {
  type: 'clip';
  assetId: string;
  mediaKind: 'video' | 'image' | 'audio';
  page?: number; // documents: page index used as an image
  srcIn: number;
  srcOut: number;
  speed?: number; // 1 = normal; duration = (srcOut - srcIn) / speed
  /** Speed curve (L4): keyframes over item-relative time, value = speed. */
  speedCurve?: Keyframe[];
  transform?: Transform;
  /** Automatic subject-tracking reframe (9:16 from 16:9 sources). */
  reframe?: { mode: 'auto' | 'manual' | 'off'; path?: { t: number; x: number; y: number }[] };
  kenBurns?: { from: { x: number; y: number; scale: number }; to: { x: number; y: number; scale: number } };
  volume?: number;
  fadeIn?: number;
  fadeOut?: number;
  effects?: Effect[];
  /** Freeze / reverse etc. */
  mode?: 'normal' | 'freeze' | 'reverse';
}

export interface TextStyle {
  font: string;
  size: number; // fraction of canvas height
  weight?: number;
  color: string;
  background?: string;
  stroke?: { color: string; width: number };
  shadow?: { color: string; blur: number; x: number; y: number };
  align?: 'left' | 'center' | 'right';
  lineHeight?: number;
  uppercase?: boolean;
}

export interface TextItem extends ItemBase {
  type: 'text';
  text: string;
  role?: 'title' | 'lowerThird' | 'callout' | 'cta' | 'step' | 'free';
  style: TextStyle;
  position: { x: number; y: number; anchor: 'center' | 'top' | 'bottom' | 'left' | 'right'; maxWidth?: number };
  animation?: { in?: TextAnimation; out?: TextAnimation };
}

export type TextAnimation = 'none' | 'fade' | 'pop' | 'slideUp' | 'slideDown' | 'typewriter' | 'wordByWord';

/** Word-timed captions (from Transcribe or TTS). */
export interface CaptionItem extends ItemBase {
  type: 'caption';
  sourceAssetId?: string;
  words: { s: number; e: number; w: string }[]; // timeline time
  preset: 'clean' | 'bold' | 'karaoke' | 'pop' | 'boxed' | 'minimal';
  style: TextStyle & { highlightColor?: string; maxWordsPerLine?: number; maxLines?: number };
  position: { y: number };
}

export interface ShapeItem extends ItemBase {
  type: 'shape';
  shape: 'rect' | 'roundRect' | 'ellipse' | 'progressBar';
  fill: string;
  stroke?: { color: string; width: number };
  box: { x: number; y: number; w: number; h: number };
}

export interface Transition {
  type: 'cut' | 'fade' | 'dissolve' | 'dipToBlack' | 'dipToWhite' | 'wipeLeft' | 'wipeUp' | 'slideLeft' | 'zoom' | 'blur';
  duration: number;
}

export type Effect =
  | { type: 'color'; brightness?: number; contrast?: number; saturation?: number; temperature?: number; tint?: number; gamma?: number }
  | { type: 'lut'; assetId: string; intensity?: number }
  | { type: 'chromaKey'; color: string; similarity: number; smoothness: number; spill?: number }
  | { type: 'blur'; radius: number }
  | { type: 'vignette'; amount: number }
  | { type: 'sharpen'; amount: number }
  | { type: 'eq'; low?: number; mid?: number; high?: number }
  | { type: 'denoise'; amount: number };

// ------------------------------------------------------------------ helpers

export const ASPECT_SIZES: Record<Exclude<Aspect, 'custom'>, [number, number]> = {
  '9:16': [1080, 1920],
  '16:9': [1920, 1080],
  '1:1': [1080, 1080],
  '4:5': [1080, 1350],
  '4:3': [1440, 1080],
};

export function itemEnd(i: Item): number {
  return i.start + i.duration;
}

export function timelineDuration(t: Timeline): number {
  return Math.max(0, ...t.tracks.flatMap((tr) => tr.items.map(itemEnd)));
}

export function clipDuration(c: Pick<ClipItem, 'srcIn' | 'srcOut' | 'speed' | 'mediaKind' | 'duration'>): number {
  if (c.mediaKind === 'image') return c.duration;
  return (c.srcOut - c.srcIn) / (c.speed ?? 1);
}

export function emptyTimeline(id: string, projectId: string, aspect: Exclude<Aspect, 'custom'> = '9:16'): Timeline {
  const [width, height] = ASPECT_SIZES[aspect];
  return {
    version: TIMELINE_VERSION,
    id,
    projectId,
    settings: { aspect, width, height, fps: 30, background: '#000000', loudness: -14, safeZone: aspect === '9:16' ? 'shorts' : 'none' },
    tracks: [
      { id: 'v1', kind: 'video', name: 'Video', items: [], audioRole: 'dialogue' },
      { id: 'o1', kind: 'overlay', name: 'B-roll', items: [] },
      { id: 't1', kind: 'text', name: 'Text', items: [] },
      { id: 'c1', kind: 'caption', name: 'Captions', items: [] },
      { id: 'a1', kind: 'audio', name: 'Music', items: [], audioRole: 'music', volume: 0.6 },
      { id: 'a2', kind: 'audio', name: 'Voiceover', items: [], audioRole: 'voiceover' },
    ],
    markers: [],
    meta: {},
    pins: [],
  };
}

/** Value of a keyframed property at item-relative time t. */
export function sampleKeyframes(kfs: Keyframe[] | undefined, t: number, fallback: number): number {
  if (!kfs?.length) return fallback;
  if (t <= kfs[0].t) return kfs[0].v;
  for (let i = 0; i < kfs.length - 1; i++) {
    const a = kfs[i], b = kfs[i + 1];
    if (t >= a.t && t <= b.t) {
      if (a.ease === 'hold') return a.v;
      const u = (t - a.t) / Math.max(1e-6, b.t - a.t);
      const e = a.ease === 'easeIn' ? u * u : a.ease === 'easeOut' ? 1 - (1 - u) ** 2 : a.ease === 'easeInOut' ? (u < 0.5 ? 2 * u * u : 1 - (-2 * u + 2) ** 2 / 2) : u;
      return a.v + (b.v - a.v) * e;
    }
  }
  return kfs[kfs.length - 1].v;
}
