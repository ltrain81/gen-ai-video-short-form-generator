import Box from '@cloudscape-design/components/box';
import type { Item, Timeline } from '@shorts/timeline';
import { timelineDuration } from '@shorts/timeline';
import { useMemo } from 'react';
import { fmtTime } from '../util';

const COLORS: Record<string, string> = {
  video: '#2563eb', overlay: '#7c3aed', text: '#d97706', caption: '#059669', audio: '#db2777',
};

interface Props {
  timeline: Timeline;
  time: number;
  onSeek: (t: number) => void;
  selectedId?: string;
  onSelect: (item: Item | undefined, trackId?: string) => void;
  titles: Record<string, string>;
}

function label(i: Item, titles: Record<string, string>): string {
  if (i.type === 'clip') return titles[i.assetId] ?? i.assetId;
  if (i.type === 'text') return i.text;
  if (i.type === 'caption') return i.words.map((w) => w.w).join(' ');
  return i.type;
}

/** Simple (L3) timeline view: tracks, items, playhead. Click an item to select it for chat (L2) or editing. */
export default function TimelineStrip({ timeline, time, onSeek, selectedId, onSelect, titles }: Props) {
  const duration = Math.max(1, timelineDuration(timeline));
  const pct = (t: number) => `${(t / duration) * 100}%`;
  const pinned = useMemo(() => new Set(timeline.pins.map((p) => p.split('/')[1])), [timeline.pins]);
  const tracks = timeline.tracks.filter((t) => t.items.length || t.kind === 'video');
  return (
    <div style={{ userSelect: 'none' }}>
      <div style={{ position: 'relative', height: 18, cursor: 'pointer' }}
        onClick={(e) => { const r = e.currentTarget.getBoundingClientRect(); onSeek(((e.clientX - r.left) / r.width) * duration); }}>
        {Array.from({ length: 11 }, (_, i) => (
          <span key={i} style={{ position: 'absolute', left: `${i * 10}%`, fontSize: 10, color: '#8d99a8', transform: 'translateX(-50%)' }}>
            {fmtTime((duration * i) / 10)}
          </span>
        ))}
      </div>
      <div style={{ position: 'relative' }}>
        {tracks.map((tr) => (
          <div key={tr.id} style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 4 }}>
            <div style={{ width: 72, fontSize: 11, color: '#8d99a8', flexShrink: 0 }}>{tr.name}</div>
            <div style={{ position: 'relative', flex: 1, height: tr.kind === 'caption' ? 18 : 30, background: 'rgba(127,127,127,0.12)', borderRadius: 6 }}
              onClick={(e) => { if (e.target === e.currentTarget) onSelect(undefined); }}>
              {tr.items.map((i) => (
                <div key={i.id}
                  title={`${label(i, titles)}\n${fmtTime(i.start)}–${fmtTime(i.start + i.duration)}${i.provenance?.reason ? `\nAI: ${i.provenance.reason}` : ''}`}
                  onClick={() => { onSelect(i, tr.id); onSeek(i.start); }}
                  style={{
                    position: 'absolute', left: pct(i.start), width: `calc(${pct(i.duration)} - 2px)`, top: 2, bottom: 2,
                    background: COLORS[tr.kind] ?? '#64748b', opacity: selectedId && selectedId !== i.id ? 0.55 : 0.95, borderRadius: 4,
                    outline: selectedId === i.id ? '2px solid #22d3ee' : undefined, overflow: 'hidden', cursor: 'pointer',
                    color: 'white', fontSize: 10, padding: '0 4px', whiteSpace: 'nowrap', lineHeight: tr.kind === 'caption' ? '14px' : '26px',
                  }}>
                  {pinned.has(i.id) ? '📌 ' : ''}{label(i, titles)}
                </div>
              ))}
            </div>
          </div>
        ))}
        <div style={{ position: 'absolute', top: 0, bottom: 0, left: `calc(80px + (100% - 80px) * ${time / duration})`, width: 2, background: '#ef4444', pointerEvents: 'none' }} />
      </div>
      {!timeline.tracks.some((t) => t.items.length) && <Box color="text-body-secondary" variant="small">—</Box>}
    </div>
  );
}
