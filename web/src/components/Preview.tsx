import Box from '@cloudscape-design/components/box';
import Button from '@cloudscape-design/components/button';
import SpaceBetween from '@cloudscape-design/components/space-between';
import Toggle from '@cloudscape-design/components/toggle';
import type { CaptionItem, ClipItem, Item, TextItem, Timeline } from '@shorts/timeline';
import { sampleKeyframes, timelineDuration } from '@shorts/timeline';
import { CSSProperties, PointerEvent as RPointerEvent, WheelEvent as RWheelEvent, useCallback, useEffect, useMemo, useRef, useState } from 'react';
import type { AssetMedia } from '../api/types';
import { fmtTime } from '../util';

/**
 * Interim live preview (DOM compositor) driven by the timeline JSON.
 * Main track sequencing, reframe crop path, B-roll overlay, text, word-timed captions, music.
 * Phase 4 replaces this with a WebGL compositor that matches the server renderer frame-for-frame.
 */
interface Props {
  timeline: Timeline;
  media: Record<string, AssetMedia>;
  time: number;
  onTime: (t: number) => void;
  selectedId?: string;
  /** Optional controlled playback (keyboard shortcuts live in the page). */
  playing?: boolean;
  onPlayingChange?: (v: boolean) => void;
  onDropFiles?: (files: File[]) => void;
  onDropAsset?: (raw: string) => void;
  /** Direct manipulation on the canvas (select / move / resize / edit text). */
  editable?: boolean;
  onOps?: (ops: Record<string, unknown>[], message: string) => void;
  onSelect?: (item: Item | undefined, trackId?: string) => void;
}

type Guide = { axis: 'x' | 'y'; at: number };
type CanvasDrag = {
  id: string; trackId: string; kind: 'text' | 'caption' | 'pip' | 'main'; mode: 'move' | 'size' | 'width' | 'pan';
  x0: number; y0: number; moved: boolean; orig: Item;
};
const SNAP = 0.015;
const SNAP_X = [0.5, 0.06, 0.82];
const SNAP_Y = [0.5, 0.12, 0.8];
function snap1(v: number, targets: number[]): { v: number; hit?: number } {
  for (const t of targets) if (Math.abs(v - t) < SNAP) return { v: t, hit: t };
  return { v };
}
const clamp01 = (v: number) => Math.min(1, Math.max(0, v));

// Same tolerant reading as the server renderer (older timelines may hold named positions / non-object styles).
const NAMED_Y: Record<string, number> = { top: 0.18, upper: 0.3, center: 0.5, middle: 0.5, lower: 0.7, bottom: 0.8 };
function asPos(v: unknown, defY: number): { x: number; y: number; maxWidth?: number } {
  if (v && typeof v === 'object') return { x: 0.5, y: defY, ...(v as object) };
  if (typeof v === 'string') return { x: 0.5, y: NAMED_Y[v.toLowerCase()] ?? defY };
  return { x: 0.5, y: defY };
}
function asObj<T extends object>(v: unknown): Partial<T> { return v && typeof v === 'object' ? (v as T) : {}; }

type ReframeValue = { mode?: string; layout?: string; path?: { t: number; x: number; y: number; scale?: number }[] };

function activeAt(items: Item[], t: number): Item | undefined {
  return items.find((i) => t >= i.start && t < i.start + i.duration);
}

function srcTime(c: ClipItem, t: number): number {
  return c.srcIn + (t - c.start) * (c.speed ?? 1);
}

function focusAt(c: ClipItem, st: number): { x: number; y: number; scale: number; layout: string } {
  const r = c.reframe as ReframeValue | undefined;
  const path = r?.path ?? [];
  if (!path.length) return { x: c.transform?.x ?? 0.5, y: c.transform?.y ?? 0.5, scale: c.transform?.scale ?? 1, layout: r?.layout ?? 'crop' };
  let p = path[0];
  for (const q of path) if (q.t <= st) p = q;
  return { x: p.x, y: p.y, scale: p.scale ?? 1, layout: r?.layout ?? 'crop' };
}

function mediaStyle(c: ClipItem, st: number, fit: 'cover' | 'contain'): CSSProperties {
  const f = focusAt(c, st);
  const rel = 0;
  const opacity = sampleKeyframes(c.keyframes?.opacity, rel, c.opacity ?? 1);
  const contain = f.layout === 'fit_blur' || f.layout === 'letterbox' || f.layout === 'split' || fit === 'contain';
  return {
    position: 'absolute', inset: 0, width: '100%', height: '100%',
    objectFit: contain ? 'contain' : 'cover',
    objectPosition: `${(f.x * 100).toFixed(1)}% ${(f.y * 100).toFixed(1)}%`,
    transform: `scale(${f.scale})`, transformOrigin: `${f.x * 100}% ${f.y * 100}%`, opacity,
  };
}

export default function Preview({ timeline: timelineIn, media, time, onTime, selectedId, playing: playingProp, onPlayingChange, onDropFiles, onDropAsset,
  editable, onOps, onSelect }: Props) {
  // Live overrides while dragging on the canvas; cleared when the saved timeline comes back.
  const [ov, setOv] = useState<Record<string, Partial<Item>>>({});
  useEffect(() => { setOv({}); }, [timelineIn]);
  const timeline = useMemo<Timeline>(() => (Object.keys(ov).length ? {
    ...timelineIn, tracks: timelineIn.tracks.map((tr) => ({ ...tr, items: tr.items.map((i) => (ov[i.id] ? ({ ...i, ...ov[i.id] } as Item) : i)) })),
  } : timelineIn), [timelineIn, ov]);
  const [guides, setGuides] = useState<Guide[]>([]);
  const [editingId, setEditingId] = useState<string | null>(null);
  const dragRef = useRef<CanvasDrag | null>(null);
  const stageRef = useRef<HTMLDivElement>(null);
  const mainRef = useRef<HTMLVideoElement>(null);
  const overlayRef = useRef<HTMLVideoElement>(null);
  const musicRef = useRef<HTMLAudioElement>(null);
  const auxRef = useRef<HTMLVideoElement>(null); // second layer of the main clip: blurred fill / split bottom
  const [playingLocal, setPlayingLocal] = useState(false);
  const playing = playingProp ?? playingLocal;
  const setPlaying = useCallback((v: boolean) => { if (onPlayingChange) onPlayingChange(v); else setPlayingLocal(v); }, [onPlayingChange]);
  const [dropOver, setDropOver] = useState(false);
  const [safe, setSafe] = useState(true);
  const [stageW, setStageW] = useState(360);
  const clock = useRef<{ t0: number; wall: number } | null>(null);
  const duration = useMemo(() => timelineDuration(timeline), [timeline]);
  const { width, height } = timeline.settings;
  const tracks = timeline.tracks;
  const main = tracks.find((t) => t.kind === 'video');
  const overlay = tracks.find((t) => t.kind === 'overlay');
  const texts = tracks.filter((t) => t.kind === 'text' && !t.hidden).flatMap((t) => t.items) as TextItem[];
  const captions = tracks.filter((t) => t.kind === 'caption' && !t.hidden).flatMap((t) => t.items) as CaptionItem[];
  const music = tracks.find((t) => t.kind === 'audio' && t.audioRole === 'music');

  const mainItem = main && activeAt(main.items, time) as ClipItem | undefined;
  const overlayItem = overlay && activeAt(overlay.items, time) as ClipItem | undefined;
  const musicItem = music && activeAt(music.items, time) as ClipItem | undefined;

  // Fit the stage into the available box (max 70vh tall).
  useEffect(() => {
    const el = stageRef.current?.parentElement;
    if (!el) return;
    // Size to the real available box (the canvas pane shrinks when the timeline grows).
    const box = el.closest('.studio-stage') as HTMLElement | null;
    const ro = new ResizeObserver(() => {
      const maxH = box ? Math.max(200, box.clientHeight - 90) : Math.max(240, window.innerHeight - 380);
      setStageW(Math.min(el.clientWidth, (maxH * width) / height));
    });
    ro.observe(el);
    if (box) ro.observe(box);
    return () => ro.disconnect();
  }, [width, height]);

  // Keep media elements on the right source/time.
  const syncEl = useCallback((el: HTMLMediaElement | null, c: ClipItem | undefined, t: number, url?: string) => {
    if (!el) return;
    if (!c || !url) { if (!el.paused) el.pause(); return; }
    const want = srcTime(c, t);
    if (el.dataset.src !== url) {
      el.dataset.src = url; el.src = url;
      // Seeking before metadata is ignored by browsers: re-apply once it is known.
      el.addEventListener('loadedmetadata', () => { el.currentTime = Number(el.dataset.want ?? want); }, { once: true });
    }
    el.dataset.want = String(want);
    if (el.readyState === 0 && el.networkState !== el.NETWORK_LOADING) el.load();
    if (Math.abs(el.currentTime - want) > (playing ? 0.35 : 0.04)) el.currentTime = want;
    el.playbackRate = c.speed ?? 1;
    el.volume = Math.min(1, Math.max(0, c.volume ?? 1));
    if (playing && el.paused) el.play().catch(() => undefined);
    if (!playing && !el.paused) el.pause();
  }, [playing]);

  useEffect(() => {
    syncEl(mainRef.current, mainItem?.mediaKind === 'video' ? mainItem : undefined, time, mainItem && media[mainItem.assetId]?.proxyUrl);
    const lay = mainItem?.mediaKind === 'video' ? focusAt(mainItem, srcTime(mainItem, time)).layout : 'crop';
    syncEl(auxRef.current, lay === 'split' || lay === 'fit_blur' ? mainItem : undefined, time,
      mainItem && (media[mainItem.assetId]?.scrubUrl ?? media[mainItem.assetId]?.proxyUrl));
    if (auxRef.current) auxRef.current.muted = true;
    syncEl(overlayRef.current, overlayItem?.mediaKind === 'video' ? overlayItem : undefined, time, overlayItem && media[overlayItem.assetId]?.proxyUrl);
    const mu = musicItem && (media[musicItem.assetId]?.audioUrl ?? media[musicItem.assetId]?.proxyUrl);
    syncEl(musicRef.current, musicItem, time, mu);
    if (musicRef.current) musicRef.current.volume = Math.min(1, (music?.volume ?? 0.6) * (musicItem?.volume ?? 1));
  }, [time, mainItem, overlayItem, musicItem, media, syncEl, music?.volume]);

  // Playback clock (wall-clock based; media elements follow).
  useEffect(() => {
    if (!playing) { clock.current = null; return; }
    clock.current = { t0: time, wall: performance.now() };
    let raf = 0;
    const tick = () => {
      const c = clock.current!;
      const t = c.t0 + (performance.now() - c.wall) / 1000;
      if (t >= duration) { setPlaying(false); onTime(0); return; }
      onTime(t);
      raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [playing, duration]);

  const scale = stageW / width;
  const stageH = (stageW * height) / width;
  const ovRef = useRef(ov);
  ovRef.current = ov;
  const zoomTimer = useRef<number>(undefined);
  const trackOf = (id: string) => timelineIn.tracks.find((tr) => tr.items.some((i) => i.id === id))?.id ?? '';
  const findOrig = (id: string) => timelineIn.tracks.flatMap((tr) => tr.items).find((i) => i.id === id);
  const canEdit = !!editable && !!onOps;
  const setOne = (id: string, patch: Partial<Item>) => { const n = { [id]: patch }; ovRef.current = n; setOv(n); };

  const beginDrag = (e: RPointerEvent, item: Item, kind: CanvasDrag['kind'], mode: CanvasDrag['mode']) => {
    if (e.button !== 0 || editingId) return;
    e.stopPropagation();
    const orig = findOrig(item.id) ?? item;
    onSelect?.(orig, trackOf(item.id));
    if (!canEdit) return;
    if (playing) setPlaying(false);
    (e.currentTarget as HTMLElement).setPointerCapture(e.pointerId);
    dragRef.current = { id: item.id, trackId: trackOf(item.id), kind, mode, x0: e.clientX, y0: e.clientY, moved: false, orig };
  };

  /** Pan the crop focus of the main clip (shifts every point of the reframe path, so auto-tracking is kept). */
  const panPatch = (c: ClipItem, dxPx: number, dyPx: number, zoomK = 1): Partial<Item> | null => {
    const r = (c.reframe ?? {}) as ReframeValue;
    const layout = r.layout ?? 'crop';
    if (layout !== 'crop' && layout !== 'split') return null;
    const m = media[c.assetId];
    const boxH = layout === 'split' ? stageH / 2 : stageH;
    const srcAR = (m?.width ?? width) / (m?.height ?? height);
    const rw = srcAR > stageW / boxH ? boxH * srcAR : stageW;
    const rh = srcAR > stageW / boxH ? boxH : stageW / srcAR;
    const base = r.path?.length ? r.path : [{ t: c.srcIn, x: c.transform?.x ?? 0.5, y: c.transform?.y ?? 0.5, scale: c.transform?.scale ?? 1 }];
    const ddx = rw - stageW > 1 ? -dxPx / (rw - stageW) : 0;
    const ddy = rh - boxH > 1 ? -dyPx / (rh - boxH) : 0;
    const path = base.map((q) => ({ ...q, x: +clamp01(q.x + ddx).toFixed(4), y: +clamp01(q.y + ddy).toFixed(4),
      scale: +Math.min(3, Math.max(1, (q.scale ?? 1) * zoomK)).toFixed(3) }));
    return { reframe: { ...r, layout, mode: r.mode ?? 'manual', path } } as Partial<Item>;
  };

  const onDragMove = (e: RPointerEvent) => {
    const d = dragRef.current;
    if (!d) return;
    const dxPx = e.clientX - d.x0, dyPx = e.clientY - d.y0;
    if (!d.moved && Math.hypot(dxPx, dyPx) < 3) return;
    d.moved = true;
    const dx = dxPx / stageW, dy = dyPx / stageH;
    const o = d.orig as unknown as Record<string, unknown>;
    const g: Guide[] = [];
    if (d.kind === 'text' || d.kind === 'caption') {
      const pos = asPos(o.position, d.kind === 'text' ? 0.2 : 0.75);
      const st = asObj<TextItem['style']>(o.style);
      if (d.mode === 'move') {
        const sy = snap1(clamp01(pos.y + dy), SNAP_Y);
        if (sy.hit != null) g.push({ axis: 'y', at: sy.hit });
        if (d.kind === 'text') {
          const sx = snap1(clamp01(pos.x + dx), SNAP_X);
          if (sx.hit != null) g.push({ axis: 'x', at: sx.hit });
          setOne(d.id, { position: { ...asObj(o.position), ...pos, x: +sx.v.toFixed(4), y: +sy.v.toFixed(4) } } as Partial<Item>);
        } else setOne(d.id, { position: { ...asObj(o.position), y: +sy.v.toFixed(4) } } as Partial<Item>);
      } else if (d.mode === 'size') {
        const size0 = Number(st.size) || (d.kind === 'text' ? 0.05 : 0.045);
        const k = Math.max(0.2, 1 + (dx + dy) * 1.5);
        setOne(d.id, { style: { ...st, size: +Math.min(0.3, Math.max(0.015, size0 * k)).toFixed(4) } } as Partial<Item>);
      } else if (d.mode === 'width') {
        const mw = Number(pos.maxWidth ?? 0.86);
        setOne(d.id, { position: { ...asObj(o.position), ...pos, maxWidth: +Math.min(1, Math.max(0.15, mw + dx * 2)).toFixed(3) } } as Partial<Item>);
      }
    } else if (d.kind === 'pip') {
      const tf = { x: 0.5, y: 0.5, scale: 1, rotation: 0, ...asObj<NonNullable<ClipItem['transform']>>(o.transform) };
      if (d.mode === 'move') {
        const sx = snap1(clamp01(Number(tf.x) + dx), SNAP_X), sy = snap1(clamp01(Number(tf.y) + dy), SNAP_Y);
        if (sx.hit != null) g.push({ axis: 'x', at: sx.hit });
        if (sy.hit != null) g.push({ axis: 'y', at: sy.hit });
        setOne(d.id, { transform: { ...tf, x: +sx.v.toFixed(4), y: +sy.v.toFixed(4) } } as Partial<Item>);
      } else {
        const sc = Math.min(1, Math.max(0.05, Number(tf.scale) * (1 + dx * 2)));
        setOne(d.id, { transform: { ...tf, scale: +sc.toFixed(3) } } as Partial<Item>);
      }
    } else if (d.kind === 'main') {
      const patch = panPatch(d.orig as ClipItem, dxPx, dyPx);
      if (patch) setOne(d.id, patch);
    }
    setGuides(g);
  };

  const commit = (id: string, kind: CanvasDrag['kind'], patch: Partial<Item>) => {
    const set: Record<string, unknown> = {};
    for (const k of ['position', 'style', 'transform', 'reframe'] as const) if (k in patch) set[k] = (patch as Record<string, unknown>)[k];
    if (!Object.keys(set).length) return;
    const what = 'reframe' in set ? 'reframe' : 'transform' in set ? 'overlay position' : 'style' in set ? `${kind} size` : `${kind} position`;
    onOps?.([{ op: 'update', itemId: id, set }], what);
  };
  const onDragEnd = () => {
    const d = dragRef.current;
    dragRef.current = null;
    setGuides([]);
    if (!d || !d.moved) return;
    const patch = ovRef.current[d.id];
    if (patch) commit(d.id, d.kind, patch);
  };
  const onStageWheel = (e: RWheelEvent) => {
    if (!canEdit || !(e.ctrlKey || e.metaKey) || mainItem?.mediaKind !== 'video') return;
    e.preventDefault();
    const cur = (ovRef.current[mainItem.id] ? { ...mainItem, ...ovRef.current[mainItem.id] } : mainItem) as ClipItem;
    const patch = panPatch(cur, 0, 0, e.deltaY < 0 ? 1.06 : 1 / 1.06);
    if (!patch) return;
    setOne(mainItem.id, patch);
    window.clearTimeout(zoomTimer.current);
    zoomTimer.current = window.setTimeout(() => commit(mainItem.id, 'main', patch), 450);
  };
  const handles = (item: Item, kind: CanvasDrag['kind'], width?: boolean) => (!canEdit || selectedId !== item.id || editingId === item.id ? null : <>
    <span className="cv-handle br" title="resize" onPointerDown={(e) => beginDrag(e, item, kind, 'size')} />
    {width && <span className="cv-handle mr" title="wrap width" onPointerDown={(e) => beginDrag(e, item, kind, 'width')} />}
  </>);
  const startEdit = (x: TextItem) => { if (canEdit) { setPlaying(false); setEditingId(x.id); } };
  const finishEdit = (x: TextItem, el: HTMLElement, save: boolean) => {
    setEditingId(null);
    const text = el.innerText.replace(/\n+$/, '');
    if (save && text.trim() && text !== x.text) onOps?.([{ op: 'update', itemId: x.id, set: { text } }], 'edit text');
    else el.innerText = x.text;
  };
  const imageEl = (c: ClipItem) => {
    const m = media[c.assetId];
    const url = c.page != null && m?.pages ? m.pages.find((p) => p.index === c.page)?.url : (m?.imageUrl ?? m?.thumbnailUrl);
    const u = Math.min(1, Math.max(0, (time - c.start) / Math.max(0.01, c.duration)));
    const kb = c.kenBurns;
    const s = kb ? kb.from.scale + (kb.to.scale - kb.from.scale) * u : 1;
    const x = kb ? kb.from.x + (kb.to.x - kb.from.x) * u : 0.5;
    const y = kb ? kb.from.y + (kb.to.y - kb.from.y) * u : 0.5;
    return url ? <img src={url} alt="" style={{ position: 'absolute', inset: 0, width: '100%', height: '100%', objectFit: 'cover', transform: `scale(${s})`, transformOrigin: `${x * 100}% ${y * 100}%` }} /> : null;
  };
  /** Overlay clips scaled below full frame render as positioned boxes (picture-in-picture), like the renderer. */
  const pipBox = (o: ClipItem): CSSProperties | null => {
    const tf = asObj<NonNullable<ClipItem['transform']>>(o.transform);
    const sc = Number(tf.scale ?? 1);
    if (sc >= 0.999) return null;
    return { position: 'absolute', width: `${sc * 100}%`, height: 'auto', aspectRatio: `${media[o.assetId]?.width ?? 16} / ${media[o.assetId]?.height ?? 9}`,
      left: `${(tf.x ?? 0.5) * 100}%`, top: `${(tf.y ?? 0.5) * 100}%`, transform: 'translate(-50%, -50%)', objectFit: 'cover', opacity: o.opacity ?? 1,
      cursor: canEdit ? 'move' : 'pointer' };
  };
  const mainLayout = mainItem?.mediaKind === 'video' ? focusAt(mainItem, srcTime(mainItem, time)).layout : 'crop';
  // Mirrors the server renderer's layouts: fit_blur = blurred cover fill + contained frame;
  // split = subject crop on the top half + whole frame (slide/board) on the bottom half; letterbox = contain on black.
  const auxStyle: CSSProperties = mainLayout === 'fit_blur'
    ? { position: 'absolute', inset: '-6%', width: '112%', height: '112%', objectFit: 'cover', filter: 'blur(24px) brightness(0.88)' }
    : mainLayout === 'split' ? { position: 'absolute', left: 0, right: 0, bottom: 0, width: '100%', height: '50%', objectFit: 'contain', background: '#000' }
      : { display: 'none' };
  const mainStyle = (c: ClipItem): CSSProperties => {
    const base = mediaStyle(c, srcTime(c, time), 'cover');
    if (mainLayout === 'split') {
      const f = focusAt(c, srcTime(c, time));
      return { ...base, objectFit: 'cover', height: '50%', bottom: 'auto', objectPosition: `${(f.x * 100).toFixed(1)}% ${(f.y * 100).toFixed(1)}%` };
    }
    return base;
  };

  return (
    <SpaceBetween size="s">
      <div style={{ display: 'flex', justifyContent: 'center' }}>
        <div ref={stageRef} className={dropOver ? 'drop-zone-active' : undefined}
          onDragOver={(e) => { if (!onDropFiles && !onDropAsset) return; e.preventDefault(); setDropOver(true); }}
          onDragLeave={() => setDropOver(false)}
          onDrop={(e) => {
            setDropOver(false);
            if (!onDropFiles && !onDropAsset) return;
            e.preventDefault();
            const raw = e.dataTransfer.getData('application/x-shorts-asset');
            if (raw) onDropAsset?.(raw); else if (e.dataTransfer.files.length) onDropFiles?.([...e.dataTransfer.files]);
          }}
          onPointerDown={(e) => {
            if (e.button !== 0) return;
            const ovFull = overlayItem && Number(asObj<NonNullable<ClipItem['transform']>>(overlayItem.transform).scale ?? 1) >= 0.999 ? overlayItem : undefined;
            if (ovFull) { beginDrag(e, ovFull, 'pip', 'move'); return; }
            if (mainItem) beginDrag(e, mainItem, 'main', 'pan'); else onSelect?.(undefined);
          }}
          onPointerMove={onDragMove} onPointerUp={onDragEnd} onPointerCancel={() => { dragRef.current = null; setGuides([]); setOv({}); }}
          onWheel={onStageWheel}
          style={{ position: 'relative', width: stageW, height: stageH, background: timeline.settings.background, overflow: 'hidden', borderRadius: 4,
            cursor: canEdit && mainItem?.mediaKind === 'video' ? 'grab' : undefined, touchAction: canEdit ? 'none' : undefined, outline: selectedId && selectedId === mainItem?.id ? '2px solid #22d3ee' : undefined, outlineOffset: -2 }}>
          <video ref={auxRef} muted playsInline preload="auto" style={mainItem?.mediaKind === 'video' ? auxStyle : { display: 'none' }} />
          <video ref={mainRef} muted={main?.muted} playsInline preload="auto"
            style={mainItem?.mediaKind === 'video' ? mainStyle(mainItem) : { display: 'none' }} />
          {mainItem?.mediaKind === 'image' && imageEl(mainItem)}
          <video ref={overlayRef} muted playsInline preload="auto"
            onPointerDown={(e) => overlayItem && pipBox(overlayItem) && beginDrag(e, overlayItem, 'pip', 'move')}
            style={overlayItem?.mediaKind === 'video' ? (pipBox(overlayItem) ?? mediaStyle(overlayItem, srcTime(overlayItem, time), 'cover')) : { display: 'none' }} />
          {overlayItem?.mediaKind === 'video' && pipBox(overlayItem) && (
            <div style={{ ...pipBox(overlayItem)!, pointerEvents: 'none', outline: selectedId === overlayItem.id ? '2px solid #22d3ee' : undefined }}>
              <div style={{ pointerEvents: 'auto' }}>{handles(overlayItem, 'pip')}</div>
            </div>
          )}
          {overlay?.items.filter((o) => o.type === 'clip' && time >= o.start && time < o.start + o.duration).map((o0) => {
            const o = o0 as ClipItem;
            const tf = asObj<NonNullable<ClipItem['transform']>>(o.transform);
            const sc = Number(tf.scale ?? 1);
            if (o.mediaKind !== 'image') return null;
            if (sc >= 0.999 && !o.transform) return <div key={o.id} style={{ position: 'absolute', inset: 0 }}>{imageEl(o)}</div>;
            const m = media[o.assetId];
            return (
              <div key={o.id} onPointerDown={(e) => beginDrag(e, o, 'pip', 'move')}
                style={{ position: 'absolute', width: `${sc * 100}%`, left: `${(tf.x ?? 0.5) * 100}%`, top: `${(tf.y ?? 0.5) * 100}%`,
                  transform: 'translate(-50%, -50%)', cursor: canEdit ? 'move' : 'pointer', outline: selectedId === o.id ? '2px solid #22d3ee' : undefined }}>
                <img src={m?.imageUrl ?? m?.thumbnailUrl} alt="" draggable={false} style={{ width: '100%', display: 'block', opacity: o.opacity ?? 1, objectFit: 'contain', pointerEvents: 'none' }} />
                {handles(o, 'pip')}
              </div>
            );
          })}
          <audio ref={musicRef} preload="auto" />
          {texts.filter((x) => time >= x.start && time < x.start + x.duration).map((x0) => {
            const x = { ...x0, position: asPos(x0.position, 0.2), style: { size: 0.05, weight: 800, color: '#FFFFFF', ...asObj<TextItem['style']>(x0.style) } as TextItem['style'] };
            return (
            <div key={x.id} onPointerDown={(e) => beginDrag(e, x0, 'text', 'move')} onDoubleClick={() => startEdit(x0)}
              title={canEdit ? '드래그: 이동 · 더블클릭: 글자 수정 / Drag: move · Double-click: edit' : undefined}
              style={{ cursor: canEdit ? (editingId === x.id ? 'text' : 'move') : 'pointer', touchAction: 'none',
              position: 'absolute', left: `${(x.position?.x ?? 0.5) * 100}%`, top: `${(x.position?.y ?? 0.2) * 100}%`, transform: 'translate(-50%, -50%)',
              width: `${(x.position?.maxWidth ?? 0.86) * 100}%`, textAlign: x.style.align ?? 'center', color: x.style.color ?? '#fff',
              fontSize: (Number(x.style.size) || 0.05) * height * scale, fontWeight: x.style.weight ?? 800, lineHeight: 1.15, whiteSpace: 'pre-line',
              fontFamily: `${x.style.font}, Pretendard, 'Apple SD Gothic Neo', 'Noto Sans KR', sans-serif`,
              background: x.style.background, padding: x.style.background ? '0.15em 0.4em' : 0, borderRadius: 3,
              WebkitTextStroke: x.style.stroke ? `${x.style.stroke.width * height * scale}px ${x.style.stroke.color}` : undefined,
              paintOrder: 'stroke fill', textTransform: x.style.uppercase ? 'uppercase' : 'none',
              outline: x.id === selectedId ? '2px dashed #22d3ee' : undefined, outlineOffset: 4,
            }}>
              {editingId === x.id ? (
                <span contentEditable suppressContentEditableWarning className="cv-edit"
                  ref={(el) => { if (el && document.activeElement !== el) { el.focus(); const r = document.createRange(); r.selectNodeContents(el); const sel = window.getSelection(); sel?.removeAllRanges(); sel?.addRange(r); } }}
                  onPointerDown={(e) => e.stopPropagation()}
                  onBlur={(e) => finishEdit(x0, e.currentTarget, true)}
                  onKeyDown={(e) => {
                    e.stopPropagation();
                    if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); e.currentTarget.blur(); }
                    if (e.key === 'Escape') { e.preventDefault(); e.currentTarget.innerText = x0.text; e.currentTarget.blur(); }
                  }}>{x.text}</span>
              ) : x.text}
              {handles(x0, 'text', true)}
            </div>
          ); })}
          {captions.filter((c) => time >= c.start && time < c.start + c.duration).map((c0) => {
            const c = { ...c0, position: asPos(c0.position, 0.75), style: { size: 0.045, weight: 800, color: '#FFFFFF', ...asObj<CaptionItem['style']>(c0.style) } as CaptionItem['style'] };
            return (
            <div key={c.id} onPointerDown={(e) => beginDrag(e, c0, 'caption', 'move')}
              style={{ cursor: canEdit ? 'ns-resize' : 'pointer', touchAction: 'none', outline: c.id === selectedId ? '2px dashed #22d3ee' : undefined, outlineOffset: 4,
              position: 'absolute', left: '7%', right: '7%', top: `${c.position.y * 100}%`, transform: 'translateY(-50%)', textAlign: 'center',
              fontSize: (Number(c.style.size) || 0.045) * height * scale, fontWeight: c.style.weight ?? 800, color: c.style.color, lineHeight: 1.2,
              fontFamily: `${c.style.font}, Pretendard, 'Apple SD Gothic Neo', 'Noto Sans KR', sans-serif`,
              textTransform: c.style.uppercase ? 'uppercase' : 'none',
              WebkitTextStroke: c.style.stroke ? `${c.style.stroke.width * height * scale}px ${c.style.stroke.color}` : undefined, paintOrder: 'stroke fill',
              textShadow: c.style.shadow ? `${c.style.shadow.x}px ${c.style.shadow.y}px ${c.style.shadow.blur}px ${c.style.shadow.color}` : undefined,
            }}>
              <span style={{ background: c.style.background, padding: c.style.background ? '0.1em 0.35em' : 0, borderRadius: 6 }}>
                {c.words.map((w, i) => (
                  <span key={i} style={{ color: c.style.highlightColor && time >= w.s && time < w.e ? c.style.highlightColor : undefined }}>{w.w} </span>
                ))}
              </span>
              {handles(c0, 'caption')}
            </div>
          ); })}
          {guides.map((g) => (
            <div key={`${g.axis}${g.at}`} style={g.axis === 'x'
              ? { position: 'absolute', top: 0, bottom: 0, left: `${g.at * 100}%`, width: 1, background: '#ff2d95', pointerEvents: 'none', zIndex: 20 }
              : { position: 'absolute', left: 0, right: 0, top: `${g.at * 100}%`, height: 1, background: '#ff2d95', pointerEvents: 'none', zIndex: 20 }} />
          ))}
          {safe && timeline.settings.safeZone && timeline.settings.safeZone !== 'none' && (
            <div style={{ position: 'absolute', left: '6%', right: '18%', top: '12%', bottom: '20%', border: '1px dashed rgba(255,255,255,0.35)', pointerEvents: 'none' }} />
          )}
          {!mainItem && duration === 0 && <Box textAlign="center" color="inherit" padding="xxl"><span style={{ color: '#9ca3af' }}>—</span></Box>}
        </div>
      </div>
      <div style={{ display: 'flex', justifyContent: 'center' }}><SpaceBetween direction="horizontal" size="xs" alignItems="center">
        <Button iconName={playing ? 'pause' : 'caret-right-filled'} variant="icon" ariaLabel="play" onClick={() => setPlaying(!playing)} disabled={!duration} />
        <input type="range" min={0} max={Math.max(0.01, duration)} step={0.01} value={Math.min(time, duration)} style={{ width: `min(${Math.max(240, stageW)}px, 40vw)` }}
          onChange={(e) => { setPlaying(false); onTime(Number(e.target.value)); }} />
        <Box variant="small">{fmtTime(time)} / {fmtTime(duration)}</Box>
        <Toggle checked={safe} onChange={(e) => setSafe(e.detail.checked)}>safe zone</Toggle>
      </SpaceBetween></div>
    </SpaceBetween>
  );
}
