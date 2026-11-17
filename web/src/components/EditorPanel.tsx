import Box from '@cloudscape-design/components/box';
import Button from '@cloudscape-design/components/button';
import ColumnLayout from '@cloudscape-design/components/column-layout';
import FormField from '@cloudscape-design/components/form-field';
import ExpandableSection from '@cloudscape-design/components/expandable-section';
import Input from '@cloudscape-design/components/input';
import SegmentedControl from '@cloudscape-design/components/segmented-control';
import Select from '@cloudscape-design/components/select';
import SpaceBetween from '@cloudscape-design/components/space-between';
import Tabs from '@cloudscape-design/components/tabs';
import Textarea from '@cloudscape-design/components/textarea';
import * as awsui from '@cloudscape-design/design-tokens';
import type { ClipItem, Item, TextItem, Timeline } from '@shorts/timeline';
import { useEffect, useState } from 'react';
import AssetPicker from './AssetPicker';
import { api } from '../api/client';
import type { Asset, BrandKit } from '../api/types';
import { useI18n } from '../i18n';
import { fmtDate, fmtTime } from '../util';
import { ASSET_MIME, type EditOp } from './TimelineStrip';
import GeneratePanel from './GeneratePanel';
import { useApp } from '../App';

interface Props {
  projectId: string;
  timeline: Timeline;
  selected?: Item;
  time: number;
  titles: Record<string, string>;
  onOps: (ops: EditOp[], message: string) => void;
  onRestore: (version: number) => void;
  version: number;
  disabled?: boolean;
}

const LAYOUTS = ['crop', 'box', 'split', 'fit_blur', 'letterbox'] as const;
const TRANSITIONS = ['cut', 'fade', 'dissolve', 'dipToBlack', 'dipToWhite', 'wipeLeft', 'wipeUp', 'slideLeft', 'zoom', 'blur', 'circleopen', 'radial', 'pixelize', 'slideright', 'smoothup', 'squeezeh', 'coverleft', 'revealup', 'diagtl'];
const FX_PRESETS: Record<string, Record<string, unknown>> = {
  vivid: { type: 'color', saturation: 1.4, contrast: 1.08 }, warm: { type: 'color', temperature: -0.5 }, cool: { type: 'color', temperature: 0.5 },
  bw: { type: 'grayscale' }, sepia: { type: 'sepia' }, vignette: { type: 'vignette', amount: 0.6 }, blur: { type: 'blur', radius: 12 }, sharpen: { type: 'sharpen', amount: 0.6 },
};
const BOX_PRESETS: Record<string, { x: number; y: number; w: number; h: number }> = {
  square: { x: 0, y: 0.21875, w: 1, h: 0.5625 }, wide: { x: 0, y: 0.3418, w: 1, h: 0.3164 }, top: { x: 0, y: 0, w: 1, h: 0.667 }, bottom: { x: 0, y: 0.333, w: 1, h: 0.667 }, card: { x: 0.06, y: 0.2, w: 0.88, h: 0.5 },
};
const CAPTION_PRESETS = ['clean', 'bold', 'karaoke', 'pop', 'boxed', 'minimal'];

/** Manual (L3/L4) editing panel. Every change is an op → same engine as the AI, auto-pinned. */
export default function EditorPanel({ projectId, timeline, selected, time, titles, onOps, onRestore, version, disabled }: Props) {
  const { t } = useI18n();
  const { me } = useApp();
  const [tab, setTab] = useState('inspect');
  useEffect(() => { if (selected) setTab('inspect'); }, [selected?.id]);
  return (
    <div style={{ height: '100%', overflowY: 'auto', padding: '8px 16px 16px', opacity: disabled ? 0.6 : 1, pointerEvents: disabled ? 'none' : 'auto' }}>
      <Tabs activeTabId={tab} onChange={(e) => setTab(e.detail.activeTabId)} tabs={[
        { id: 'inspect', label: t('inspector'), content: <Inspector {...{ timeline, selected, time, onOps, projectId }} /> },
        { id: 'add', label: t('addMedia'), content: <AddMedia {...{ timeline, time, onOps, titles }} /> },
        ...(me?.features.generation ? [{ id: 'generate', label: t('generateTab'), content: <GeneratePanel {...{ projectId, timeline, time, onOps }} /> }] : []),
        { id: 'versions', label: t('versions'), content: <Versions {...{ projectId, version, onRestore }} /> },
      ]} />
    </div>
  );
}

/** Text/number input that commits on Enter or blur (local edit state, one op per change). */
function CommitInput({ value, onCommit, type, placeholder }: { value: string; onCommit: (v: string) => void; type?: 'number' | 'text'; placeholder?: string }) {
  const [v, setV] = useState(value);
  useEffect(() => setV(value), [value]);
  const commit = () => { if (v !== value) onCommit(v); };
  return <Input type={type ?? 'text'} value={v} placeholder={placeholder} onChange={(e) => setV(e.detail.value)} onBlur={commit}
    onKeyDown={(e) => { if (e.detail.key === 'Enter') commit(); }} />;
}

function num(v: string, fallback: number) { const n = Number(v); return Number.isFinite(n) ? n : fallback; }

function Inspector({ timeline, selected, time, onOps, projectId }: Pick<Props, 'timeline' | 'selected' | 'time' | 'onOps' | 'projectId'>) {
  const { t } = useI18n();
  const pins = timeline.pins.filter((p) => selected && p.startsWith(`items/${selected.id}`));
  const set = (s: Record<string, unknown>, msg = 'edit') => selected && onOps([{ op: 'update', itemId: selected.id, set: s }], msg);

  const global = (
    <SpaceBetween size="m">
      <FormField label={t('aspect')} description={`${timeline.settings.width}×${timeline.settings.height}`}>
        <SpaceBetween size="xs">
          <SegmentedControl selectedId={timeline.settings.aspect} options={['9:16', '1:1', '4:5', '16:9', '4:3', '21:9'].map((a) => ({ id: a, text: a }))}
            onChange={(e) => onOps([{ op: 'settings', set: { aspect: e.detail.selectedId } }], 'aspect')} />
          <ColumnLayout columns={4}>
            <FormField label={t('customRatio')}><CommitInput value={timeline.settings.aspect} placeholder="2:3" onCommit={(v) => onOps([{ op: 'settings', set: { aspect: v.trim() } }], 'aspect')} /></FormField>
            <FormField label="W px"><CommitInput type="number" value={String(timeline.settings.width)} onCommit={(v) => onOps([{ op: 'settings', set: { aspect: 'custom', width: num(v, timeline.settings.width), height: timeline.settings.height } }], 'size')} /></FormField>
            <FormField label="H px"><CommitInput type="number" value={String(timeline.settings.height)} onCommit={(v) => onOps([{ op: 'settings', set: { aspect: 'custom', width: timeline.settings.width, height: num(v, timeline.settings.height) } }], 'size')} /></FormField>
            <FormField label="FPS"><CommitInput type="number" value={String(timeline.settings.fps)} onCommit={(v) => onOps([{ op: 'settings', set: { fps: num(v, 30) } }], 'fps')} /></FormField>
          </ColumnLayout>
          <FormField label={t('canvasBackground')}><input type="color" value={/^#[0-9a-f]{6}$/i.test(timeline.settings.background ?? '') ? timeline.settings.background : '#000000'} onChange={(e) => onOps([{ op: 'settings', set: { background: e.target.value } }], 'background')} /></FormField>
        </SpaceBetween>
      </FormField>
      <FormField label={t('captions')}>
        <SpaceBetween direction="horizontal" size="xs">
          {CAPTION_PRESETS.map((p) => <Button key={p} onClick={() => onOps([{ op: 'captions', preset: p }], `captions ${p}`)}>{p}</Button>)}
          <Button iconName="remove" onClick={() => onOps([{ op: 'clear_track', track: 'c1' }], 'remove captions')}>{t('remove')}</Button>
        </SpaceBetween>
      </FormField>
      <MetadataEditor timeline={timeline} time={time} onOps={onOps} projectId={projectId} />
      <RawEditor label={t('rawSettings')} value={timeline.settings as unknown as Record<string, unknown>} onApply={(changed) => onOps([{ op: 'settings', set: changed }], 'raw settings')} />
    </SpaceBetween>
  );
  if (!selected) {
    return <SpaceBetween size="m"><Box color="text-body-secondary">{t('selectHint')}</Box>{global}</SpaceBetween>;
  }
  const playheadInside = time > selected.start + 0.1 && time < selected.start + selected.duration - 0.1;
  return (
    <SpaceBetween size="m">
      <SpaceBetween direction="horizontal" size="xs">
        <Button disabled={!playheadInside} onClick={() => onOps([{ op: 'split', itemId: selected.id, at: time }], 'split')}>{t('splitAtPlayhead')} (S)</Button>
        <Button iconName="remove" onClick={() => onOps([{ op: 'remove', itemId: selected.id }], 'delete')}>{t('delete')} (Del)</Button>
      </SpaceBetween>
      <Box variant="small" color="text-body-secondary">{fmtTime(selected.start)} – {fmtTime(selected.start + selected.duration)} · {selected.duration.toFixed(2)}s
        {selected.provenance?.reason ? <><br />AI: {selected.provenance.reason}</> : null}</Box>
      <TimingEditor item={selected} onMain={!!timeline.tracks.find((tr) => tr.kind === 'video' && tr.items.some((i) => i.id === selected.id))} onOps={onOps} />
      {selected.type === 'clip' && <ClipInspector c={selected} set={set} onOps={onOps}
        onMain={!!timeline.tracks.find((tr) => tr.kind === 'video' && tr.items.some((i) => i.id === selected.id)) && timeline.tracks.find((tr) => tr.kind === 'video')!.items.some((i) => i.id === selected.id)} />}
      {selected.type === 'text' && <TextInspector x={selected} set={set} />}
      {selected.type === 'caption' && (
        <FormField label={t('captions')}>
          <Select expandToViewport selectedOption={{ value: selected.preset, label: selected.preset }} options={CAPTION_PRESETS.map((p) => ({ value: p, label: p }))}
            onChange={(e) => set({ preset: e.detail.selectedOption.value })} />
        </FormField>
      )}
      <RawEditor label={t('rawItem')} value={selected as unknown as Record<string, unknown>} onApply={(changed) => set(changed, 'raw edit')} />
      <FormField label={t('pins')} description={t('pinsHint')}>
        {pins.length ? (
          <SpaceBetween size="xxs">
            {pins.map((p) => (
              <SpaceBetween key={p} direction="horizontal" size="xs" alignItems="center">
                <Box variant="code">📌 {p.split('/').slice(2).join('/') || t('wholeItem')}</Box>
                <Button variant="inline-link" onClick={() => onOps([{ op: 'pin', path: p, pinned: false }], 'unpin')}>{t('unpin')}</Button>
              </SpaceBetween>
            ))}
          </SpaceBetween>
        ) : <Button onClick={() => onOps([{ op: 'pin', path: `items/${selected.id}`, pinned: true }], 'pin')}>{t('pinItem')}</Button>}
      </FormField>
    </SpaceBetween>
  );
}

function MetadataEditor({ timeline, time, onOps, projectId }: Pick<Props, 'timeline' | 'time' | 'onOps' | 'projectId'>) {
  const { t } = useI18n();
  const [busy, setBusy] = useState(false);
  const m = timeline.meta;
  const suggest = async () => {
    setBusy(true);
    try {
      const s = await api.suggestMetadata(projectId);
      onOps([{ op: 'meta', set: { title: s.title, description: s.description, hashtags: s.hashtags } }], 'AI metadata');
    } finally { setBusy(false); }
  };
  return (
    <SpaceBetween size="s">
      <SpaceBetween direction="horizontal" size="xs" alignItems="center">
        <Box variant="awsui-key-label">{t('metadata')}</Box>
        <Button variant="inline-link" iconName="gen-ai" loading={busy} onClick={suggest}>{t('aiSuggest')}</Button>
      </SpaceBetween>
      <FormField label={t('title')}><CommitInput value={m.title ?? ''} onCommit={(v) => onOps([{ op: 'meta', set: { title: v } }], 'title')} /></FormField>
      <FormField label={t('description')}><CommitInput value={m.description ?? ''} onCommit={(v) => onOps([{ op: 'meta', set: { description: v } }], 'description')} /></FormField>
      <FormField label={t('hashtags')}><CommitInput value={(m.hashtags ?? []).join(' ')}
        onCommit={(v) => onOps([{ op: 'meta', set: { hashtags: v.split(/[\s,]+/).filter(Boolean).map((h) => (h.startsWith('#') ? h : `#${h}`)) } }], 'hashtags')} /></FormField>
      <FormField label={t('thumbnail')} description={m.thumbnailAt != null ? `${fmtTime(m.thumbnailAt)}` : '—'}>
        <Button onClick={() => onOps([{ op: 'meta', set: { thumbnailAt: Number(time.toFixed(2)) } }], 'thumbnail')}>{t('setThumbnail')}</Button>
      </FormField>
    </SpaceBetween>
  );
}

function ClipInspector({ c, set, onOps, onMain }: { c: ClipItem; set: (s: Record<string, unknown>, m?: string) => void; onOps: Props['onOps']; onMain: boolean }) {
  const { t } = useI18n();
  const r = (c.reframe ?? {}) as { layout?: string; path?: { t: number; x: number; y: number; scale?: number }[]; box?: { x: number; y: number; w: number; h: number }; fit?: string; background?: string };
  const p0 = r.path?.[0] ?? { x: c.transform?.x ?? 0.5, y: c.transform?.y ?? 0.5, scale: c.transform?.scale ?? 1 };
  const tf = { x: 0.5, y: 0.5, scale: 1, rotation: 0, ...(c.transform ?? {}) } as NonNullable<ClipItem['transform']> & { flipV?: boolean };
  // Changing the layout keeps the (auto-tracked) focus path; focus/zoom edits shift every point of it.
  const reframe = (patch: Record<string, unknown>) => set({ reframe: { mode: 'manual', ...r, layout: r.layout ?? 'crop', ...patch } }, 'reframe');
  const shiftPath = (dx: number, z?: number) => {
    const base = r.path?.length ? r.path : [{ t: c.srcIn, x: p0.x, y: p0.y, scale: p0.scale ?? 1 }];
    reframe({ path: base.map((q) => ({ ...q, x: Math.min(1, Math.max(0, q.x + dx)), scale: z ?? q.scale ?? 1 })) });
  };
  const fx = (c.effects ?? []) as Record<string, unknown>[];
  const fxOn = (k: string) => fx.some((e) => JSON.stringify(e) === JSON.stringify(FX_PRESETS[k]));
  const toggleFx = (k: string) => set({ effects: fxOn(k) ? fx.filter((e) => JSON.stringify(e) !== JSON.stringify(FX_PRESETS[k])) : [...fx, FX_PRESETS[k]] }, `effect ${k}`);
  return (
    <SpaceBetween size="m">
      {c.mediaKind !== 'image' && (
        <ColumnLayout columns={4}>
          <FormField label="In (s)"><CommitInput type="number" value={String(c.srcIn)} onCommit={(v) => onOps([{ op: 'trim', itemId: c.id, srcIn: num(v, c.srcIn) }], 'trim')} /></FormField>
          <FormField label="Out (s)"><CommitInput type="number" value={String(c.srcOut)} onCommit={(v) => onOps([{ op: 'trim', itemId: c.id, srcOut: num(v, c.srcOut) }], 'trim')} /></FormField>
          <FormField label={t('speed')}><CommitInput type="number" value={String(c.speed ?? 1)} onCommit={(v) => set({ speed: Math.min(16, Math.max(0.1, num(v, 1))) }, 'speed')} /></FormField>
          <FormField label={t('playMode')}>
            <Select expandToViewport selectedOption={{ value: c.mode ?? 'normal', label: t(`mode_${c.mode ?? 'normal'}` as never) }}
              options={['normal', 'reverse', 'freeze'].map((v) => ({ value: v, label: t(`mode_${v}` as never) }))}
              onChange={(e) => set({ mode: e.detail.selectedOption.value }, 'mode')} />
          </FormField>
        </ColumnLayout>
      )}
      {c.mediaKind !== 'audio' && onMain && (
        <FormField label={t('layout')} description={t('layoutHint')}>
          <SpaceBetween size="xs">
            <SegmentedControl selectedId={r.layout ?? 'crop'} options={LAYOUTS.map((l) => ({ id: l, text: t(`layout_${l}` as never) }))}
              onChange={(e) => reframe({ layout: e.detail.selectedId, ...(e.detail.selectedId === 'box' && !r.box ? { box: BOX_PRESETS.square } : {}) })} />
            {r.layout === 'box' && (
              <SpaceBetween size="xs">
                <SpaceBetween direction="horizontal" size="xxs">
                  {Object.entries(BOX_PRESETS).map(([k, b]) => <Button key={k} variant="normal" onClick={() => reframe({ box: b })}>{t(`box_${k}` as never)}</Button>)}
                </SpaceBetween>
                <ColumnLayout columns={4}>
                  {(['x', 'y', 'w', 'h'] as const).map((k) => (
                    <FormField key={k} label={k.toUpperCase()}><CommitInput type="number" value={String(r.box?.[k] ?? 0)} onCommit={(v) => reframe({ box: { ...(r.box ?? BOX_PRESETS.square), [k]: Math.min(1, Math.max(0, num(v, 0))) } })} /></FormField>
                  ))}
                </ColumnLayout>
                <ColumnLayout columns={2}>
                  <FormField label={t('fit')}>
                    <SegmentedControl selectedId={r.fit ?? 'cover'} options={[{ id: 'cover', text: t('fitCover') }, { id: 'contain', text: t('fitContain') }]} onChange={(e) => reframe({ fit: e.detail.selectedId })} />
                  </FormField>
                  <FormField label={t('boxBackground')}>
                    <SpaceBetween direction="horizontal" size="xxs" alignItems="center">
                      <SegmentedControl selectedId={(r.background ?? 'black').startsWith('#') ? 'color' : (r.background ?? 'black')}
                        options={[{ id: 'black', text: t('bgBlack') }, { id: 'blur', text: t('bgBlur') }, { id: 'color', text: t('bgColor') }]}
                        onChange={(e) => reframe({ background: e.detail.selectedId === 'color' ? '#1e293b' : e.detail.selectedId })} />
                      {(r.background ?? '').startsWith('#') && <input type="color" value={r.background} onChange={(e) => reframe({ background: e.target.value })} />}
                    </SpaceBetween>
                  </FormField>
                </ColumnLayout>
              </SpaceBetween>
            )}
            <ColumnLayout columns={2}>
              <FormField label={t('focusX')}><CommitInput type="number" value={String(p0.x)} onCommit={(v) => shiftPath(num(v, p0.x) - p0.x)} /></FormField>
              <FormField label={t('zoom')}><CommitInput type="number" value={String(p0.scale ?? 1)} onCommit={(v) => shiftPath(0, Math.min(3, Math.max(1, num(v, 1))))} /></FormField>
            </ColumnLayout>
          </SpaceBetween>
        </FormField>
      )}
      {c.mediaKind !== 'audio' && (
        <FormField label={onMain ? t('flipRotate') : t('layerTransform')}>
          <ColumnLayout columns={onMain ? 2 : 4}>
            {!onMain && (['x', 'y', 'scale'] as const).map((k) => (
              <FormField key={k} label={k}><CommitInput type="number" value={String(tf[k])} onCommit={(v) => set({ transform: { ...tf, [k]: num(v, tf[k]) } }, 'transform')} /></FormField>
            ))}
            <FormField label={t('rotation')}><CommitInput type="number" value={String(tf.rotation ?? 0)} onCommit={(v) => set({ transform: { ...tf, rotation: num(v, 0) } }, 'rotate')} /></FormField>
            <FormField label={t('flip')}>
              <SpaceBetween direction="horizontal" size="xxs">
                <Button variant={tf.flipH ? 'primary' : 'normal'} onClick={() => set({ transform: { ...tf, flipH: !tf.flipH } }, 'flip')}>↔</Button>
                <Button variant={tf.flipV ? 'primary' : 'normal'} onClick={() => set({ transform: { ...tf, flipV: !tf.flipV } }, 'flip')}>↕</Button>
              </SpaceBetween>
            </FormField>
          </ColumnLayout>
        </FormField>
      )}
      {c.mediaKind !== 'audio' && (
        <FormField label={t('effects')}>
          <SpaceBetween direction="horizontal" size="xxs">
            {Object.keys(FX_PRESETS).map((k) => <Button key={k} variant={fxOn(k) ? 'primary' : 'normal'} onClick={() => toggleFx(k)}>{t(`fx_${k}` as never)}</Button>)}
          </SpaceBetween>
        </FormField>
      )}
      <ColumnLayout columns={4}>
        <FormField label={`${t('volume')} %`}><CommitInput type="number" value={String(Math.round((c.volume ?? (onMain ? 1 : 0)) * 100))} onCommit={(v) => set({ volume: Math.min(4, Math.max(0, num(v, 100) / 100)) }, 'volume')} /></FormField>
        <FormField label={`${t('opacity')} %`}><CommitInput type="number" value={String(Math.round((c.opacity ?? 1) * 100))} onCommit={(v) => set({ opacity: Math.min(1, Math.max(0, num(v, 100) / 100)) }, 'opacity')} /></FormField>
        <FormField label={t('fadeIn')}><CommitInput type="number" value={String(c.fadeIn ?? 0)} onCommit={(v) => set({ fadeIn: Math.max(0, num(v, 0)) }, 'fade in')} /></FormField>
        <FormField label={t('fadeOut')}><CommitInput type="number" value={String(c.fadeOut ?? 0)} onCommit={(v) => set({ fadeOut: Math.max(0, num(v, 0)) }, 'fade out')} /></FormField>
      </ColumnLayout>
      {onMain && (
        <ColumnLayout columns={2}>
          <FormField label={t('transitionIn')}>
            <Select expandToViewport filteringType="auto" selectedOption={{ value: c.transitionIn?.type ?? 'cut', label: c.transitionIn?.type ?? 'cut' }}
              options={TRANSITIONS.map((v) => ({ value: v, label: v }))}
              onChange={(e) => set({ transitionIn: e.detail.selectedOption.value === 'cut' ? null : { type: e.detail.selectedOption.value, duration: c.transitionIn?.duration ?? 0.4 } }, 'transition')} />
          </FormField>
          <FormField label={t('transitionSec')}>
            <CommitInput type="number" value={String(c.transitionIn?.duration ?? 0.4)} onCommit={(v) => c.transitionIn && set({ transitionIn: { ...c.transitionIn, duration: Math.min(3, Math.max(0.1, num(v, 0.4))) } }, 'transition')} />
          </FormField>
        </ColumnLayout>
      )}
    </SpaceBetween>
  );
}

/** L5: every property of the item as JSON. Applies only the keys you changed (each is pinned like any manual edit). */
function RawEditor({ value, onApply, label }: { value: Record<string, unknown>; onApply: (changed: Record<string, unknown>) => void; label: string }) {
  const { t } = useI18n();
  const strip = (v: Record<string, unknown>) => Object.fromEntries(Object.entries(v).filter(([k]) => !['id', 'type', 'provenance', 'items'].includes(k)));
  const src = JSON.stringify(strip(value), null, 2);
  const [text, setText] = useState(src);
  const [err, setErr] = useState('');
  useEffect(() => { setText(src); setErr(''); }, [src]);
  const apply = () => {
    try {
      const next = JSON.parse(text) as Record<string, unknown>;
      const before = strip(value);
      const changed = Object.fromEntries(Object.entries(next).filter(([k, v]) => JSON.stringify(v) !== JSON.stringify(before[k])));
      for (const k of Object.keys(before)) if (!(k in next)) changed[k] = null;
      if (Object.keys(changed).length) onApply(changed);
      setErr('');
    } catch (e) { setErr((e as Error).message); }
  };
  return (
    <ExpandableSection headerText={label} variant="footer">
      <SpaceBetween size="xs">
        <Textarea value={text} onChange={(e) => setText(e.detail.value)} rows={12} spellcheck={false} />
        {err && <Box color="text-status-error" variant="small">{err}</Box>}
        <SpaceBetween direction="horizontal" size="xs">
          <Button onClick={apply}>{t('apply')}</Button>
          <Button variant="link" onClick={() => setText(src)}>{t('reset')}</Button>
        </SpaceBetween>
      </SpaceBetween>
    </ExpandableSection>
  );
}

function TextInspector({ x, set }: { x: TextItem; set: (s: Record<string, unknown>, m?: string) => void }) {
  const { t } = useI18n();
  const [text, setText] = useState(x.text);
  useEffect(() => setText(x.text), [x.id, x.text]);
  const NAMED: Record<string, number> = { top: 0.18, upper: 0.3, center: 0.5, middle: 0.5, lower: 0.7, bottom: 0.8 };
  const pos = typeof x.position === 'object' && x.position ? x.position : { x: 0.5, y: NAMED[String(x.position ?? '').toLowerCase()] ?? 0.2, anchor: 'center' as const };
  const st = (typeof x.style === 'object' && x.style ? x.style : {}) as TextItem['style'] & { italic?: boolean };
  const anim = (x.animation ?? {}) as { in?: string; out?: string };
  const setPos = (k: string, v: number | string) => set({ position: { ...pos, [k]: v } }, 'text position');
  return (
    <SpaceBetween size="m">
      <FormField label={t('text')}><Textarea value={text} onChange={(e) => setText(e.detail.value)} onBlur={() => text !== x.text && set({ text }, 'text')} rows={3} /></FormField>
      <ColumnLayout columns={4}>
        <FormField label={`${t('size')} %`}><CommitInput type="number" value={String(Math.round((st.size ?? 0.05) * 1000) / 10)} onCommit={(v) => set({ style: { size: Math.min(0.4, Math.max(0.01, num(v, 5) / 100)) } }, 'text size')} /></FormField>
        <FormField label="X %"><CommitInput type="number" value={String(Math.round((pos.x ?? 0.5) * 100))} onCommit={(v) => setPos('x', Math.min(1, Math.max(0, num(v, 50) / 100)))} /></FormField>
        <FormField label="Y %"><CommitInput type="number" value={String(Math.round((pos.y ?? 0.2) * 100))} onCommit={(v) => setPos('y', Math.min(1, Math.max(0, num(v, 20) / 100)))} /></FormField>
        <FormField label={`${t('wrapWidth')} %`}><CommitInput type="number" value={String(Math.round((pos.maxWidth ?? 0.86) * 100))} onCommit={(v) => setPos('maxWidth', Math.min(1, Math.max(0.05, num(v, 86) / 100)))} /></FormField>
      </ColumnLayout>
      <ColumnLayout columns={4}>
        <FormField label={t('color')}><input type="color" value={/^#[0-9a-f]{6}$/i.test(st.color ?? '') ? st.color : '#ffffff'} onChange={(e) => set({ style: { color: e.target.value } }, 'text color')} /></FormField>
        <FormField label={t('textBackground')}>
          <SpaceBetween direction="horizontal" size="xxs" alignItems="center">
            <input type="color" value={/^#[0-9a-f]{6}/i.test(st.background ?? '') ? (st.background ?? '').slice(0, 7) : '#000000'} onChange={(e) => set({ style: { background: e.target.value } }, 'text background')} />
            {st.background && <Button variant="inline-icon" iconName="close" ariaLabel="none" onClick={() => set({ style: { background: null } }, 'text background')} />}
          </SpaceBetween>
        </FormField>
        <FormField label={t('weight')}><CommitInput type="number" value={String(st.weight ?? 800)} onCommit={(v) => set({ style: { weight: Math.min(900, Math.max(100, num(v, 800))) } }, 'weight')} /></FormField>
        <FormField label={t('rotation')}><CommitInput type="number" value={String((x as unknown as { rotation?: number }).rotation ?? 0)} onCommit={(v) => set({ rotation: num(v, 0) }, 'rotate')} /></FormField>
      </ColumnLayout>
      <ColumnLayout columns={3}>
        <FormField label={t('align')}>
          <SegmentedControl selectedId={st.align ?? 'center'} options={['left', 'center', 'right'].map((a) => ({ id: a, text: t(`align_${a}` as never) }))} onChange={(e) => set({ style: { align: e.detail.selectedId } }, 'align')} />
        </FormField>
        <FormField label={t('animIn')}>
          <Select expandToViewport selectedOption={{ value: anim.in ?? 'none', label: t(`anim_${anim.in ?? 'none'}` as never) }}
            options={['none', 'fade', 'pop', 'slideUp', 'slideDown', 'typewriter', 'wordByWord'].map((v) => ({ value: v, label: t(`anim_${v}` as never) }))}
            onChange={(e) => set({ animation: { ...anim, in: e.detail.selectedOption.value } }, 'animation')} />
        </FormField>
        <FormField label={t('animOut')}>
          <Select expandToViewport selectedOption={{ value: anim.out ?? 'none', label: t(`anim_${anim.out ?? 'none'}` as never) }}
            options={['none', 'fade'].map((v) => ({ value: v, label: t(`anim_${v}` as never) }))}
            onChange={(e) => set({ animation: { ...anim, out: e.detail.selectedOption.value } }, 'animation')} />
        </FormField>
      </ColumnLayout>
    </SpaceBetween>
  );
}

/** Exact start / length (seconds). Video & audio clips: length extends or shortens the source range; Alt-stretch on the timeline changes speed instead. */
function TimingEditor({ item, onMain, onOps }: { item: Item; onMain: boolean; onOps: Props['onOps'] }) {
  const { t } = useI18n();
  const [start, setStart] = useState(item.start.toFixed(2));
  const [dur, setDur] = useState(item.duration.toFixed(2));
  useEffect(() => { setStart(item.start.toFixed(2)); setDur(item.duration.toFixed(2)); }, [item.id, item.start, item.duration]);
  const media = item.type === 'clip' && item.mediaKind !== 'image';
  const commitDur = () => {
    const d = Number(dur);
    if (!Number.isFinite(d) || d < 0.1 || Math.abs(d - item.duration) < 0.005) { setDur(item.duration.toFixed(2)); return; }
    if (media && item.type === 'clip') onOps([{ op: 'trim', itemId: item.id, srcOut: +(item.srcIn + d * (item.speed ?? 1)).toFixed(3) }], 'length');
    else onOps([{ op: 'trim', itemId: item.id, duration: +d.toFixed(3) }], 'length');
  };
  const commitStart = () => {
    const s0 = Number(start);
    if (!Number.isFinite(s0) || s0 < 0 || Math.abs(s0 - item.start) < 0.005) { setStart(item.start.toFixed(2)); return; }
    onOps([{ op: 'move', itemId: item.id, start: +s0.toFixed(3) }], 'start');
  };
  return (
    <ColumnLayout columns={2}>
      <FormField label={t('startSec')} description={onMain ? t('mainTrackMagnetic') : undefined}>
        <Input type="number" step={0.1} value={start} disabled={onMain} onChange={(e) => setStart(e.detail.value)}
          onBlur={commitStart} onKeyDown={(e) => e.detail.key === 'Enter' && commitStart()} />
      </FormField>
      <FormField label={t('lengthSec')} description={media ? t('lengthHintMedia') : undefined}>
        <Input type="number" step={0.1} value={dur} onChange={(e) => setDur(e.detail.value)}
          onBlur={commitDur} onKeyDown={(e) => e.detail.key === 'Enter' && commitDur()} />
      </FormField>
    </ColumnLayout>
  );
}

function AddMedia({ timeline, time, onOps, titles }: Pick<Props, 'timeline' | 'time' | 'onOps' | 'titles'>) {
  const { t } = useI18n();
  const [assets, setAssets] = useState<Asset[]>([]);
  const [kits, setKits] = useState<BrandKit[]>([]);
  const [kit, setKit] = useState<string | null>(timeline.settings.brandKitId ?? null);
  const [text, setText] = useState('');
  const [picker, setPicker] = useState(false);
  useEffect(() => {
    api.assets().then((a) => setAssets(a.filter((x) => x.status === 'ready'))).catch(() => undefined);
    api.brandKits().then(setKits).catch(() => undefined);
  }, []);
  const main = timeline.tracks.find((tr) => tr.kind === 'video');
  return (
    <SpaceBetween size="m">
      <FormField label={t('addText')}>
        <SpaceBetween direction="horizontal" size="xs">
          <Input value={text} onChange={(e) => setText(e.detail.value)} placeholder={t('text')} />
          <Button disabled={!text.trim()} onClick={() => { onOps([{ op: 'add_text', text, start: time, duration: 3, role: 'title' }], 'add text'); setText(''); }}>{t('addAtPlayhead')}</Button>
        </SpaceBetween>
      </FormField>
      {kits.length > 0 && (
        <FormField label={t('brandKit')}>
          <SpaceBetween direction="horizontal" size="xs">
            <Select expandToViewport selectedOption={kit ? { value: kit, label: kits.find((k) => k.brandKitId === kit)?.name } : null} placeholder={t('brandKit')}
              options={kits.map((k) => ({ value: k.brandKitId, label: k.name }))} onChange={(e) => setKit(e.detail.selectedOption.value ?? null)} />
            <Button disabled={!kit} onClick={() => onOps([{ op: 'brand', brandKitId: kit }], 'brand kit')}>{t('apply')}</Button>
          </SpaceBetween>
        </FormField>
      )}
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
        <Box variant="awsui-key-label">{t('fromArchive')}</Box>
        <Button iconName="folder-open" onClick={() => setPicker(true)}>{t('addMultiple')}</Button>
      </div>
      <AssetPicker visible={picker} onDismiss={() => setPicker(false)} kinds={['video', 'image', 'audio']}
        title={t('addMultiple')} confirmText={t('addToTimeline')}
        onConfirm={(picked) => {
          setPicker(false);
          let index = main?.items.length ?? 0;
          const ops = picked.map((a) => (a.kind === 'audio'
            ? { op: 'add_clip', assetId: a.assetId, track: 'a1', start: time, srcIn: 0, srcOut: a.durationSec ?? 30 }
            : { op: 'add_clip', assetId: a.assetId, srcIn: 0, srcOut: Math.min(a.durationSec ?? 5, 5), duration: 3, index: index++ }));
          onOps(ops as never, `add ${picked.length} clips`);
        }} />
      {assets.map((a) => (
        <div key={a.assetId} draggable title={t('dragToTimeline')}
          onDragStart={(e) => { e.dataTransfer.setData(ASSET_MIME, JSON.stringify({ assetId: a.assetId, kind: a.kind, durationSec: a.durationSec, title: a.displayTitle })); e.dataTransfer.effectAllowed = 'copy'; }}
          style={{ cursor: 'grab', display: 'flex', gap: 10, alignItems: 'center', borderBottom: `1px solid ${awsui.colorBorderDividerDefault}`, paddingBottom: 8 }}>
          {a.thumbnailUrl ? <img src={a.thumbnailUrl} alt="" style={{ width: 72, aspectRatio: '16/9', objectFit: 'cover', borderRadius: 3 }} /> : <div className="thumb-placeholder" style={{ width: 72 }} />}
          <div style={{ flex: 1, minWidth: 0 }}>
            <Box variant="small" fontWeight="bold">{titles[a.assetId] ?? a.displayTitle}</Box>
            <Box variant="small" color="text-body-secondary">{t((a.kind ?? 'other') as never)} {a.durationSec ? `· ${fmtTime(a.durationSec)}` : ''}</Box>
          </div>
          <SpaceBetween direction="horizontal" size="xxs">
            <Button variant="inline-icon" iconName="add-plus" ariaLabel={t('addToMain')}
              onClick={() => onOps([{ op: 'add_clip', assetId: a.assetId, srcIn: 0, srcOut: Math.min(a.durationSec ?? 5, 5), duration: 3, index: main?.items.length }], 'add clip')} />
            {a.kind !== 'audio' && <Button variant="inline-link" onClick={() => onOps([{ op: 'add_clip', assetId: a.assetId, track: 'o1', start: time, srcIn: 0, srcOut: Math.min(a.durationSec ?? 3, 3), duration: 3 }], 'add b-roll')}>B-roll</Button>}
            {(a.kind === 'audio' || a.kind === 'video') && <Button variant="inline-link" onClick={() => onOps([{ op: 'add_clip', assetId: a.assetId, track: 'a1', start: 0, srcIn: 0, srcOut: a.durationSec ?? 30 }], 'add music')}>♪</Button>}
          </SpaceBetween>
        </div>
      ))}
    </SpaceBetween>
  );
}

function Versions({ projectId, version, onRestore }: Pick<Props, 'projectId' | 'version' | 'onRestore'>) {
  const { t } = useI18n();
  const [items, setItems] = useState<{ version: number; author: string; message: string; createdAt: number }[]>([]);
  useEffect(() => { api.versions(projectId).then(setItems).catch(() => undefined); }, [projectId, version]);
  return (
    <SpaceBetween size="xs">
      {items.map((v) => (
        <div key={v.version} style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: 8 }}>
          <Box variant="small"><b>v{v.version}</b> · {v.author === 'agent' ? 'AI' : v.author} · {v.message || '—'}<br /><span style={{ color: '#8d99a8' }}>{fmtDate(v.createdAt)}</span></Box>
          {v.version !== version && <Button variant="inline-link" onClick={() => onRestore(v.version)}>{t('restore')}</Button>}
        </div>
      ))}
    </SpaceBetween>
  );
}
