import Box from '@cloudscape-design/components/box';
import type { Item, Timeline } from '@shorts/timeline';
import { timelineDuration } from '@shorts/timeline';
import { PointerEvent as RPointerEvent, useMemo, useRef, useState } from 'react';
import { fmtTime } from '../util';

const COLORS: Record<string, string> = {
  video: '#2563eb', overlay: '#7c3aed', text: '#d97706', caption: '#059669', audio: '#db2777',
};
const GUTTER = 80;

export type EditOp = Record<string, unknown>;

interface Props {
  timeline: Timeline;
  time: number;
  onSeek: (t: number) => void;
  selectedId?: string;
  onSelect: (item: Item | undefined, trackId?: string) => void;
  titles: Record<string, string>;
  /** Manual mode: enables drag/trim and reports ops. */
  editable?: boolean;
  onOps?: (ops: EditOp[], message: string) => void;
}

function label(i: Item, titles: Record<string, string>): string {
  if (i.type === 'clip') return titles[i.assetId] ?? i.assetId;
  if (i.type === 'text') return i.text;
  if (i.type === 'caption') return i.words.map((w) => w.w).join(' ');
  return i.type;
}

type Drag = { kind: 'move' | 'trimL' | 'trimR'; item: Item; trackId: string; trackKind: string; x0: number; dx: number; pxPerSec: number };

/** Timeline: L3 view in AI mode; drag/trim editing in manual mode. */
export default function TimelineStrip({ timeline, time, onSeek, selectedId, onSelect, titles, editable, onOps }: Props) {
  const duration = Math.max(1, timelineDuration(timeline));
  const pct = (t: number) => `${(t / duration) * 100}%`;
  const pinned = useMemo(() => new Set(timeline.pins.map((p) => p.split('/')[1])), [timeline.pins]);
  const tracks = timeline.tracks.filter((t) => t.items.length || t.kind === 'video' || editable);
  const laneRef = useRef<HTMLDivElement>(null);
  const [drag, setDrag] = useState<Drag | null>(null);

  const start = (e: RPointerEvent, kind: Drag['kind'], item: Item, trackId: string, trackKind: string) => {
    if (!editable) return;
    e.stopPropagation();
    (e.target as HTMLElement).setPointerCapture(e.pointerId);
    const lane = laneRef.current!;
    setDrag({ kind, item, trackId, trackKind, x0: e.clientX, dx: 0, pxPerSec: (lane.clientWidth - GUTTER - 12) / duration });
    onSelect(item, trackId);
  };
  const move = (e: RPointerEvent) => { if (drag) setDrag({ ...drag, dx: e.clientX - drag.x0 }); };
  const end = () => {
    if (!drag || !onOps) { setDrag(null); return; }
    const d = drag.dx / drag.pxPerSec;
    const i = drag.item;
    setDrag(null);
    if (Math.abs(drag.dx) < 3) return;
    if (drag.kind === 'move') {
      if (drag.trackKind === 'video') {
        const tr = timeline.tracks.find((t) => t.id === drag.trackId)!;
        const target = i.start + d + i.duration / 2;
        const others = tr.items.filter((x) => x.id !== i.id);
        const index = others.filter((x) => x.start + x.duration / 2 < target).length;
        onOps([{ op: 'move', itemId: i.id, index }], 'reorder');
      } else {
        onOps([{ op: 'move', itemId: i.id, start: Math.max(0, +(i.start + d).toFixed(3)) }], 'move');
      }
    } else if (i.type === 'clip' && i.mediaKind !== 'image') {
      const speed = i.speed ?? 1;
      const op = drag.kind === 'trimL' ? { op: 'trim', itemId: i.id, srcIn: Math.max(0, +(i.srcIn + d * speed).toFixed(3)) }
        : { op: 'trim', itemId: i.id, srcOut: +(i.srcOut + d * speed).toFixed(3) };
      onOps([op], 'trim');
    } else {
      if (drag.kind === 'trimL') onOps([{ op: 'update', itemId: i.id, set: { start: Math.max(0, i.start + d), duration: Math.max(0.1, i.duration - d) } }], 'trim');
      else onOps([{ op: 'trim', itemId: i.id, duration: Math.max(0.1, +(i.duration + d).toFixed(3)) }], 'trim');
    }
  };

  const geom = (i: Item) => {
    if (!drag || drag.item.id !== i.id) return { left: pct(i.start), width: `calc(${pct(i.duration)} - 2px)` };
    const d = drag.dx / drag.pxPerSec;
    if (drag.kind === 'move') return { left: pct(i.start + d), width: `calc(${pct(i.duration)} - 2px)` };
    if (drag.kind === 'trimL') return { left: pct(i.start + d), width: `calc(${pct(Math.max(0.1, i.duration - d))} - 2px)` };
    return { left: pct(i.start), width: `calc(${pct(Math.max(0.1, i.duration + d))} - 2px)` };
  };

  return (
    <div ref={laneRef} style={{ userSelect: 'none', touchAction: 'none' }} onPointerMove={move} onPointerUp={end} onPointerCancel={() => setDrag(null)}>
      <div style={{ position: 'relative', height: 18, cursor: 'pointer', marginLeft: GUTTER, marginRight: 12 }}
        onClick={(e) => { const r = e.currentTarget.getBoundingClientRect(); onSeek(((e.clientX - r.left) / r.width) * duration); }}>
        {Array.from({ length: 11 }, (_, i) => (
          <span key={i} style={{ position: 'absolute', left: `${i * 10}%`, fontSize: 10, color: '#8d99a8', transform: i === 0 ? 'none' : i === 10 ? 'translateX(-100%)' : 'translateX(-50%)' }}>
            {fmtTime((duration * i) / 10)}
          </span>
        ))}
      </div>
      <div style={{ position: 'relative' }}>
        {tracks.map((tr) => (
          <div key={tr.id} style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 4 }}>
            <div style={{ width: GUTTER - 8, fontSize: 11, color: '#8d99a8', flexShrink: 0 }}>{tr.name}</div>
            <div style={{ position: 'relative', flex: 1, marginRight: 12, height: tr.kind === 'caption' ? 18 : 30, background: 'rgba(127,127,127,0.12)', borderRadius: 3 }}
              onClick={(e) => { if (e.target === e.currentTarget) { onSelect(undefined); const r = e.currentTarget.getBoundingClientRect(); onSeek(((e.clientX - r.left) / r.width) * duration); } }}>
              {tr.items.map((i) => (
                <div key={i.id}
                  title={`${label(i, titles)}\n${fmtTime(i.start)}–${fmtTime(i.start + i.duration)}${i.provenance?.reason ? `\nAI: ${i.provenance.reason}` : ''}`}
                  onClick={(e) => { e.stopPropagation(); onSelect(i, tr.id); if (!editable) onSeek(i.start); }}
                  onPointerDown={(e) => start(e, 'move', i, tr.id, tr.kind)}
                  style={{
                    position: 'absolute', ...geom(i), top: 2, bottom: 2, boxSizing: 'border-box',
                    background: COLORS[tr.kind] ?? '#64748b', opacity: selectedId && selectedId !== i.id ? 0.55 : 0.95, borderRadius: 3,
                    outline: selectedId === i.id ? '2px solid #22d3ee' : undefined, overflow: 'hidden',
                    cursor: editable ? (drag?.item.id === i.id ? 'grabbing' : 'grab') : 'pointer',
                    color: 'white', fontSize: 10, padding: '0 6px', whiteSpace: 'nowrap', lineHeight: tr.kind === 'caption' ? '14px' : '26px',
                    zIndex: drag?.item.id === i.id ? 2 : 1,
                  }}>
                  {editable && selectedId === i.id && tr.kind !== 'caption' && (
                    <>
                      <span onPointerDown={(e) => start(e, 'trimL', i, tr.id, tr.kind)} style={{ position: 'absolute', left: 0, top: 0, bottom: 0, width: 6, cursor: 'ew-resize', background: 'rgba(255,255,255,0.55)' }} />
                      <span onPointerDown={(e) => start(e, 'trimR', i, tr.id, tr.kind)} style={{ position: 'absolute', right: 0, top: 0, bottom: 0, width: 6, cursor: 'ew-resize', background: 'rgba(255,255,255,0.55)' }} />
                    </>
                  )}
                  {pinned.has(i.id) ? '📌 ' : ''}{label(i, titles)}
                </div>
              ))}
            </div>
          </div>
        ))}
        <div style={{ position: 'absolute', top: 0, bottom: 0, left: `calc(${GUTTER}px + (100% - ${GUTTER + 12}px) * ${Math.min(1, time / duration)})`, width: 2, background: '#ef4444', pointerEvents: 'none', zIndex: 3 }} />
      </div>
      {!timeline.tracks.some((t) => t.items.length) && <Box color="text-body-secondary" variant="small">—</Box>}
    </div>
  );
}
