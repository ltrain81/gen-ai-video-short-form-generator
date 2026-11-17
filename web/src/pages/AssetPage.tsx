import Badge from '@cloudscape-design/components/badge';
import Box from '@cloudscape-design/components/box';
import Button from '@cloudscape-design/components/button';
import ColumnLayout from '@cloudscape-design/components/column-layout';
import Container from '@cloudscape-design/components/container';
import ContentLayout from '@cloudscape-design/components/content-layout';
import Grid from '@cloudscape-design/components/grid';
import Header from '@cloudscape-design/components/header';
import KeyValuePairs from '@cloudscape-design/components/key-value-pairs';
import SpaceBetween from '@cloudscape-design/components/space-between';
import Spinner from '@cloudscape-design/components/spinner';
import StatusIndicator from '@cloudscape-design/components/status-indicator';
import Tabs from '@cloudscape-design/components/tabs';
import TokenGroup from '@cloudscape-design/components/token-group';
import Input from '@cloudscape-design/components/input';
import { useCallback, useEffect, useRef, useState } from 'react';
import { useNavigate, useParams, useSearchParams } from 'react-router-dom';
import { useApp } from '../App';
import { api } from '../api/client';
import type { Analysis, Asset, AssetMedia, Scene } from '../api/types';
import ShareModal from '../components/ShareModal';
import { useI18n } from '../i18n';
import { fmtBytes, fmtDate, fmtTime } from '../util';
import { AssetStatus } from './ArchivePage';

export default function AssetPage() {
  const { assetId = '' } = useParams();
  const [params] = useSearchParams();
  const { t } = useI18n();
  const { notify } = useApp();
  const navigate = useNavigate();
  const [asset, setAsset] = useState<Asset | null>(null);
  const [media, setMedia] = useState<AssetMedia | null>(null);
  const [analysis, setAnalysis] = useState<Analysis | null>(null);
  const [shareOpen, setShareOpen] = useState(false);
  const [tagInput, setTagInput] = useState('');
  const [personInput, setPersonInput] = useState('');
  const videoRef = useRef<HTMLVideoElement>(null);

  const load = useCallback(async () => {
    try {
      const a = await api.asset(assetId);
      setAsset(a);
      if (a.status === 'ready') {
        const [m, an] = await Promise.all([api.media(assetId), api.analysis(assetId).catch(() => null)]);
        setMedia(m);
        setAnalysis(an);
      }
    } catch (e) {
      notify('error', (e as Error).message);
    }
  }, [assetId, notify]);

  useEffect(() => { load(); }, [load]);
  useEffect(() => {
    if (!asset || asset.status === 'ready' || asset.status === 'failed') return;
    const h = window.setInterval(load, 5000);
    return () => window.clearInterval(h);
  }, [asset, load]);

  const seek = (sec: number) => {
    const v = videoRef.current;
    if (v) { v.currentTime = sec; v.play().catch(() => undefined); }
  };
  useEffect(() => {
    const t0 = Number(params.get('t'));
    if (media && t0 && videoRef.current) videoRef.current.currentTime = t0;
  }, [media, params]);

  if (!asset) return <Box textAlign="center" padding="xxl"><Spinner size="large" /></Box>;

  const save = async (fields: Partial<Pick<Asset, 'tags' | 'people'>>) => {
    try { setAsset({ ...asset, ...(await api.updateAsset(asset.assetId, fields)) }); } catch (e) { notify('error', (e as Error).message); }
  };
  const canEdit = asset.role === 'owner' || asset.role === 'editor';

  const segList = (items: Scene[] | null | undefined, title: (s: Scene) => string | undefined) => (
    items?.length ? (
      <SpaceBetween size="xxs">
        {items.map((s, i) => (
          <div key={i} className="seg-row" onClick={() => seek(s.start_sec)}>
            <span className="seg-time">{fmtTime(s.start_sec)}–{fmtTime(s.end_sec)}</span>
            <span>
              {title(s) && <Box fontWeight="bold" display="inline">{title(s)} </Box>}
              {s.summary}
              {s.highlight_score != null && s.highlight_score >= 4 && <> <Badge color="green">★ {s.highlight_score}</Badge></>}
              {s.on_screen_text && <Box variant="small" color="text-body-secondary">“{s.on_screen_text}”</Box>}
            </span>
          </div>
        ))}
      </SpaceBetween>
    ) : <Box color="text-body-secondary">–</Box>
  );

  return (
    <ContentLayout
      header={
        <Header variant="h1" description={asset.filename}
          actions={
            <SpaceBetween direction="horizontal" size="xs">
              {asset.role === 'owner' && <Button iconName="share" onClick={() => setShareOpen(true)}>{t('share')}</Button>}
              {canEdit && <Button iconName="download" onClick={async () => window.open((await api.download(asset.assetId)).url)}>{t('download')}</Button>}
              {asset.role === 'owner' && <Button iconName="remove" onClick={async () => { await api.deleteAsset(asset.assetId); navigate('/archive'); }}>{t('delete')}</Button>}
            </SpaceBetween>
          }>
          {asset.displayTitle}
        </Header>
      }>
      <Grid gridDefinition={[{ colspan: { default: 12, m: 8 } }, { colspan: { default: 12, m: 4 } }]}>
        <SpaceBetween size="l">
          <Container>
            {media?.kind === 'video' && <video ref={videoRef} className="player" src={media.proxyUrl} controls preload="metadata" poster={media.thumbnailUrl} />}
            {media?.kind === 'audio' && <audio src={media.audioUrl} controls style={{ width: '100%' }} />}
            {media?.kind === 'image' && <img className="player" style={{ objectFit: 'contain' }} src={media.imageUrl} alt={asset.displayTitle} />}
            {media?.kind === 'document' && (
              <ColumnLayout columns={3}>
                {media.pages?.map((p) => <img key={p.index} src={p.url} alt={`page ${p.index + 1}`} style={{ width: '100%', borderRadius: 6 }} loading="lazy" />)}
              </ColumnLayout>
            )}
            {!media && (
              <Box textAlign="center" padding="xl">
                <SpaceBetween size="s" alignItems="center">
                  <AssetStatus status={asset.status} />
                  {asset.processing?.stages && (
                    <SpaceBetween direction="horizontal" size="xs">
                      {Object.entries(asset.processing.stages).sort().map(([k, v]) => (
                        <StatusIndicator key={k} type={v === 'done' ? 'success' : v === 'failed' ? 'error' : v === 'skipped' ? 'stopped' : 'loading'}>{k}</StatusIndicator>
                      ))}
                    </SpaceBetween>
                  )}
                  {(asset.error || asset.processing?.error) && <Box color="text-status-error">{asset.error || asset.processing?.error}</Box>}
                </SpaceBetween>
              </Box>
            )}
          </Container>
          {analysis && (
            <Container>
              <Tabs tabs={[
                { id: 'chapters', label: t('chapters'), content: segList(analysis.chapters, (s) => s.title) },
                { id: 'scenes', label: t('scenes'), content: segList(analysis.scenes, (s) => s.shot_type) },
                {
                  id: 'transcript', label: t('transcript'),
                  content: analysis.transcript?.sentences.length ? (
                    <SpaceBetween size="xxs">
                      {analysis.transcript.sentences.map((s, i) => (
                        <div key={i} className="seg-row" onClick={() => seek(s.s)}>
                          <span className="seg-time">{fmtTime(s.s)}</span>
                          <span>{s.spk && <Badge>{s.spk}</Badge>} {s.text}</span>
                        </div>
                      ))}
                    </SpaceBetween>
                  ) : <Box color="text-body-secondary">–</Box>,
                },
              ]} />
            </Container>
          )}
        </SpaceBetween>
        <SpaceBetween size="l">
          <Container header={<Header variant="h2">{t('summary')}</Header>}>
            <SpaceBetween size="m">
              <Box>{asset.analysisSummary?.summary ?? asset.aiSummary ?? '–'}</Box>
              {asset.analysisSummary?.topics?.length ? (
                <SpaceBetween direction="horizontal" size="xxs">{asset.analysisSummary.topics.map((x) => <Badge key={x} color="blue">{x}</Badge>)}</SpaceBetween>
              ) : null}
            </SpaceBetween>
          </Container>
          <Container header={<Header variant="h2">{t('details')}</Header>}>
            <KeyValuePairs columns={1} items={[
              { label: t('status'), value: <AssetStatus status={asset.status} /> },
              { label: t('kind'), value: t((asset.kind ?? 'other') as never) },
              { label: t('duration'), value: fmtTime(asset.durationSec) },
              { label: 'Size', value: `${fmtBytes(asset.sizeBytes)}${asset.width ? ` · ${asset.width}×${asset.height}` : ''}` },
              { label: t('created'), value: fmtDate(asset.createdAt) },
              { label: t('role'), value: asset.role },
            ]} />
          </Container>
          <Container header={<Header variant="h2">{t('tags')}</Header>}>
            <SpaceBetween size="s">
              <TokenGroup items={(asset.tags ?? []).map((x) => ({ label: x, dismissLabel: t('remove') }))}
                onDismiss={({ detail }) => canEdit && save({ tags: (asset.tags ?? []).filter((_, i) => i !== detail.itemIndex) })} readOnly={!canEdit} />
              {canEdit && <Input value={tagInput} placeholder="+ tag" onChange={(e) => setTagInput(e.detail.value)}
                onKeyDown={(e) => { if (e.detail.key === 'Enter' && tagInput.trim()) { save({ tags: [...(asset.tags ?? []), tagInput.trim()] }); setTagInput(''); } }} />}
              <Box variant="awsui-key-label">{t('people')}</Box>
              <TokenGroup items={(asset.people ?? []).map((x) => ({ label: x.name, dismissLabel: t('remove') }))}
                onDismiss={({ detail }) => canEdit && save({ people: (asset.people ?? []).filter((_, i) => i !== detail.itemIndex) })} readOnly={!canEdit} />
              {canEdit && <Input value={personInput} placeholder="+ person" onChange={(e) => setPersonInput(e.detail.value)}
                onKeyDown={(e) => { if (e.detail.key === 'Enter' && personInput.trim()) { save({ people: [...(asset.people ?? []), { name: personInput.trim() }] }); setPersonInput(''); } }} />}
            </SpaceBetween>
          </Container>
        </SpaceBetween>
      </Grid>
      {shareOpen && <ShareModal resource={{ type: 'asset', id: asset.assetId, name: asset.displayTitle }} onClose={() => setShareOpen(false)} />}
    </ContentLayout>
  );
}
