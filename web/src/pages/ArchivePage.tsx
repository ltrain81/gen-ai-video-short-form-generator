import Badge from '@cloudscape-design/components/badge';
import Box from '@cloudscape-design/components/box';
import Button from '@cloudscape-design/components/button';
import Cards from '@cloudscape-design/components/cards';
import Container from '@cloudscape-design/components/container';
import ContentLayout from '@cloudscape-design/components/content-layout';
import FileUpload from '@cloudscape-design/components/file-upload';
import Header from '@cloudscape-design/components/header';
import Input from '@cloudscape-design/components/input';
import Link from '@cloudscape-design/components/link';
import ProgressBar from '@cloudscape-design/components/progress-bar';
import SegmentedControl from '@cloudscape-design/components/segmented-control';
import SpaceBetween from '@cloudscape-design/components/space-between';
import StatusIndicator, { StatusIndicatorProps } from '@cloudscape-design/components/status-indicator';
import TextContent from '@cloudscape-design/components/text-content';
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { useApp } from '../App';
import { api } from '../api/client';
import type { Asset, SearchResult } from '../api/types';
import { uploadFile, UploadProgress } from '../api/upload';
import { useI18n } from '../i18n';
import { fileToBase64, fmtBytes, fmtTime } from '../util';

const STATUS: Record<string, StatusIndicatorProps.Type> = {
  uploading: 'in-progress', queued: 'pending', processing: 'loading', ready: 'success', failed: 'error',
};

export function AssetStatus({ status }: { status: string }) {
  const { t } = useI18n();
  return <StatusIndicator type={STATUS[status] ?? 'info'}>{t(`st_${status}` as never) || status}</StatusIndicator>;
}

export function Thumb({ asset }: { asset: Asset }) {
  const { t } = useI18n();
  return asset.thumbnailUrl
    ? <img className="thumb" src={asset.thumbnailUrl} alt={asset.displayTitle} loading="lazy" />
    : <div className="thumb-placeholder">{t((asset.kind ?? 'other') as never)}</div>;
}

export default function ArchivePage() {
  const { t } = useI18n();
  const { notify } = useApp();
  const navigate = useNavigate();
  const [assets, setAssets] = useState<Asset[]>([]);
  const [loading, setLoading] = useState(true);
  const [kind, setKind] = useState('all');
  const [query, setQuery] = useState('');
  const [queryImage, setQueryImage] = useState<File[]>([]);
  const [results, setResults] = useState<SearchResult[] | null>(null);
  const [searching, setSearching] = useState(false);
  const [uploads, setUploads] = useState<Record<string, UploadProgress>>({});
  const pollRef = useRef<number>(undefined);

  const load = useCallback(async () => {
    try {
      setAssets(await api.assets(kind === 'all' ? {} : { kind }));
    } catch (e) {
      notify('error', (e as Error).message);
    } finally {
      setLoading(false);
    }
  }, [kind, notify]);

  useEffect(() => { setLoading(true); load(); }, [load]);

  // Poll while anything is still processing.
  const busy = useMemo(() => assets.some((a) => ['uploading', 'queued', 'processing'].includes(a.status)), [assets]);
  useEffect(() => {
    window.clearInterval(pollRef.current);
    if (busy) pollRef.current = window.setInterval(load, 5000);
    return () => window.clearInterval(pollRef.current);
  }, [busy, load]);

  const onFiles = async (files: File[]) => {
    await Promise.all(files.map(async (file) => {
      const key = `${file.name}-${file.size}-${file.lastModified}`;
      try {
        await uploadFile(file, (p) => setUploads((u) => ({ ...u, [key]: p })));
        load();
      } catch (e) {
        notify('error', `${file.name}: ${(e as Error).message}`);
      }
    }));
  };

  const runSearch = async () => {
    if (!query.trim() && !queryImage.length) { setResults(null); return; }
    setSearching(true);
    try {
      const imageBase64 = queryImage[0] ? await fileToBase64(queryImage[0]) : undefined;
      setResults(await api.search({ text: query.trim() || undefined, imageBase64, kinds: kind === 'all' ? undefined : [kind] }));
    } catch (e) {
      notify('error', (e as Error).message);
    } finally {
      setSearching(false);
    }
  };

  const activeUploads = Object.entries(uploads).filter(([, u]) => u.status !== 'done');

  return (
    <ContentLayout header={<Header variant="h1" counter={`(${assets.length})`}
      actions={<FileUpload multiple value={[]} onChange={(e) => onFiles(e.detail.value)}
        i18nStrings={{ uploadButtonText: () => t('upload'), dropzoneText: () => t('uploadHint'), removeFileAriaLabel: () => t('remove') }}
        showFileThumbnail={false} />}
      description={t('uploadHint')}>{t('navArchive')}</Header>}>
      <SpaceBetween size="l">
        <Container>
          <SpaceBetween size="s">
            <SpaceBetween direction="horizontal" size="xs" alignItems="center">
              <div style={{ minWidth: 'min(560px, 70vw)' }}>
                <Input type="search" value={query} placeholder={t('searchPlaceholder')}
                  onChange={(e) => setQuery(e.detail.value)} onKeyDown={(e) => e.detail.key === 'Enter' && runSearch()} />
              </div>
              <FileUpload value={queryImage} onChange={(e) => setQueryImage(e.detail.value)} accept="image/*"
                i18nStrings={{ uploadButtonText: () => t('searchByImage'), removeFileAriaLabel: () => t('remove') }} showFileThumbnail />
              <Button variant="primary" loading={searching} onClick={runSearch}>{t('search')}</Button>
              {results && <Button onClick={() => { setResults(null); setQuery(''); setQueryImage([]); }}>{t('clear')}</Button>}
            </SpaceBetween>
            <SegmentedControl selectedId={kind} onChange={(e) => setKind(e.detail.selectedId)}
              options={['all', 'video', 'image', 'audio', 'document'].map((k) => ({ id: k, text: t(k as never) }))} />
          </SpaceBetween>
        </Container>

        {activeUploads.length > 0 && (
          <Container header={<Header variant="h3">{t('upload')}</Header>}>
            <SpaceBetween size="s">
              {activeUploads.map(([k, u]) => (
                <ProgressBar key={k} label={u.file.name} value={Math.round((u.loaded / u.total) * 100)}
                  description={`${fmtBytes(u.loaded)} / ${fmtBytes(u.total)}`}
                  status={u.status === 'error' ? 'error' : 'in-progress'} additionalInfo={u.error} />
              ))}
            </SpaceBetween>
          </Container>
        )}

        {results ? (
          <Cards
            items={results}
            loading={searching}
            trackBy={(r) => r.asset.assetId}
            cardsPerRow={[{ cards: 1 }, { minWidth: 640, cards: 2 }, { minWidth: 1100, cards: 3 }]}
            header={<Header counter={`(${results.length})`}>{t('matchedMoments')}</Header>}
            empty={<Box textAlign="center" color="inherit">{t('noResults')}</Box>}
            cardDefinition={{
              header: (r) => <Link fontSize="heading-s" onFollow={() => navigate(`/archive/${r.asset.assetId}`)}>{r.asset.displayTitle}</Link>,
              sections: [
                { id: 'thumb', content: (r) => <Thumb asset={r.asset} /> },
                {
                  id: 'segments',
                  content: (r) => (
                    <SpaceBetween size="xxs">
                      {r.segments.map((s, i) => (
                        <div key={i} className="seg-row" onClick={() => navigate(`/archive/${r.asset.assetId}?t=${s.startSec}`)}>
                          <span className="seg-time">{s.page != null && r.asset.kind === 'document' ? `p.${s.page + 1}` : `${fmtTime(s.startSec)}–${fmtTime(s.endSec)}`}</span>
                          <span>
                            {(s.modalities ?? [s.modality]).map((m) => <Badge key={m}>{m}</Badge>)}{' '}
                            <Box variant="small" display="inline">{(s.score * 100).toFixed(0)}%</Box>
                            {s.text && <Box variant="small" color="text-body-secondary">{s.text.slice(0, 140)}</Box>}
                          </span>
                        </div>
                      ))}
                    </SpaceBetween>
                  ),
                },
              ],
            }}
          />
        ) : (
          <Cards
            items={assets}
            loading={loading}
            trackBy="assetId"
            cardsPerRow={[{ cards: 1 }, { minWidth: 500, cards: 2 }, { minWidth: 900, cards: 3 }, { minWidth: 1300, cards: 4 }]}
            empty={<Box textAlign="center" color="inherit" padding="xxl"><TextContent><p>{t('noAssets')}</p></TextContent></Box>}
            cardDefinition={{
              header: (a) => <Link fontSize="heading-s" onFollow={() => navigate(`/archive/${a.assetId}`)}>{a.displayTitle}</Link>,
              sections: [
                { id: 'thumb', content: (a) => <div style={{ cursor: 'pointer' }} onClick={() => navigate(`/archive/${a.assetId}`)}><Thumb asset={a} /></div> },
                {
                  id: 'meta',
                  content: (a) => (
                    <SpaceBetween direction="horizontal" size="s" alignItems="center">
                      <AssetStatus status={a.status} />
                      <Badge>{t((a.kind ?? 'other') as never)}</Badge>
                      {a.durationSec ? <Box variant="small">{fmtTime(a.durationSec)}</Box> : null}
                      {a.pageCount ? <Box variant="small">{a.pageCount} {t('pages')}</Box> : null}
                      <Box variant="small" color="text-body-secondary">{fmtBytes(a.sizeBytes)}</Box>
                    </SpaceBetween>
                  ),
                },
                { id: 'summary', content: (a) => a.aiSummary ? <Box variant="small" color="text-body-secondary">{a.aiSummary.slice(0, 160)}{a.aiSummary.length > 160 ? '…' : ''}</Box> : null },
              ],
            }}
          />
        )}
      </SpaceBetween>
    </ContentLayout>
  );
}
