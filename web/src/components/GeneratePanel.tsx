import Alert from '@cloudscape-design/components/alert';
import Box from '@cloudscape-design/components/box';
import Button from '@cloudscape-design/components/button';
import FormField from '@cloudscape-design/components/form-field';
import Multiselect from '@cloudscape-design/components/multiselect';
import SegmentedControl from '@cloudscape-design/components/segmented-control';
import Select from '@cloudscape-design/components/select';
import SpaceBetween from '@cloudscape-design/components/space-between';
import StatusIndicator from '@cloudscape-design/components/status-indicator';
import Textarea from '@cloudscape-design/components/textarea';
import type { Timeline } from '@shorts/timeline';
import { useCallback, useEffect, useState } from 'react';
import { useApp } from '../App';
import { api } from '../api/client';
import type { Asset, GenJob } from '../api/types';
import { useI18n } from '../i18n';
import { fmtTime } from '../util';
import type { EditOp } from './TimelineStrip';

const KINDS = ['image', 'image_edit', 'video', 'speech', 'music'] as const;

/** Generation mode (deploy -c mode=full): estimate → confirm → job → archive asset → add to timeline. */
export default function GeneratePanel({ projectId, timeline, time, onOps }: { projectId: string; timeline: Timeline; time: number; onOps: (o: EditOp[], m: string) => void }) {
  const { t } = useI18n();
  const { notify } = useApp();
  const [kind, setKind] = useState<(typeof KINDS)[number]>('image');
  const [prompt, setPrompt] = useState('');
  const [seconds, setSeconds] = useState(5);
  const [provider, setProvider] = useState('voxcpm');
  const [refs, setRefs] = useState<{ value?: string; label?: string }[]>([]);
  const [images, setImages] = useState<Asset[]>([]);
  const [estimate, setEstimate] = useState<{ seconds: number; usd: number; coldStart: boolean } | null>(null);
  const [jobs, setJobs] = useState<GenJob[]>([]);
  const load = useCallback(() => api.generations(projectId).then(setJobs).catch(() => undefined), [projectId]);
  useEffect(() => { load(); api.assets({ kind: 'image' }).then(setImages).catch(() => undefined); }, [load]);
  useEffect(() => {
    if (!jobs.some((j) => j.status === 'running' || j.status === 'queued')) return;
    const h = window.setInterval(load, 10000);
    return () => window.clearInterval(h);
  }, [jobs, load]);
  useEffect(() => setEstimate(null), [kind, seconds, provider]);

  const params = () => ({ prompt, ...(kind === 'speech' ? { text: prompt, provider } : {}), seconds,
    aspect: timeline.settings.aspect, referenceAssetIds: refs.map((r) => r.value!) });
  const doEstimate = async () => {
    try { setEstimate(await api.generationEstimate(kind, { seconds, provider })); } catch (e) { notify('error', (e as Error).message); }
  };
  const doGenerate = async () => {
    try { await api.generate(projectId, { kind, ...params() }); setPrompt(''); setEstimate(null); load(); }
    catch (e) { notify('error', (e as Error).message); }
  };
  const addToTimeline = (j: GenJob) => {
    if (!j.assetId) return;
    if (j.kind === 'speech') onOps([{ op: 'add_clip', assetId: j.assetId, track: 'a2', start: time, srcIn: 0, srcOut: 60 }], 'add voiceover');
    else if (j.kind === 'music') onOps([{ op: 'add_clip', assetId: j.assetId, track: 'a1', start: 0, srcIn: 0, srcOut: 240 }], 'add music');
    else onOps([{ op: 'add_clip', assetId: j.assetId, track: 'o1', start: time, srcIn: 0, srcOut: 5, duration: 3 }], 'add generated');
  };

  return (
    <SpaceBetween size="m">
      <Alert type="info">{t('genHint')}</Alert>
      <SegmentedControl selectedId={kind} onChange={(e) => setKind(e.detail.selectedId as typeof kind)}
        options={KINDS.map((k) => ({ id: k, text: t(`gen_${k}` as never) }))} />
      <FormField label={kind === 'speech' ? t('narration') : t('prompt')}>
        <Textarea rows={3} value={prompt} onChange={(e) => setPrompt(e.detail.value)} />
      </FormField>
      {(kind === 'image_edit' || kind === 'video') && (
        <FormField label={t('references')} description={t('referencesHint')}>
          <Multiselect expandToViewport selectedOptions={refs} onChange={(e) => setRefs(e.detail.selectedOptions.slice(0, 3) as typeof refs)} filteringType="auto"
            options={images.map((a) => ({ value: a.assetId, label: a.displayTitle }))} />
        </FormField>
      )}
      {(kind === 'video' || kind === 'music') && (
        <FormField label={t('lengthSec')}>
          <Select expandToViewport selectedOption={{ value: String(seconds), label: `${seconds}s` }}
            options={(kind === 'video' ? [2, 3, 5] : [15, 30, 60, 120]).map((v) => ({ value: String(v), label: `${v}s` }))}
            onChange={(e) => setSeconds(Number(e.detail.selectedOption.value))} />
        </FormField>
      )}
      {kind === 'speech' && (
        <FormField label={t('provider')}>
          <Select expandToViewport selectedOption={{ value: provider, label: provider }} options={['voxcpm', 'polly', 'elevenlabs'].map((v) => ({ value: v, label: v }))}
            onChange={(e) => setProvider(e.detail.selectedOption.value!)} />
        </FormField>
      )}
      <SpaceBetween direction="horizontal" size="xs" alignItems="center">
        <Button disabled={!prompt.trim()} onClick={doEstimate}>{t('estimate')}</Button>
        {estimate && <Box variant="small">≈ {fmtTime(estimate.seconds)} · ${estimate.usd}{estimate.coldStart ? ` · ${t('coldStart')}` : ''}</Box>}
        <Button variant="primary" disabled={!estimate || !prompt.trim()} onClick={doGenerate}>{t('generate')}</Button>
      </SpaceBetween>
      {jobs.length > 0 && (
        <SpaceBetween size="xs">
          <Box variant="awsui-key-label">{t('genJobs')}</Box>
          {jobs.map((j) => (
            <div key={j.jobId} style={{ display: 'flex', justifyContent: 'space-between', gap: 8, alignItems: 'center' }}>
              <Box variant="small">{t(`gen_${j.kind}` as never)} · {String(j.params?.prompt ?? j.params?.text ?? '').slice(0, 40)}</Box>
              {j.status === 'done' ? <Button variant="inline-link" onClick={() => addToTimeline(j)}>{t('addAtPlayhead')}</Button>
                : j.status === 'failed' ? <StatusIndicator type="error">{(j.error ?? '').slice(0, 60)}</StatusIndicator>
                  : <StatusIndicator type="loading">{t('generating')}</StatusIndicator>}
            </div>
          ))}
        </SpaceBetween>
      )}
    </SpaceBetween>
  );
}
