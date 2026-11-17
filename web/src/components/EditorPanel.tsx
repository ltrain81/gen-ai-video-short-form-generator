import Box from '@cloudscape-design/components/box';
import Button from '@cloudscape-design/components/button';
import ColumnLayout from '@cloudscape-design/components/column-layout';
import FormField from '@cloudscape-design/components/form-field';
import Input from '@cloudscape-design/components/input';
import SegmentedControl from '@cloudscape-design/components/segmented-control';
import Select from '@cloudscape-design/components/select';
import SpaceBetween from '@cloudscape-design/components/space-between';
import Tabs from '@cloudscape-design/components/tabs';
import Textarea from '@cloudscape-design/components/textarea';
import * as awsui from '@cloudscape-design/design-tokens';
import type { ClipItem, Item, TextItem, Timeline } from '@shorts/timeline';
import { useEffect, useState } from 'react';
import { api } from '../api/client';
import type { Asset, BrandKit } from '../api/types';
import { useI18n } from '../i18n';
import { fmtDate, fmtTime } from '../util';
import type { EditOp } from './TimelineStrip';
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

const LAYOUTS = ['crop', 'split', 'fit_blur', 'letterbox'] as const;
const TRANSITIONS = ['cut', 'fade', 'dissolve', 'dipToBlack', 'slideLeft', 'zoom'];
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
      <FormField label={t('aspect')}>
        <SegmentedControl selectedId={timeline.settings.aspect} options={['9:16', '1:1', '4:5', '16:9'].map((a) => ({ id: a, text: a }))}
          onChange={(e) => onOps([{ op: 'settings', set: { aspect: e.detail.selectedId } }], 'aspect')} />
      </FormField>
      <FormField label={t('captions')}>
        <SpaceBetween direction="horizontal" size="xs">
          {CAPTION_PRESETS.map((p) => <Button key={p} onClick={() => onOps([{ op: 'captions', preset: p }], `captions ${p}`)}>{p}</Button>)}
          <Button iconName="remove" onClick={() => onOps([{ op: 'clear_track', track: 'c1' }], 'remove captions')}>{t('remove')}</Button>
        </SpaceBetween>
      </FormField>
      <MetadataEditor timeline={timeline} time={time} onOps={onOps} projectId={projectId} />
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
      {selected.type === 'clip' && <ClipInspector c={selected} set={set} onOps={onOps} />}
      {selected.type === 'text' && <TextInspector x={selected} set={set} />}
      {selected.type === 'caption' && (
        <FormField label={t('captions')}>
          <Select selectedOption={{ value: selected.preset, label: selected.preset }} options={CAPTION_PRESETS.map((p) => ({ value: p, label: p }))}
            onChange={(e) => set({ preset: e.detail.selectedOption.value })} />
        </FormField>
      )}
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

function ClipInspector({ c, set, onOps }: { c: ClipItem; set: (s: Record<string, unknown>, m?: string) => void; onOps: Props['onOps'] }) {
  const { t } = useI18n();
  const r = (c.reframe ?? {}) as { layout?: string; path?: { t: number; x: number; y: number; scale?: number }[] };
  const p0 = r.path?.[0] ?? { x: c.transform?.x ?? 0.5, y: c.transform?.y ?? 0.5, scale: c.transform?.scale ?? 1 };
  const [x, setX] = useState(String(p0.x));
  const [scale, setScale] = useState(String(p0.scale ?? 1));
  const reframe = (layout: string, fx = num(x, 0.5), sc = num(scale, 1)) =>
    set({ reframe: { mode: 'manual', layout, path: [{ t: c.srcIn, x: Math.min(1, Math.max(0, fx)), y: p0.y, scale: Math.min(2, Math.max(1, sc)) }] } }, 'reframe');
  return (
    <SpaceBetween size="m">
      {c.mediaKind !== 'image' && (
        <ColumnLayout columns={3}>
          <FormField label="In (s)"><CommitInput type="number" value={String(c.srcIn)} onCommit={(v) => onOps([{ op: 'trim', itemId: c.id, srcIn: num(v, c.srcIn) }], 'trim')} /></FormField>
          <FormField label="Out (s)"><CommitInput type="number" value={String(c.srcOut)} onCommit={(v) => onOps([{ op: 'trim', itemId: c.id, srcOut: num(v, c.srcOut) }], 'trim')} /></FormField>
          <FormField label={t('speed')}>
            <Select selectedOption={{ value: String(c.speed ?? 1), label: `${c.speed ?? 1}×` }}
              options={['0.5', '0.75', '1', '1.25', '1.5', '2', '3'].map((v) => ({ value: v, label: `${v}×` }))}
              onChange={(e) => set({ speed: Number(e.detail.selectedOption.value) }, 'speed')} />
          </FormField>
        </ColumnLayout>
      )}
      {c.mediaKind !== 'audio' && (
        <FormField label={t('layout')} description={t('layoutHint')}>
          <SpaceBetween size="xs">
            <SegmentedControl selectedId={r.layout ?? 'crop'} options={LAYOUTS.map((l) => ({ id: l, text: t(`layout_${l}` as never) }))}
              onChange={(e) => reframe(e.detail.selectedId)} />
            <ColumnLayout columns={2}>
              <FormField label={t('focusX')}><Input type="number" value={x} onChange={(e) => setX(e.detail.value)} onBlur={() => reframe(r.layout ?? 'crop')} /></FormField>
              <FormField label={t('zoom')}><Input type="number" value={scale} onChange={(e) => setScale(e.detail.value)} onBlur={() => reframe(r.layout ?? 'crop')} /></FormField>
            </ColumnLayout>
          </SpaceBetween>
        </FormField>
      )}
      <ColumnLayout columns={3}>
        <FormField label={t('volume')}>
          <Select selectedOption={{ value: String(c.volume ?? 1), label: `${Math.round((c.volume ?? 1) * 100)}%` }}
            options={['0', '0.25', '0.5', '0.75', '1', '1.5'].map((v) => ({ value: v, label: `${Math.round(Number(v) * 100)}%` }))}
            onChange={(e) => set({ volume: Number(e.detail.selectedOption.value) }, 'volume')} />
        </FormField>
        <FormField label={t('transitionIn')}>
          <Select selectedOption={{ value: c.transitionIn?.type ?? 'cut', label: c.transitionIn?.type ?? 'cut' }}
            options={TRANSITIONS.map((v) => ({ value: v, label: v }))}
            onChange={(e) => set({ transitionIn: e.detail.selectedOption.value === 'cut' ? null : { type: e.detail.selectedOption.value, duration: 0.4 } }, 'transition')} />
        </FormField>
        <FormField label={t('fade')}>
          <Select selectedOption={{ value: String(c.fadeOut ?? 0), label: `${c.fadeOut ?? 0}s` }}
            options={['0', '0.3', '0.6', '1'].map((v) => ({ value: v, label: `${v}s` }))}
            onChange={(e) => set({ fadeIn: Number(e.detail.selectedOption.value), fadeOut: Number(e.detail.selectedOption.value) }, 'fade')} />
        </FormField>
      </ColumnLayout>
    </SpaceBetween>
  );
}

function TextInspector({ x, set }: { x: TextItem; set: (s: Record<string, unknown>, m?: string) => void }) {
  const { t } = useI18n();
  const [text, setText] = useState(x.text);
  useEffect(() => setText(x.text), [x.id, x.text]);
  return (
    <SpaceBetween size="m">
      <FormField label={t('text')}><Textarea value={text} onChange={(e) => setText(e.detail.value)} onBlur={() => text !== x.text && set({ text }, 'text')} rows={3} /></FormField>
      <ColumnLayout columns={3}>
        <FormField label={t('size')}>
          <Select selectedOption={{ value: String(x.style.size), label: `${Math.round((x.style.size ?? 0.05) * 1000) / 10}%` }}
            options={['0.035', '0.045', '0.055', '0.07', '0.09'].map((v) => ({ value: v, label: `${Number(v) * 100}%` }))}
            onChange={(e) => set({ style: { size: Number(e.detail.selectedOption.value) } }, 'text size')} />
        </FormField>
        <FormField label={t('color')}><CommitInput value={x.style.color ?? '#FFFFFF'} onCommit={(v) => set({ style: { color: v } }, 'text color')} /></FormField>
        <FormField label={t('positionY')}>
          <Select selectedOption={{ value: String(x.position.y), label: `${Math.round(x.position.y * 100)}%` }}
            options={['0.12', '0.2', '0.35', '0.5', '0.65', '0.8'].map((v) => ({ value: v, label: `${Math.round(Number(v) * 100)}%` }))}
            onChange={(e) => set({ position: { ...x.position, y: Number(e.detail.selectedOption.value) } }, 'text position')} />
        </FormField>
      </ColumnLayout>
    </SpaceBetween>
  );
}

function AddMedia({ timeline, time, onOps, titles }: Pick<Props, 'timeline' | 'time' | 'onOps' | 'titles'>) {
  const { t } = useI18n();
  const [assets, setAssets] = useState<Asset[]>([]);
  const [kits, setKits] = useState<BrandKit[]>([]);
  const [kit, setKit] = useState<string | null>(timeline.settings.brandKitId ?? null);
  const [text, setText] = useState('');
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
            <Select selectedOption={kit ? { value: kit, label: kits.find((k) => k.brandKitId === kit)?.name } : null} placeholder={t('brandKit')}
              options={kits.map((k) => ({ value: k.brandKitId, label: k.name }))} onChange={(e) => setKit(e.detail.selectedOption.value ?? null)} />
            <Button disabled={!kit} onClick={() => onOps([{ op: 'brand', brandKitId: kit }], 'brand kit')}>{t('apply')}</Button>
          </SpaceBetween>
        </FormField>
      )}
      <Box variant="awsui-key-label">{t('fromArchive')}</Box>
      {assets.map((a) => (
        <div key={a.assetId} style={{ display: 'flex', gap: 10, alignItems: 'center', borderBottom: `1px solid ${awsui.colorBorderDividerDefault}`, paddingBottom: 8 }}>
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
