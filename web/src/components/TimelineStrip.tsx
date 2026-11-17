import Box from '@cloudscape-design/components/box';
import Button from '@cloudscape-design/components/button';
import * as awsui from '@cloudscape-design/design-tokens';
import type { Item, Timeline } from '@shorts/timeline';
import { timelineDuration } from '@shorts/timeline';
import { DragEvent, PointerEvent as RPointerEvent, WheelEvent, useEffect, useMemo, useRef, useState } from 'react';
import { useI18n } from '../i18n';
import { fmtTime } from '../util';

const COLORS: Record<string, string> = {
  video: '#2563eb', overlay: '#7c3aed', text: '#d97706', caption: '#059669', audio: '#db2777',
};
const NAME_W = 72;
const SNAP_PX = 8;
const TICK_STEPS = [0.25, 0.5, 1, 2, 5, 10, 15, 30, 60, 120, 300, 600];
export const ASSET_MIME = 'application/x-shorts-asset';

export type EditOp = Record<string, unknown>;
export type DragAsset = { assetId: string; kind?: string; durationSec?: number; title?: string };
export type ItemAction = 'split' | 'duplicate' | 'copy' | 'paste' | 'delete' | 'pin';
/** Where something was dropped: the track, the timeline time and (main track) the insertion index. */
export type DropTarget = { trackId: string; trackKind: string; time: number; index: number };

interface Props {
  timeline: Timeline;
  time: number;
  onSeek: (t: number) => void;
  selectedId?: string;
  onSelect: (item: Item | undefined, trackId?: string) => void;
  titles: Record<string, string>;
  thumbs?: Record<string, string | undefined>;
  /** Manual mode: enables drag/trim/drop and reports ops. */
  editable?: boolean;
  /** Accept drops (archive assets / desktop files) even outside manual mode. */
  droppable?: boolean;
  onOps?: (ops: EditOp[], message: string) => void;
  onDropAsset?: (a: DragAsset, at: DropTarget) => void;
  onDropFiles?: (files: File[], at: DropTarget) => void;
  onAction?: (action: ItemAction, item: Item, trackId: string) => void;
  canPaste?: boolean;
}

function label(i: Item, titles: Record<string, string>): string {
  if (i.type === 'clip') return titles[i.assetId] ?? i.assetId;
  if (i.type === 'text') return i.text;
  if (i.type === 'caption') return i.words.map((w) => w.w).join(' ');
  return i.type;
}

type Drag = { kind: 'move' | 'trimL' | 'trimR'; item: Item; trackId: string; trackKind: string; x0: number; dx: number; stretch: boolean };

/** Timeline: read-only view in AI mode; drag / trim / drop / zoom / snap editing in manual mode. */
export default function TimelineStrip({ timeline, time, onSeek, selectedId, onSelect, titles, thumbs = {}, editable, droppable, onOps,
  onDropAsset, onDropFiles, onAction, canPaste }: Props) {
  const { t } = useI18n();
  const duration = timelineDuration(timeline);
  // Leave room after the end so things can be dropped / dragged past the last clip.
  const viewDur = Math.max(5, duration + Math.max(2, duration * 0.08));
  const pinned = useMemo(() => new Set(timeline.pins.map((p) => p.split('/')[1])), [timeline.pins]);
  const tracks = timeline.tracks.filter((tr) => tr.items.length || tr.kind === 'video' || editable);
  const scrollRef = useRef<HTMLDivElement>(null);
  const innerRef = useRef<HTMLDivElement>(null);
  const [zoom, setZoom] = useState(1);
  const [snap, setSnap] = useState(true);
  const [drag, setDrag] = useState<Drag | null>(null);
  const [scrub, setScrub] = useState(false);
  const [dropAt, setDropAt] = useState<DropTarget | null>(null);
  const [menu, setMenu] = useState<{ x: number; y: number; item: Item; trackId: string } | null>(null);
  const [innerW, setInnerW] = useState(800);

  useEffect(() => {
    const el = innerRef.current;
    if (!el) return undefined;
    const ro = new ResizeObserver(() => setInnerW(el.clientWidth));
    ro.observe(el);
    return () => ro.disconnect();
  }, []);
  useEffect(() => {
    if (!menu) return undefined;
    const close = () => setMenu(null);
    const esc = (e: KeyboardEvent) => { if (e.key === 'Escape') close(); };
    window.addEventListener('pointerdown', close);
    window.addEventListener('keydown', esc);
    return () => { window.removeEventListener('pointerdown', close); window.removeEventListener('keydown', esc); };
  }, [menu]);

  const pps = innerW / viewDur; // px per second
  const pct = (s: number) => `${(s / viewDur) * 100}%`;
  const timeAt = (clientX: number) => {
    const r = innerRef.current!.getBoundingClientRect();
    return Math.max(0, Math.min(viewDur, ((clientX - r.left) / r.width) * viewDur));
  };
  const tick = TICK_STEPS.find((s) => s * pps >= 70) ?? 600;

  // Keep the playhead visible while playing / stepping when zoomed in.
  useEffect(() => {
    const sc = scrollRef.current;
    if (!sc || zoom === 1 || drag || scrub) return;
    const x = time * pps;
    if (x < sc.scrollLeft + 20 || x > sc.scrollLeft + sc.clientWidth - 20) sc.scrollLeft = Math.max(0, x - sc.clientWidth * 0.3);
  }, [time, pps, zoom, drag, scrub]);

  const setZoomAround = (z: number, clientX?: number) => {
    const sc = scrollRef.current;
    const nz = Math.min(40, Math.max(1, z));
    if (!sc) { setZoom(nz); return; }
    const anchorT = clientX != null ? timeAt(clientX) : time;
    const offset = clientX != null ? clientX - sc.getBoundingClientRect().left : sc.clientWidth * 0.3;
    setZoom(nz);
    requestAnimationFrame(() => { sc.scrollLeft = Math.max(0, anchorT * (sc.clientWidth * nz / viewDur) - offset); });
  };
  const onWheel = (e: WheelEvent) => {
    if (!(e.ctrlKey || e.metaKey)) return;
    e.preventDefault();
    setZoomAround(zoom * (e.deltaY < 0 ? 1.25 : 0.8), e.clientX);
  };

  // ---------------------------------------------------------------- snapping
  const snapPoints = useMemo(() => {
    const pts = [0, time];
    timeline.tracks.forEach((tr) => tr.items.forEach((i) => { if (i.id !== drag?.item.id) pts.push(i.start, i.start + i.duration); }));
    return pts;
  }, [timeline, time, drag?.item.id]);
  const snapTo = (edges: number[], delta: number): { delta: number; line?: number } => {
    if (!snap) return { delta };
    let best: { delta: number; line?: number; dist: number } = { delta, dist: SNAP_PX / pps };
    for (const e of edges) for (const p of snapPoints) {
      const dist = Math.abs(e + delta - p);
      if (dist < best.dist) best = { delta: p - e, line: p, dist };
    }
    return { delta: best.delta, line: best.line };
  };
  const dragDelta = (d: Drag) => {
    const raw = d.dx / pps;
    const i = d.item;
    if (d.kind === 'move') return d.trackKind === 'video' ? { delta: raw } : snapTo([i.start, i.start + i.duration], raw);
    if (d.kind === 'trimL') return snapTo([i.start], raw);
    return snapTo([i.start + i.duration], raw);
  };

  // ---------------------------------------------------------------- item drag
  const start = (e: RPointerEvent, kind: Drag['kind'], item: Item, trackId: string, trackKind: string) => {
    if (e.button !== 0) return;
    if (!editable) return;
    e.stopPropagation();
    (e.target as HTMLElement).setPointerCapture(e.pointerId);
    setDrag({ kind, item, trackId, trackKind, x0: e.clientX, dx: 0, stretch: e.altKey && kind === 'trimR' && item.type === 'clip' && item.mediaKind !== 'image' });
    onSelect(item, trackId);
  };
  const move = (e: RPointerEvent) => {
    if (drag) setDrag({ ...drag, dx: e.clientX - drag.x0 });
    else if (scrub) onSeek(Math.min(duration, timeAt(e.clientX)));
  };
  const end = () => {
    setScrub(false);
    if (!drag || !onOps) { setDrag(null); return; }
    const { delta: d } = dragDelta(drag);
    const i = drag.item;
    setDrag(null);
    if (Math.abs(drag.dx) < 3) return;
    if (drag.kind === 'move') {
      if (drag.trackKind === 'video') {
        const tr = timeline.tracks.find((x) => x.id === drag.trackId)!;
        const target = i.start + d + i.duration / 2;
        const others = tr.items.filter((x) => x.id !== i.id);
        onOps([{ op: 'move', itemId: i.id, index: others.filter((x) => x.start + x.duration / 2 < target).length }], 'reorder');
      } else {
        onOps([{ op: 'move', itemId: i.id, start: Math.max(0, +(i.start + d).toFixed(3)) }], 'move');
      }
    } else if (drag.stretch && i.type === 'clip') {
      // Alt/Option-drag on the right edge: time-stretch (keep the source range, change speed).
      const speed = Math.min(4, Math.max(0.25, (i.srcOut - i.srcIn) / Math.max(0.1, i.duration + d)));
      onOps([{ op: 'update', itemId: i.id, set: { speed: +speed.toFixed(3) } }], `speed ${speed.toFixed(2)}x`);
    } else if (i.type === 'clip' && i.mediaKind !== 'image') {
      const speed = i.speed ?? 1;
      const op = drag.kind === 'trimL' ? { op: 'trim', itemId: i.id, srcIn: Math.max(0, +(i.srcIn + d * speed).toFixed(3)) }
        : { op: 'trim', itemId: i.id, srcOut: +(i.srcOut + d * speed).toFixed(3) };
      const ops: EditOp[] = [op];
      // Free tracks keep the right edge where it was when trimming the head.
      if (drag.kind === 'trimL' && drag.trackKind !== 'video') ops.push({ op: 'update', itemId: i.id, set: { start: Math.max(0, +(i.start + d).toFixed(3)) } });
      onOps(ops, 'trim');
    } else if (drag.kind === 'trimL') {
      onOps([{ op: 'update', itemId: i.id, set: { start: Math.max(0, i.start + d), duration: Math.max(0.1, i.duration - d) } }], 'trim');
    } else {
      onOps([{ op: 'trim', itemId: i.id, duration: Math.max(0.1, +(i.duration + d).toFixed(3)) }], 'trim');
    }
  };
  const live = drag ? dragDelta(drag) : null;
  const geom = (i: Item) => {
    if (!drag || drag.item.id !== i.id) return { left: pct(i.start), width: `calc(${pct(i.duration)} - 2px)` };
    const d = live!.delta;
    if (drag.kind === 'move') return { left: pct(i.start + d), width: `calc(${pct(i.duration)} - 2px)` };
    if (drag.kind === 'trimL') return { left: pct(i.start + d), width: `calc(${pct(Math.max(0.1, i.duration - d))} - 2px)` };
    return { left: pct(i.start), width: `calc(${pct(Math.max(0.1, i.duration + d))} - 2px)` };
  };

  // ---------------------------------------------------------------- drop
  const accepts = (e: DragEvent) => (editable || droppable) && (e.dataTransfer.types.includes(ASSET_MIME) || e.dataTransfer.types.includes('Files'));
  const target = (e: DragEvent, trackId: string, trackKind: string): DropTarget => {
    const tm = timeAt(e.clientX);
    const tr = timeline.tracks.find((x) => x.id === trackId)!;
    const index = tr.items.filter((x) => x.start + x.duration / 2 < tm).length;
    // Main track is magnetic: show the drop at the clip boundary it will land on.
    const shown = trackKind === 'video' ? (tr.items[index - 1] ? tr.items[index - 1].start + tr.items[index - 1].duration : 0) : snapTo([tm], 0).delta + tm;
    return { trackId, trackKind, time: +shown.toFixed(3), index };
  };
  const onDrop = (e: DragEvent, trackId: string, trackKind: string) => {
    if (!accepts(e)) return;
    e.preventDefault();
    const at = target(e, trackId, trackKind);
    setDropAt(null);
    const raw = e.dataTransfer.getData(ASSET_MIME);
    if (raw) { try { onDropAsset?.(JSON.parse(raw) as DragAsset, at); } catch { /* ignore malformed payloads */ } return; }
    if (e.dataTransfer.files.length) onDropFiles?.([...e.dataTransfer.files], at);
  };

  const menuItems: { id: ItemAction; text: string; disabled?: boolean; danger?: boolean }[] = menu ? [
    { id: 'split', text: `${t('splitAtPlayhead')}  S`, disabled: !(time > menu.item.start && time < menu.item.start + menu.item.duration) },
    { id: 'duplicate', text: `${t('duplicate')}  ⌘D` },
    { id: 'copy', text: `${t('copy')}  ⌘C` },
    { id: 'paste', text: `${t('pasteAtPlayhead')}  ⌘V`, disabled: !canPaste },
    { id: 'pin', text: pinned.has(menu.item.id) ? t('unpin') : t('pin') },
    { id: 'delete', text: `${t('delete')}  ⌫`, danger: true },
  ] : [];

  return (
    <div style={{ userSelect: 'none' }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 6, justifyContent: 'flex-end', margin: '0 0 6px', minHeight: 24 }}>
        {editable && <span style={{ fontSize: 11, color: '#8d99a8', marginRight: 'auto', paddingLeft: NAME_W }}>{t('timelineHint')}</span>}
        {editable && <button type="button" className={`tl-chip${snap ? ' on' : ''}`} aria-pressed={snap} onClick={() => setSnap(!snap)}>🧲 {t('snap')}</button>}
        <Button variant="inline-icon" iconName="zoom-out" ariaLabel={t('zoomOut')} disabled={zoom <= 1} onClick={() => setZoomAround(zoom / 1.5)} />
        <input type="range" aria-label="zoom" min={0} max={100} value={Math.round((Math.log(zoom) / Math.log(40)) * 100)}
          onChange={(e) => setZoomAround(Math.exp((Number(e.target.value) / 100) * Math.log(40)))} style={{ width: 110 }} />
        <Button variant="inline-icon" iconName="zoom-in" ariaLabel={t('zoomIn')} disabled={zoom >= 40} onClick={() => setZoomAround(zoom * 1.5)} />
        <Button variant="inline-icon" iconName="zoom-to-fit" ariaLabel={t('zoomFit')} onClick={() => setZoomAround(1)} />
      </div>
      <div style={{ display: 'flex' }} onPointerMove={move} onPointerUp={end} onPointerCancel={() => { setDrag(null); setScrub(false); }}>
        <div style={{ width: NAME_W, flexShrink: 0 }}>
          <div style={{ height: 18 }} />
          {tracks.map((tr) => (
            <div key={tr.id} className="tl-trackname" style={{ height: tr.kind === 'caption' ? 18 : 30, marginBottom: 4, fontSize: 11, color: tr.hidden ? '#5b6573' : '#8d99a8', display: 'flex', alignItems: 'center', gap: 2 }}>
              <span style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', flex: 1, textDecoration: tr.hidden ? 'line-through' : undefined }} title={tr.name}>{tr.name}</span>
              {editable && onOps && (
                <span className="tl-trackbtns">
                  <button type="button" title={t('hideTrack')} onClick={() => onOps([{ op: 'update_track', trackId: tr.id, set: { hidden: !tr.hidden } }], 'track visibility')}>{tr.hidden ? '◌' : '●'}</button>
                  {(tr.kind === 'audio' || tr.kind === 'video' || tr.kind === 'overlay') && (
                    <button type="button" title={t('muteTrack')} onClick={() => onOps([{ op: 'update_track', trackId: tr.id, set: { muted: !tr.muted } }], 'track mute')}>{tr.muted ? '🔇' : '🔊'}</button>
                  )}
                </span>
              )}
            </div>
          ))}
          {editable && onOps && (
            <select aria-label={t('addTrack')} value="" onChange={(e) => { if (e.target.value) onOps([{ op: 'add_track', kind: e.target.value }], 'add track'); }}
              style={{ fontSize: 11, width: NAME_W - 6, background: 'transparent', color: '#8d99a8', border: '1px dashed rgba(127,127,127,0.5)', borderRadius: 4 }}>
              <option value="">+ {t('addTrack')}</option>
              {['overlay', 'text', 'caption', 'audio'].map((k) => <option key={k} value={k}>{t(`trackKind_${k}` as never)}</option>)}
            </select>
          )}
        </div>
        <div ref={scrollRef} style={{ flex: 1, minWidth: 0, overflowX: zoom > 1 ? 'auto' : 'hidden', overflowY: 'hidden', paddingRight: 12 }} onWheel={onWheel}>
          <div ref={innerRef} style={{ position: 'relative', width: `${zoom * 100}%`, touchAction: 'none' }}>
            <div style={{ position: 'relative', height: 18, cursor: 'ew-resize' }}
              onPointerDown={(e) => { (e.target as HTMLElement).setPointerCapture(e.pointerId); setScrub(true); onSeek(Math.min(duration, timeAt(e.clientX))); }}>
              {Array.from({ length: Math.floor(viewDur / tick) + 1 }, (_, k) => (
                <span key={k} style={{ position: 'absolute', left: pct(k * tick), fontSize: 10, color: '#8d99a8', borderLeft: '1px solid rgba(127,127,127,0.35)', paddingLeft: 3, height: 12, lineHeight: '12px', whiteSpace: 'nowrap' }}>
                  {fmtTime(k * tick)}
                </span>
              ))}
            </div>
            {tracks.map((tr) => (
              <div key={tr.id}
                style={{ position: 'relative', height: tr.kind === 'caption' ? 18 : 30, marginBottom: 4, background: dropAt?.trackId === tr.id ? 'rgba(9,114,211,0.16)' : 'rgba(127,127,127,0.12)', borderRadius: 3 }}
                onClick={(e) => { if (e.target === e.currentTarget) { onSelect(undefined); onSeek(Math.min(duration, timeAt(e.clientX))); } }}
                onDragOver={(e) => { if (!accepts(e)) return; e.preventDefault(); e.dataTransfer.dropEffect = 'copy'; setDropAt(target(e, tr.id, tr.kind)); }}
                onDragLeave={(e) => { if (!e.currentTarget.contains(e.relatedTarget as Node)) setDropAt(null); }}
                onDrop={(e) => onDrop(e, tr.id, tr.kind)}>
                {tr.items.map((i) => {
                  const thumb = i.type === 'clip' && tr.kind !== 'audio' && i.duration * pps > 90 ? thumbs[i.assetId] : undefined;
                  return (
                    <div key={i.id}
                      title={`${label(i, titles)}\n${fmtTime(i.start)}–${fmtTime(i.start + i.duration)}${i.provenance?.reason ? `\nAI: ${i.provenance.reason}` : ''}`}
                      onClick={(e) => { e.stopPropagation(); onSelect(i, tr.id); if (!editable) onSeek(i.start); }}
                      onContextMenu={(e) => { if (!editable || !onAction) return; e.preventDefault(); onSelect(i, tr.id); setMenu({ x: e.clientX, y: e.clientY, item: i, trackId: tr.id }); }}
                      onPointerDown={(e) => start(e, 'move', i, tr.id, tr.kind)}
                      className="tl-item"
                      style={{
                        position: 'absolute', ...geom(i), top: 2, bottom: 2, boxSizing: 'border-box',
                        background: COLORS[tr.kind] ?? '#64748b', opacity: selectedId && selectedId !== i.id ? 0.6 : 0.95, borderRadius: 3,
                        outline: selectedId === i.id ? '2px solid #22d3ee' : undefined, overflow: 'hidden',
                        cursor: editable ? (drag?.item.id === i.id ? 'grabbing' : 'grab') : 'pointer',
                        color: 'white', fontSize: 10, padding: thumb ? '0 6px 0 44px' : '0 6px', whiteSpace: 'nowrap', lineHeight: tr.kind === 'caption' ? '14px' : '26px',
                        zIndex: drag?.item.id === i.id ? 2 : 1,
                      }}>
                      {thumb && <img src={thumb} alt="" draggable={false} style={{ position: 'absolute', left: 0, top: 0, height: '100%', width: 40, objectFit: 'cover', opacity: 0.85, pointerEvents: 'none' }} />}
                      {editable && (
                        <>
                          <span className={`tl-handle l${selectedId === i.id ? ' on' : ''}`} title={t('dragToTrim')} onPointerDown={(e) => start(e, 'trimL', i, tr.id, tr.kind)} />
                          <span className={`tl-handle r${selectedId === i.id ? ' on' : ''}`}
                            title={i.type === 'clip' && i.mediaKind !== 'image' ? t('dragToTrimOrStretch') : t('dragToTrim')}
                            onPointerDown={(e) => start(e, 'trimR', i, tr.id, tr.kind)} />
                        </>
                      )}
                      {i.type === 'clip' && (i.speed ?? 1) !== 1 && <span className="tl-speed">{(i.speed ?? 1).toFixed(2).replace(/\.?0+$/, '')}x</span>}
                      {pinned.has(i.id) ? '📌 ' : ''}{label(i, titles)}
                    </div>
                  );
                })}
              </div>
            ))}
            {dropAt && <div style={{ position: 'absolute', top: 18, bottom: 0, left: pct(dropAt.time), width: 2, background: '#0972d3', pointerEvents: 'none', zIndex: 4 }} />}
            {drag?.stretch && drag.item.type === 'clip' && (
              <div style={{ position: 'absolute', top: 0, left: pct(drag.item.start + drag.item.duration + live!.delta), transform: 'translateX(-50%)', background: '#111827', color: '#fff', fontSize: 11, padding: '0 6px', borderRadius: 4, zIndex: 5, pointerEvents: 'none' }}>
                {Math.min(4, Math.max(0.25, (drag.item.srcOut - drag.item.srcIn) / Math.max(0.1, drag.item.duration + live!.delta))).toFixed(2)}x
              </div>
            )}
            {live?.line != null && <div style={{ position: 'absolute', top: 18, bottom: 0, left: pct(live.line), width: 1, background: '#facc15', pointerEvents: 'none', zIndex: 4 }} />}
            <div style={{ position: 'absolute', top: 0, bottom: 0, left: pct(Math.min(time, viewDur)), width: 2, background: '#ef4444', pointerEvents: 'none', zIndex: 3 }}>
              <div style={{ position: 'absolute', top: 0, left: -5, width: 12, height: 10, background: '#ef4444', clipPath: 'polygon(0 0, 100% 0, 50% 100%)' }} />
            </div>
          </div>
        </div>
      </div>
      {editable && !timeline.tracks.some((tr) => tr.items.length) && <Box color="text-body-secondary" variant="small" textAlign="center">{t('dropHint')}</Box>}
      {menu && (
        <div role="menu" onPointerDown={(e) => e.stopPropagation()}
          style={{ position: 'fixed', left: menu.x, top: Math.min(menu.y, window.innerHeight - 240), zIndex: 3000, minWidth: 200, padding: '4px 0',
            background: awsui.colorBackgroundDropdownItemDefault, border: `1px solid ${awsui.colorBorderDropdownContainer}`, borderRadius: 8,
            boxShadow: '0 4px 20px rgba(0,0,0,0.25)' }}>
          {menuItems.map((m) => (
            <button key={m.id} type="button" role="menuitem" disabled={m.disabled}
              onClick={() => { onAction?.(m.id, menu.item, menu.trackId); setMenu(null); }}
              className="ctx-item" style={{ color: m.danger ? '#d91515' : undefined }}>{m.text}</button>
          ))}
        </div>
      )}
    </div>
  );
}
