/**
 * Keyframes, transition motion and speed ramps — the browser twin of services/shorts/render.py
 * (item_keyframes / kf_at) and timeline_ops.py (speed_chunks / src_at), so the preview matches the render.
 */
import type { ClipItem, Keyframe } from '@shorts/timeline';
import { sampleKeyframes } from '@shorts/timeline';

type Any = Record<string, unknown>;
const obj = (v: unknown): Any => (v && typeof v === 'object' ? (v as Any) : {});
const sorted = (k: unknown): Keyframe[] => (Array.isArray(k) ? (k as Keyframe[]).filter((x) => x && typeof x.t === 'number' && typeof x.v === 'number').sort((a, b) => a.t - b.t) : []);

export const kfAt = (kfs: Keyframe[] | undefined, t: number, d: number) => sampleKeyframes(kfs?.length ? kfs : undefined, t, d);

const SLIDES: Record<string, [string, number]> = {
  slideLeft: ['x', 0.35], slideRight: ['x', -0.35], slideUp: ['y', 0.25], slideDown: ['y', -0.25], wipeLeft: ['x', 0.35], wipeUp: ['y', 0.25],
  coverleft: ['x', 0.35], coverright: ['x', -0.35], coverup: ['y', 0.25], coverdown: ['y', -0.25],
};

export interface ItemKf { [prop: string]: Keyframe[] | undefined; }
export interface Motion { kf: ItemKf; fadeIn: number; fadeOut: number }

/** User keyframes + motion from transitionIn/transitionOut (slide/zoom), plus fade durations. */
export function itemMotion(it: Any, base: Record<string, number>): Motion {
  const kf: ItemKf = {};
  for (const [k, v] of Object.entries(obj(it.keyframes))) kf[k] = sorted(v);
  const dur = Number(it.duration ?? 0);
  let fadeIn = 0, fadeOut = 0;
  for (const which of ['transitionIn', 'transitionOut'] as const) {
    const tr = obj(it[which]);
    const kind = tr.type as string | undefined;
    const d = Math.min(dur / 2, Number(tr.duration ?? 0.4) || 0.4);
    if (!kind || kind === 'cut' || d <= 0) continue;
    const [a, b] = which === 'transitionIn' ? [0, d] : [dur - d, dur];
    if (SLIDES[kind]) {
      const [p, off] = SLIDES[kind];
      const v = base[p] ?? 0.5;
      const ks: Keyframe[] = which === 'transitionIn' ? [{ t: a, v: v + off, ease: 'easeOut' }, { t: b, v }] : [{ t: a, v, ease: 'easeIn' }, { t: b, v: v - off }];
      kf[p] = sorted([...(kf[p] ?? []), ...ks]);
    } else if (['zoom', 'zoomin', 'pop'].includes(kind)) {
      const v = base.scale ?? 1;
      const ks: Keyframe[] = which === 'transitionIn' ? [{ t: a, v: v * 0.6, ease: 'easeOut' }, { t: b, v }] : [{ t: a, v, ease: 'easeIn' }, { t: b, v: v * 0.6 }];
      kf.scale = sorted([...(kf.scale ?? []), ...ks]);
    }
    if (which === 'transitionIn') fadeIn = d; else fadeOut = d;
  }
  return { kf, fadeIn, fadeOut };
}

/** Opacity multiplier from fades at item-relative time u. */
export const fadeMul = (m: Motion, u: number, dur: number) =>
  Math.max(0, Math.min(1, (m.fadeIn ? u / m.fadeIn : 1), (m.fadeOut ? (dur - u) / m.fadeOut : 1)));

const CHUNK = 0.25;
/** (srcA, srcB, speed) pieces — mirrors timeline_ops.speed_chunks. */
export function speedChunks(c: ClipItem): [number, number, number][] {
  const a = c.srcIn, b = c.srcOut;
  const curve = sorted(c.speedCurve);
  if (!curve.length || b <= a) return [[a, b, Math.max(0.05, c.speed ?? 1)]];
  const out: [number, number, number][] = [];
  for (let t = a; t < b - 1e-6; t += CHUNK) {
    const e = Math.min(b, t + CHUNK);
    out.push([t, e, Math.max(0.1, Math.min(16, sampleKeyframes(curve, (t + e) / 2 - a, c.speed ?? 1)))]);
  }
  return out;
}

/** Source time shown `rel` seconds into the item (speed, ramps, reverse, freeze) — mirrors timeline_ops.src_at. */
export function srcAt(c: ClipItem, rel: number): number {
  if (c.mode === 'freeze') return c.srcIn;
  let tOut = 0, src = c.srcIn;
  for (const [x, y, sp] of speedChunks(c)) {
    const seg = (y - x) / sp;
    if (rel <= tOut + seg) { src = x + (rel - tOut) * sp; break; }
    tOut += seg; src = y;
  }
  return c.mode === 'reverse' ? c.srcIn + c.srcOut - src : src;
}
