import Box from '@cloudscape-design/components/box';
import Button from '@cloudscape-design/components/button';
import FormField from '@cloudscape-design/components/form-field';
import Input from '@cloudscape-design/components/input';
import KeyValuePairs from '@cloudscape-design/components/key-value-pairs';
import RadioGroup from '@cloudscape-design/components/radio-group';
import Select from '@cloudscape-design/components/select';
import Slider from '@cloudscape-design/components/slider';
import SpaceBetween from '@cloudscape-design/components/space-between';
import Table from '@cloudscape-design/components/table';
import Textarea from '@cloudscape-design/components/textarea';
import Toggle from '@cloudscape-design/components/toggle';
import TokenGroup from '@cloudscape-design/components/token-group';
import * as awsui from '@cloudscape-design/design-tokens';
import { CSSProperties, ReactNode, useEffect, useMemo, useRef, useState } from 'react';
import type { Asset, AssetMedia } from '../api/types';
import { useI18n } from '../i18n';
import { fmtTime } from '../util';
import AssetPicker from './AssetPicker';

/* ------------------------------------------------------------------ spec types (mirror services/ui_widgets.py) */
type Opt = { value: string; label: string; description?: string; assetId?: string; startSec?: number; endSec?: number; color?: string };
type Clip = { assetId: string; startSec: number; endSec: number; label?: string; reason?: string; score?: number };
export type Block = { id: string; type: string; label?: string; [k: string]: unknown };
export interface WidgetSpec { title?: string; description?: string; blocks: Block[]; submitLabel?: string | null; allowSkip?: boolean; interactive?: boolean }
export interface WidgetEntry { uiId: string; spec: WidgetSpec; answered?: boolean; answer?: Record<string, unknown> }

const INPUT = new Set(['choice', 'clips', 'compare', 'slider', 'range', 'text_input', 'select', 'toggle', 'color', 'order',
  'frame_point', 'assets', 'caption_style', 'text_list', 'rating', 'table']);

export const CAPTION_LOOKS: Record<string, CSSProperties> = {
  clean: { color: '#fff', fontWeight: 700, WebkitTextStroke: '1px #000', paintOrder: 'stroke fill' },
  bold: { color: '#FFE600', fontWeight: 900, WebkitTextStroke: '2px #000', paintOrder: 'stroke fill', textTransform: 'uppercase' },
  karaoke: { color: '#fff', fontWeight: 800, WebkitTextStroke: '1px #000', paintOrder: 'stroke fill' },
  pop: { color: '#fff', fontWeight: 900, fontSize: '1.25em', textShadow: '0 3px 0 #ff2d75, 0 6px 12px rgba(0,0,0,.5)' },
  boxed: { color: '#fff', fontWeight: 700, background: 'rgba(0,0,0,0.78)', padding: '2px 8px', borderRadius: 4 },
  minimal: { color: '#fff', fontWeight: 500, fontSize: '0.85em', textShadow: '0 1px 3px rgba(0,0,0,.8)' },
};

interface Ctx {
  media: Record<string, AssetMedia>;
  thumbs: Record<string, string | undefined>;
  titles: Record<string, string>;
  done: boolean;
}

/* ------------------------------------------------------------------ media helpers */
/** A frame/clip of an asset: shows the frame at `start`; hover (or `autoPlay`) loops start→end, muted. */
function ClipView({ assetId, start = 0, end, ctx, height = 96, controls, onTime, seekTo, style }: {
  assetId: string; start?: number; end?: number; ctx: Ctx; height?: number; controls?: boolean;
  onTime?: (t: number) => void; seekTo?: number; style?: CSSProperties;
}) {
  const ref = useRef<HTMLVideoElement>(null);
  const m = ctx.media[assetId];
  const url = m?.proxyUrl;
  useEffect(() => { if (ref.current && seekTo != null && Math.abs(ref.current.currentTime - seekTo) > 0.05) ref.current.currentTime = seekTo; }, [seekTo]);
  if (!url || m?.kind === 'image' || m?.kind === 'document') {
    const img = m?.imageUrl ?? m?.thumbnailUrl ?? ctx.thumbs[assetId];
    return img ? <img src={img} alt="" style={{ width: '100%', height, objectFit: 'cover', borderRadius: 4, display: 'block', ...style }} />
      : <div className="thumb-placeholder" style={{ height, ...style }} />;
  }
  return (
    <video ref={ref} src={`${url}#t=${start.toFixed(2)}`} muted={!controls} playsInline preload="metadata" controls={controls}
      style={{ width: '100%', height, objectFit: 'cover', borderRadius: 4, background: '#000', display: 'block', ...style }}
      onMouseEnter={(e) => { if (!controls) { e.currentTarget.currentTime = start; e.currentTarget.play().catch(() => undefined); } }}
      onMouseLeave={(e) => { if (!controls) { e.currentTarget.pause(); e.currentTarget.currentTime = start; } }}
      onTimeUpdate={(e) => { onTime?.(e.currentTarget.currentTime); if (end != null && e.currentTarget.currentTime > end) { e.currentTarget.currentTime = start; if (controls) e.currentTarget.pause(); } }} />
  );
}

function Pickable({ on, disabled, onClick, children, badge }: { on: boolean; disabled?: boolean; onClick: () => void; children: ReactNode; badge?: ReactNode }) {
  return (
    <div role="button" tabIndex={disabled ? -1 : 0} aria-pressed={on} onClick={() => !disabled && onClick()}
      onKeyDown={(e) => { if (!disabled && (e.key === 'Enter' || e.key === ' ')) { e.preventDefault(); onClick(); } }}
      style={{ position: 'relative', cursor: disabled ? 'default' : 'pointer', borderRadius: 6, padding: 6,
        outline: on ? '2px solid #0972d3' : `1px solid ${awsui.colorBorderDividerDefault}`, background: on ? 'rgba(9,114,211,0.07)' : undefined }}>
      {badge != null && <span style={{ position: 'absolute', top: 10, left: 10, zIndex: 1, background: '#0972d3', color: '#fff', borderRadius: 10, fontSize: 11, padding: '0 7px', fontWeight: 700 }}>{badge}</span>}
      {children}
    </div>
  );
}

/* ------------------------------------------------------------------ blocks */
function defaultValue(b: Block): unknown {
  switch (b.type) {
    case 'slider': return Number(b.default ?? b.min ?? 0);
    case 'range': return { startSec: Number(b.startSec ?? 0), endSec: Number(b.endSec ?? 0) };
    case 'text_input': return String(b.default ?? '');
    case 'select': return b.default ?? (b.options as Opt[])?.[0]?.value;
    case 'toggle': return Boolean(b.default);
    case 'color': return String(b.default ?? '#FFFFFF');
    case 'order': return ((b.items as Opt[]) ?? []).map((x) => x.value);
    case 'caption_style': return String(b.default ?? 'clean');
    case 'text_list': return ((b.items as { id: string; text: string }[]) ?? []).map((x) => ({ id: x.id, text: x.text }));
    case 'rating': return 0;
    default: return undefined;
  }
}

function BlockView({ b, value, set, ctx, submitNow }: { b: Block; value: unknown; set: (v: unknown) => void; ctx: Ctx; submitNow?: (v: unknown) => void }) {
  const { t } = useI18n();
  const d = ctx.done;
  const label = b.label as string | undefined;
  const wrap = (node: ReactNode) => label ? <FormField label={label} description={b.description as string | undefined} stretch>{node}</FormField> : <>{node}</>;
  switch (b.type) {
    case 'markdown':
      return <Box><span style={{ whiteSpace: 'pre-wrap' }}>{String(b.text ?? '')}</span></Box>;
    case 'kv':
      return wrap(<KeyValuePairs columns={Math.min(3, ((b.items as unknown[]) ?? []).length || 1) as 1 | 2 | 3}
        items={((b.items as { label: string; value: unknown }[]) ?? []).map((x) => ({ label: x.label, value: String(x.value ?? '') }))} />);
    case 'table': {
      const cols = (b.columns as string[]) ?? [];
      const rows = ((b.rows as unknown[][]) ?? []).map((r, i) => ({ i, r }));
      const sel = (value as number[] | undefined) ?? [];
      return wrap(<Table variant="embedded" items={rows} trackBy="i" wrapLines
        selectionType={b.selectable ? (b.multiple ? 'multi' : 'single') : undefined}
        selectedItems={rows.filter((x) => sel.includes(x.i))} isItemDisabled={() => d}
        onSelectionChange={(e) => set(e.detail.selectedItems.map((x) => x.i))}
        columnDefinitions={cols.map((c, ci) => ({ id: String(ci), header: c, cell: (x: { i: number; r: unknown[] }) => String(x.r[ci] ?? '') }))} />);
    }
    case 'media':
      return wrap(<SpaceBetween size="xxs">
        <ClipView assetId={String(b.assetId)} start={Number(b.startSec ?? 0)} end={b.endSec != null ? Number(b.endSec) : undefined} ctx={ctx} height={180} controls />
        {b.caption ? <Box variant="small" color="text-body-secondary">{String(b.caption)}</Box> : null}
      </SpaceBetween>);
    case 'timeline': {
      const segs = (b.segments as (Clip & { label?: string })[]) ?? [];
      const total = segs.reduce((s, x) => s + Math.max(0.1, x.endSec - x.startSec), 0) || 1;
      return wrap(<TimelineProposal segs={segs} total={total} ctx={ctx} />);
    }
    case 'choice': {
      const opts = (b.options as Opt[]) ?? [];
      const multi = Boolean(b.multiple);
      const cur = multi ? ((value as string[]) ?? []) : value as string | undefined;
      const toggle = (v: string) => multi ? set((cur as string[]).includes(v) ? (cur as string[]).filter((x) => x !== v) : [...(cur as string[]), v]) : (submitNow ? submitNow(v) : set(v));
      const style = (b.style as string) ?? (opts.some((o) => o.assetId) ? 'cards' : opts.some((o) => o.description) ? 'list' : 'buttons');
      if (style === 'list' && !multi) {
        return wrap(<RadioGroup value={(cur as string) ?? null} onChange={(e) => set(e.detail.value)} readOnly={d}
          items={opts.map((o) => ({ value: o.value, label: o.label, description: o.description }))} />);
      }
      if (style === 'cards') {
        return wrap(<div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(140px, 1fr))', gap: 8 }}>
          {opts.map((o) => {
            const on = multi ? (cur as string[]).includes(o.value) : cur === o.value;
            return (
              <Pickable key={o.value} on={on} disabled={d} onClick={() => toggle(o.value)}>
                {o.assetId ? <ClipView assetId={o.assetId} start={o.startSec} end={o.endSec} ctx={ctx} height={84} />
                  : o.color ? <div style={{ height: 40, borderRadius: 4, background: o.color }} /> : null}
                <Box variant="small" fontWeight="bold" padding={{ top: 'xxs' }}>{o.label}</Box>
                {o.description && <Box variant="small" color="text-body-secondary">{o.description}</Box>}
              </Pickable>
            );
          })}
        </div>);
      }
      return wrap(<div style={{ display: 'flex', flexWrap: 'wrap', gap: 6 }}>
        {opts.map((o) => {
          const on = multi ? (cur as string[]).includes(o.value) : cur === o.value;
          return <Button key={o.value} disabled={d && !on} variant={on ? 'primary' : 'normal'} onClick={() => !d && toggle(o.value)}>
            {o.color ? <span style={{ display: 'inline-block', width: 10, height: 10, borderRadius: 5, background: o.color, marginRight: 6 }} /> : null}{o.label}</Button>;
        })}
      </div>);
    }
    case 'clips': {
      const clips = (b.clips as Clip[]) ?? [];
      const picked = (value as number[] | undefined) ?? [];
      const multi = b.multiple !== false;
      const max = Number(b.max ?? clips.length);
      return wrap(<SpaceBetween size="xxs">
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(150px, 1fr))', gap: 8 }}>
          {clips.map((c, i) => {
            const n = picked.indexOf(i);
            return (
              <Pickable key={i} on={n >= 0} disabled={d} badge={n >= 0 && multi ? n + 1 : undefined}
                onClick={() => set(n >= 0 ? picked.filter((x) => x !== i) : multi ? (picked.length < max ? [...picked, i] : picked) : [i])}>
                <ClipView assetId={c.assetId} start={c.startSec} end={c.endSec} ctx={ctx} height={86} />
                <Box variant="small" fontWeight="bold" padding={{ top: 'xxs' }}>{c.label ?? ctx.titles[c.assetId] ?? ''}</Box>
                <Box variant="small" color="text-body-secondary">{fmtTime(c.startSec)}–{fmtTime(c.endSec)}{c.score != null ? ` · ${Math.round(c.score * 100)}%` : ''}</Box>
                {c.reason && <Box variant="small" color="text-body-secondary">{c.reason}</Box>}
              </Pickable>
            );
          })}
        </div>
        {!d && <Box variant="small" color="text-body-secondary">{t('wHoverToPreview')}{multi ? ` · ${t('wPickOrder')}` : ''}</Box>}
      </SpaceBetween>);
    }
    case 'compare': {
      const opts = (b.options as Opt[]) ?? [];
      return wrap(<div style={{ display: 'grid', gridTemplateColumns: `repeat(${Math.min(opts.length, 3)}, 1fr)`, gap: 10 }}>
        {opts.map((o) => (
          <Pickable key={o.value} on={value === o.value} disabled={d} onClick={() => set(o.value)}>
            {o.assetId && <ClipView assetId={o.assetId} start={o.startSec} end={o.endSec} ctx={ctx} height={160} controls />}
            <Box fontWeight="bold" padding={{ top: 'xxs' }}>{o.label}</Box>
            {o.description && <Box variant="small" color="text-body-secondary">{o.description}</Box>}
          </Pickable>
        ))}
      </div>);
    }
    case 'slider': {
      const lo = Number(b.min ?? 0), hi = Number(b.max ?? 100), step = Number(b.step ?? 1);
      const v = Number(value ?? lo);
      return wrap(<div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
        <div style={{ flex: 1 }}><Slider value={v} min={lo} max={hi} step={step} disabled={d} onChange={(e) => set(e.detail.value)}
          valueFormatter={(x) => `${x}${b.unit ?? ''}`} /></div>
        <Box fontWeight="bold">{v}{String(b.unit ?? '')}</Box>
      </div>);
    }
    case 'range':
      return wrap(<RangeBlock b={b} value={value as { startSec: number; endSec: number }} set={set} ctx={ctx} />);
    case 'text_input':
      return wrap(b.multiline
        ? <Textarea value={String(value ?? '')} placeholder={b.placeholder as string | undefined} readOnly={d} onChange={(e) => set(e.detail.value)} rows={3} />
        : <Input value={String(value ?? '')} placeholder={b.placeholder as string | undefined} readOnly={d} onChange={(e) => set(e.detail.value)} />);
    case 'select': {
      const opts = (b.options as Opt[]) ?? [];
      const o = opts.find((x) => x.value === value);
      return wrap(<Select expandToViewport disabled={d} selectedOption={o ? { value: o.value, label: o.label } : null}
        options={opts.map((x) => ({ value: x.value, label: x.label, description: x.description }))} onChange={(e) => set(e.detail.selectedOption.value)} />);
    }
    case 'toggle':
      return <Toggle checked={Boolean(value)} disabled={d} onChange={(e) => set(e.detail.checked)}>{label}</Toggle>;
    case 'color': {
      const sw = (b.swatches as string[]) ?? ['#FFFFFF', '#000000', '#FFE600', '#FF2D75', '#00D1FF', '#22C55E', '#F97316', '#8B5CF6'];
      const v = String(value ?? '#FFFFFF');
      return wrap(<div style={{ display: 'flex', alignItems: 'center', gap: 6, flexWrap: 'wrap' }}>
        {sw.map((c) => (
          <button key={c} type="button" aria-label={c} disabled={d} onClick={() => set(c)}
            style={{ width: 28, height: 28, borderRadius: 14, background: c, cursor: d ? 'default' : 'pointer',
              border: v.toLowerCase() === c.toLowerCase() ? '3px solid #0972d3' : `1px solid ${awsui.colorBorderDividerDefault}` }} />
        ))}
        <input type="color" value={/^#[0-9a-f]{6}$/i.test(v) ? v : '#ffffff'} disabled={d} onChange={(e) => set(e.target.value)} style={{ width: 36, height: 28, border: 0, background: 'none' }} />
        <Box variant="small">{v}</Box>
      </div>);
    }
    case 'order':
      return wrap(<OrderBlock items={(b.items as Opt[]) ?? []} value={(value as string[]) ?? []} set={set} ctx={ctx} />);
    case 'frame_point':
      return wrap(<FramePoint b={b} value={value as { x: number; y: number } | undefined} set={set} ctx={ctx} />);
    case 'assets':
      return wrap(<AssetsBlock b={b} value={(value as Asset[] | string[] | undefined) ?? []} set={set} ctx={ctx} />);
    case 'caption_style': {
      const sample = String(b.sample ?? t('wCaptionSample'));
      return wrap(<div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(120px, 1fr))', gap: 8 }}>
        {Object.entries(CAPTION_LOOKS).map(([k, look]) => (
          <Pickable key={k} on={value === k} disabled={d} onClick={() => set(k)}>
            <div style={{ height: 64, borderRadius: 4, display: 'flex', alignItems: 'center', justifyContent: 'center', textAlign: 'center',
              background: 'linear-gradient(135deg,#334155,#0f172a)', fontSize: 15, padding: 4 }}>
              <span style={look}>{k === 'karaoke' ? <>{sample.split(' ')[0]} <span style={{ color: '#FFE600' }}>{sample.split(' ').slice(1).join(' ')}</span></> : sample}</span>
            </div>
            <Box variant="small" fontWeight="bold" textAlign="center" padding={{ top: 'xxs' }}>{k}</Box>
          </Pickable>
        ))}
      </div>);
    }
    case 'text_list': {
      const items = (b.items as { id: string; text: string; startSec?: number }[]) ?? [];
      const cur = (value as { id: string; text: string }[]) ?? [];
      return wrap(<SpaceBetween size="xxs">
        {items.map((it, i) => (
          <div key={it.id} style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
            <Box variant="small" color="text-body-secondary"><span style={{ display: 'inline-block', width: 44 }}>{it.startSec != null ? fmtTime(it.startSec) : `#${i + 1}`}</span></Box>
            <div style={{ flex: 1 }}><Input value={cur[i]?.text ?? it.text} readOnly={d}
              onChange={(e) => set(items.map((x, j) => ({ id: x.id, text: j === i ? e.detail.value : (cur[j]?.text ?? x.text) })))} /></div>
          </div>
        ))}
      </SpaceBetween>);
    }
    case 'rating': {
      const max = Number(b.max ?? 5), v = Number(value ?? 0);
      return wrap(<div style={{ display: 'flex', gap: 4 }}>
        {Array.from({ length: max }, (_, i) => (
          <button key={i} type="button" disabled={d} aria-label={`${i + 1}`} onClick={() => set(i + 1)}
            style={{ fontSize: 22, background: 'none', border: 0, cursor: d ? 'default' : 'pointer', color: i < v ? '#f59e0b' : '#9ca3af' }}>★</button>
        ))}
      </div>);
    }
    default:
      return null;
  }
}

function TimelineProposal({ segs, total, ctx }: { segs: (Clip & { label?: string })[]; total: number; ctx: Ctx }) {
  const [open, setOpen] = useState<number | null>(null);
  return (
    <SpaceBetween size="xs">
      <div style={{ display: 'flex', gap: 2, height: 40 }}>
        {segs.map((s, i) => (
          <div key={i} title={`${s.label ?? ''} ${fmtTime(s.startSec)}–${fmtTime(s.endSec)}`} onClick={() => setOpen(open === i ? null : i)}
            style={{ flex: Math.max(0.1, s.endSec - s.startSec) / total, minWidth: 6, cursor: 'pointer', borderRadius: 3, overflow: 'hidden', position: 'relative',
              outline: open === i ? '2px solid #22d3ee' : undefined, background: '#2563eb' }}>
            {ctx.thumbs[s.assetId] && <img src={ctx.thumbs[s.assetId]} alt="" style={{ width: '100%', height: '100%', objectFit: 'cover', opacity: 0.7 }} />}
            <span style={{ position: 'absolute', left: 4, bottom: 2, fontSize: 10, color: '#fff', textShadow: '0 1px 2px #000', whiteSpace: 'nowrap' }}>{i + 1}</span>
          </div>
        ))}
      </div>
      <Box variant="small" color="text-body-secondary">{segs.length} · {fmtTime(total)}</Box>
      {open != null && segs[open] && (
        <SpaceBetween size="xxs">
          <ClipView assetId={segs[open].assetId} start={segs[open].startSec} end={segs[open].endSec} ctx={ctx} height={160} controls />
          <Box variant="small">{open + 1}. {segs[open].label ?? ''} ({fmtTime(segs[open].startSec)}–{fmtTime(segs[open].endSec)})</Box>
        </SpaceBetween>
      )}
    </SpaceBetween>
  );
}

function RangeBlock({ b, value, set, ctx }: { b: Block; value: { startSec: number; endSec: number }; set: (v: unknown) => void; ctx: Ctx }) {
  const { t } = useI18n();
  const assetId = String(b.assetId);
  const dur = ctx.media[assetId]?.durationSec ?? Number(b.max ?? value.endSec + 10);
  const lo = Number(b.min ?? Math.max(0, value.startSec - 15)), hi = Number(b.max ?? Math.min(dur, value.endSec + 15));
  const [now, setNow] = useState(value.startSec);
  const [seek, setSeek] = useState<number | undefined>(undefined);
  const v = value;
  return (
    <SpaceBetween size="xs">
      <ClipView assetId={assetId} start={v.startSec} end={v.endSec} ctx={ctx} height={180} controls onTime={setNow} seekTo={seek} />
      <div style={{ position: 'relative', height: 26, background: 'rgba(127,127,127,0.15)', borderRadius: 4 }}>
        <div style={{ position: 'absolute', top: 0, bottom: 0, left: `${((v.startSec - lo) / (hi - lo)) * 100}%`, width: `${((v.endSec - v.startSec) / (hi - lo)) * 100}%`, background: 'rgba(9,114,211,0.35)', borderRadius: 4 }} />
        <div style={{ position: 'absolute', top: 0, bottom: 0, left: `${((now - lo) / (hi - lo)) * 100}%`, width: 2, background: '#ef4444' }} />
      </div>
      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 10 }}>
        <FormField label={`${t('wStart')} ${fmtTime(v.startSec)}`}>
          <Slider min={lo} max={hi} step={0.1} value={v.startSec} disabled={ctx.done}
            onChange={(e) => { const s = Math.min(e.detail.value, v.endSec - 0.2); set({ ...v, startSec: +s.toFixed(2) }); setSeek(s); }} />
        </FormField>
        <FormField label={`${t('wEnd')} ${fmtTime(v.endSec)}`}>
          <Slider min={lo} max={hi} step={0.1} value={v.endSec} disabled={ctx.done}
            onChange={(e) => { const s = Math.max(e.detail.value, v.startSec + 0.2); set({ ...v, endSec: +s.toFixed(2) }); setSeek(s); }} />
        </FormField>
      </div>
      {!ctx.done && <SpaceBetween direction="horizontal" size="xs">
        <Button onClick={() => set({ ...v, startSec: +Math.min(now, v.endSec - 0.2).toFixed(2) })}>{t('wSetStartHere')}</Button>
        <Button onClick={() => set({ ...v, endSec: +Math.max(now, v.startSec + 0.2).toFixed(2) })}>{t('wSetEndHere')}</Button>
        <Box variant="small" color="text-body-secondary">{t('wLength')} {(v.endSec - v.startSec).toFixed(1)}s</Box>
      </SpaceBetween>}
    </SpaceBetween>
  );
}

function OrderBlock({ items, value, set, ctx }: { items: Opt[]; value: string[]; set: (v: unknown) => void; ctx: Ctx }) {
  const byId = useMemo(() => Object.fromEntries(items.map((x) => [x.value, x])), [items]);
  const [dragging, setDragging] = useState<string | null>(null);
  const move = (from: number, to: number) => {
    if (to < 0 || to >= value.length) return;
    const next = [...value];
    const [x] = next.splice(from, 1);
    next.splice(to, 0, x);
    set(next);
  };
  return (
    <SpaceBetween size="xxs">
      {value.map((v, i) => {
        const o = byId[v];
        if (!o) return null;
        return (
          <div key={v} draggable={!ctx.done}
            onDragStart={(e) => { setDragging(v); e.dataTransfer.effectAllowed = 'move'; }}
            onDragOver={(e) => { if (dragging) e.preventDefault(); }}
            onDrop={(e) => { e.preventDefault(); if (dragging) move(value.indexOf(dragging), i); setDragging(null); }}
            style={{ display: 'flex', alignItems: 'center', gap: 8, padding: 6, borderRadius: 6, border: `1px solid ${awsui.colorBorderDividerDefault}`,
              cursor: ctx.done ? 'default' : 'grab', opacity: dragging === v ? 0.5 : 1 }}>
            <Box variant="small" color="text-body-secondary">⋮⋮ {i + 1}</Box>
            {o.assetId && <div style={{ width: 72 }}><ClipView assetId={o.assetId} start={o.startSec} end={o.endSec} ctx={ctx} height={40} /></div>}
            <div style={{ flex: 1 }}><Box variant="small" fontWeight="bold">{o.label}</Box>{o.description && <Box variant="small" color="text-body-secondary">{o.description}</Box>}</div>
            {!ctx.done && <>
              <Button variant="inline-icon" iconName="angle-up" ariaLabel="up" disabled={i === 0} onClick={() => move(i, i - 1)} />
              <Button variant="inline-icon" iconName="angle-down" ariaLabel="down" disabled={i === value.length - 1} onClick={() => move(i, i + 1)} />
            </>}
          </div>
        );
      })}
    </SpaceBetween>
  );
}

function FramePoint({ b, value, set, ctx }: { b: Block; value?: { x: number; y: number }; set: (v: unknown) => void; ctx: Ctx }) {
  const { t } = useI18n();
  return (
    <SpaceBetween size="xxs">
      <div style={{ position: 'relative', cursor: ctx.done ? 'default' : 'crosshair' }}
        onClick={(e) => {
          if (ctx.done) return;
          const r = e.currentTarget.getBoundingClientRect();
          set({ x: +((e.clientX - r.left) / r.width).toFixed(3), y: +((e.clientY - r.top) / r.height).toFixed(3) });
        }}>
        <div style={{ pointerEvents: 'none' }}><ClipView assetId={String(b.assetId)} start={Number(b.atSec ?? 0)} ctx={ctx} height={220} style={{ objectFit: 'contain' }} /></div>
        {value && <div style={{ position: 'absolute', left: `${value.x * 100}%`, top: `${value.y * 100}%`, width: 22, height: 22, marginLeft: -11, marginTop: -11,
          borderRadius: 11, border: '3px solid #22d3ee', boxShadow: '0 0 0 2px rgba(0,0,0,.5)', pointerEvents: 'none' }} />}
      </div>
      <Box variant="small" color="text-body-secondary">{value ? `x ${Math.round(value.x * 100)}% · y ${Math.round(value.y * 100)}%` : t('wClickFrame')}</Box>
    </SpaceBetween>
  );
}

function AssetsBlock({ b, value, set, ctx }: { b: Block; value: (Asset | string)[]; set: (v: unknown) => void; ctx: Ctx }) {
  const { t } = useI18n();
  const [open, setOpen] = useState(false);
  const assets = value.filter((x): x is Asset => typeof x === 'object');
  return (
    <SpaceBetween size="xs">
      {!ctx.done && <Button iconName="folder-open" onClick={() => setOpen(true)}>{t('attachFromArchive')}</Button>}
      {value.length > 0 && <TokenGroup readOnly={ctx.done} items={value.map((a) => ({ label: typeof a === 'string' ? (ctx.titles[a] ?? a) : a.displayTitle }))}
        onDismiss={({ detail }) => set(value.filter((_, i) => i !== detail.itemIndex))} />}
      <AssetPicker visible={open} initial={assets} kinds={b.kinds as string[] | undefined} onDismiss={() => setOpen(false)}
        onConfirm={(a) => { set(b.multiple === false ? a.slice(0, 1) : a); setOpen(false); }} />
    </SpaceBetween>
  );
}

/* ------------------------------------------------------------------ widget */
function serialize(b: Block, v: unknown): unknown {
  if (b.type === 'clips') return ((v as number[]) ?? []).map((i) => { const c = (b.clips as Clip[])[i]; return { assetId: c.assetId, startSec: c.startSec, endSec: c.endSec, label: c.label }; });
  if (b.type === 'assets') return ((v as (Asset | string)[]) ?? []).map((a) => (typeof a === 'string' ? a : a.assetId));
  return v;
}

function summarize(spec: WidgetSpec, values: Record<string, unknown>, t: (k: never) => string): string {
  const parts: string[] = [];
  for (const b of spec.blocks) {
    if (!INPUT.has(b.type) || values[b.id] === undefined) continue;
    const v = values[b.id];
    const name = (b.label as string) ?? '';
    let s: string;
    if (b.type === 'choice' || b.type === 'compare' || b.type === 'select') {
      const opts = (b.options as Opt[]) ?? [];
      const lab = (x: unknown) => opts.find((o) => o.value === x)?.label ?? String(x);
      s = Array.isArray(v) ? v.map(lab).join(', ') : lab(v);
    } else if (b.type === 'clips') s = (t as unknown as (k: string, p: Record<string, unknown>) => string)('wClips', { n: (v as unknown[]).length });
    else if (b.type === 'range') { const r = v as { startSec: number; endSec: number }; s = `${fmtTime(r.startSec)}–${fmtTime(r.endSec)}`; }
    else if (b.type === 'frame_point') { const p = v as { x: number; y: number }; s = `(${Math.round(p.x * 100)}%, ${Math.round(p.y * 100)}%)`; }
    else if (b.type === 'order') s = t('wReordered' as never);
    else if (b.type === 'text_list') s = t('wEdited' as never);
    else if (b.type === 'assets') s = `${(v as unknown[]).length}`;
    else if (b.type === 'toggle') s = v ? 'ON' : 'OFF';
    else if (b.type === 'table') s = `${(v as unknown[]).length}`;
    else s = String(v);
    parts.push(name ? `${name}: ${s}` : s);
  }
  return parts.join(' · ') || '✓';
}

export default function ChatWidget({ w, media, thumbs, titles, onSubmit }: {
  w: WidgetEntry; media: Record<string, AssetMedia>; thumbs: Record<string, string | undefined>; titles: Record<string, string>;
  onSubmit: (uiId: string, value: unknown, display: string) => void;
}) {
  const { t } = useI18n();
  const spec = w.spec;
  const [values, setValues] = useState<Record<string, unknown>>(() => w.answer && typeof w.answer === 'object'
    ? Object.fromEntries(spec.blocks.map((b) => [b.id, rehydrate(b, (w.answer as Record<string, unknown>)[b.id])]))
    : Object.fromEntries(spec.blocks.map((b) => [b.id, defaultValue(b)])));
  const [sent, setSent] = useState(false);
  const done = Boolean(w.answered) || sent;
  const ctx: Ctx = { media, thumbs, titles, done };
  const inputs = spec.blocks.filter((b) => INPUT.has(b.type) && !(b.type === 'table' && !b.selectable));
  // One single-choice button row and nothing else to fill in → a click submits.
  const quick = inputs.length === 1 && inputs[0].type === 'choice' && !inputs[0].multiple && !spec.submitLabel
    && ((inputs[0].style ?? 'buttons') === 'buttons') && !(inputs[0].options as Opt[]).some((o) => o.assetId || o.description);
  const missing = inputs.some((b) => {
    const v = values[b.id];
    if (b.type === 'clips') return ((v as unknown[]) ?? []).length < Number(b.min ?? 1);
    if (['choice', 'compare', 'frame_point', 'select'].includes(b.type)) return v == null || (Array.isArray(v) && !v.length);
    if (b.type === 'assets') return !((v as unknown[]) ?? []).length;
    return false;
  });
  const submit = (override?: Record<string, unknown>) => {
    const vals = { ...values, ...override };
    const out = Object.fromEntries(inputs.map((b) => [b.id, serialize(b, vals[b.id])]));
    setSent(true);
    onSubmit(w.uiId, out, summarize(spec, vals, t as never));
  };
  return (
    <div style={{ border: `1px solid ${awsui.colorBorderDividerDefault}`, borderRadius: 10, padding: 14, background: awsui.colorBackgroundContainerContent }}>
      <SpaceBetween size="m">
        {(spec.title || spec.description) && <div>
          {spec.title && <Box fontWeight="bold" fontSize="heading-s">{spec.title}</Box>}
          {spec.description && <Box variant="small" color="text-body-secondary">{spec.description}</Box>}
        </div>}
        {spec.blocks.map((b) => (
          <BlockView key={b.id} b={b} value={values[b.id]} ctx={ctx}
            set={(v) => setValues((x) => ({ ...x, [b.id]: v }))}
            submitNow={quick && b === inputs[0] && !done ? (v) => { setValues((x) => ({ ...x, [b.id]: v })); submit({ [b.id]: v }); } : undefined} />
        ))}
        {inputs.length > 0 && !quick && (
          done ? <Box variant="small" color="text-status-success">✓ {t('wSubmitted')}</Box> : (
            <SpaceBetween direction="horizontal" size="xs">
              <Button variant="primary" disabled={missing} onClick={() => submit()}>{spec.submitLabel || t('confirm')}</Button>
              {spec.allowSkip && <Button variant="link" onClick={() => { setSent(true); onSubmit(w.uiId, null, t('wSkipped')); }}>{t('wSkip')}</Button>}
            </SpaceBetween>
          )
        )}
        {quick && done && <Box variant="small" color="text-status-success">✓ {t('wSubmitted')}</Box>}
      </SpaceBetween>
    </div>
  );
}

/** Turn a stored answer back into the block's UI state (clips are stored as objects, shown as indexes). */
function rehydrate(b: Block, v: unknown): unknown {
  if (v == null) return defaultValue(b);
  if (b.type === 'clips' && Array.isArray(v)) {
    const clips = (b.clips as Clip[]) ?? [];
    return v.map((c) => clips.findIndex((x) => x.assetId === (c as Clip).assetId && x.startSec === (c as Clip).startSec)).filter((i) => i >= 0);
  }
  return v;
}

/** Asset ids a widget needs media for (players / frames). */
export function widgetAssetIds(spec: WidgetSpec): string[] {
  const ids = new Set<string>();
  for (const b of spec.blocks) {
    if (typeof b.assetId === 'string') ids.add(b.assetId);
    for (const k of ['clips', 'options', 'items', 'segments']) for (const x of (b[k] as { assetId?: string }[] | undefined) ?? []) if (x?.assetId) ids.add(x.assetId);
  }
  return [...ids];
}

/** Legacy ask_user questions rendered through the same widget. */
export function questionToWidget(q: { questionId: string; question: string; kind: string; options?: string[];
  clips?: Clip[]; multiple?: boolean; estimate?: Record<string, unknown> }): WidgetSpec {
  const blocks: Block[] = [];
  if (q.kind === 'confirm' && q.estimate) blocks.push({ id: 'estimate', type: 'kv', items: Object.entries(q.estimate).map(([label, value]) => ({ label, value })) });
  if (q.kind === 'choice') blocks.push({ id: 'answer', type: 'choice', options: (q.options ?? []).map((o) => ({ value: o, label: o })) });
  if (q.kind === 'confirm') blocks.push({ id: 'answer', type: 'choice', options: [{ value: 'yes', label: '✓ Yes' }, { value: 'no', label: 'No' }] });
  if (q.kind === 'clips') blocks.push({ id: 'answer', type: 'clips', clips: q.clips ?? [], multiple: q.multiple ?? true });
  if (q.kind === 'text' || q.kind === 'range') blocks.push({ id: 'answer', type: 'text_input', multiline: true });
  return { title: q.question, blocks, interactive: true };
}

/** Legacy questions expect the bare value (string / boolean / clips), not {blockId: value}. */
export function legacyValue(kind: string, v: Record<string, unknown> | null): unknown {
  const a = v?.answer;
  if (kind === 'confirm') return a === 'yes';
  return a;
}

