import Badge from '@cloudscape-design/components/badge';
import Box from '@cloudscape-design/components/box';
import Button from '@cloudscape-design/components/button';
import Cards from '@cloudscape-design/components/cards';
import FileUpload from '@cloudscape-design/components/file-upload';
import Header from '@cloudscape-design/components/header';
import Modal from '@cloudscape-design/components/modal';
import SegmentedControl from '@cloudscape-design/components/segmented-control';
import SpaceBetween from '@cloudscape-design/components/space-between';
import TextFilter from '@cloudscape-design/components/text-filter';
import { useCallback, useEffect, useMemo, useState } from 'react';
import { useApp } from '../App';
import { api } from '../api/client';
import type { Asset } from '../api/types';
import { uploadFile } from '../api/upload';
import { useI18n } from '../i18n';
import { AssetStatus, Thumb } from '../pages/ArchivePage';
import { fmtTime, mergeOrdered } from '../util';

const KINDS = ['all', 'video', 'image', 'audio', 'document'] as const;

interface Props {
  visible: boolean;
  onDismiss: () => void;
  onConfirm: (assets: Asset[]) => void;
  initial?: Asset[];
  /** Restrict which kinds can be picked (e.g. the editor's main track takes video + image). */
  kinds?: string[];
  confirmText?: string;
  title?: string;
}

/** Multi-select from the archive: local filter, AI (semantic) search, kind filter, inline upload. */
export default function AssetPicker({ visible, onDismiss, onConfirm, initial = [], kinds, confirmText, title }: Props) {
  const { t } = useI18n();
  const { notify } = useApp();
  const [assets, setAssets] = useState<Asset[]>([]);
  const [loading, setLoading] = useState(false);
  const [picked, setPicked] = useState<Asset[]>(initial);
  const [kind, setKind] = useState('all');
  const [filter, setFilter] = useState('');
  const [match, setMatch] = useState<string[] | null>(null);
  const [searching, setSearching] = useState(false);
  const [uploading, setUploading] = useState(0);

  const load = useCallback(async () => {
    setLoading(true);
    try { setAssets(await api.assets()); } catch (e) { notify('error', (e as Error).message); } finally { setLoading(false); }
  }, [notify]);
  useEffect(() => { if (visible) { setPicked(initial); load(); } }, [visible]); // eslint-disable-line react-hooks/exhaustive-deps
  // Refresh while fresh uploads are still being analyzed.
  const pending = assets.some((a) => ['uploading', 'queued', 'processing'].includes(a.status));
  useEffect(() => {
    if (!visible || !pending) return undefined;
    const h = window.setInterval(load, 5000);
    return () => window.clearInterval(h);
  }, [visible, pending, load]);

  const allowed = (a: Asset) => !kinds || kinds.includes(a.kind ?? 'other');
  const items = useMemo(() => {
    const q = filter.trim().toLowerCase();
    let xs = assets.filter((a) => allowed(a) && (kind === 'all' || a.kind === kind));
    if (match) {
      const rank = new Map(match.map((id, i) => [id, i]));
      xs = xs.filter((a) => rank.has(a.assetId)).sort((a, b) => rank.get(a.assetId)! - rank.get(b.assetId)!);
    } else if (q) {
      xs = xs.filter((a) => [a.displayTitle, a.filename, a.aiSummary, ...(a.tags ?? [])].some((s) => s?.toLowerCase().includes(q)));
    }
    return xs;
  }, [assets, kind, filter, match, kinds]); // eslint-disable-line react-hooks/exhaustive-deps
  const selectable = items.filter((a) => a.status === 'ready');

  const aiSearch = async () => {
    if (!filter.trim()) { setMatch(null); return; }
    setSearching(true);
    try {
      const r = await api.search({ text: filter.trim(), kinds: kind === 'all' ? kinds : [kind] });
      setMatch(r.map((x) => x.asset.assetId));
    } catch (e) { notify('error', (e as Error).message); } finally { setSearching(false); }
  };

  const onFiles = async (files: File[]) => {
    setUploading((n) => n + files.length);
    await Promise.all(files.map(async (f) => {
      try { await uploadFile(f, () => undefined); } catch (e) { notify('error', `${f.name}: ${(e as Error).message}`); }
      finally { setUploading((n) => n - 1); }
    }));
    load();
  };

  return (
    <Modal visible={visible} onDismiss={onDismiss} size="max" header={title ?? t('pickAssets')}
      footer={
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: 12, flexWrap: 'wrap' }}>
          <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap', minWidth: 0, flex: 1 }}>
            {picked.length === 0 ? <Box color="text-body-secondary">{t('pickHint')}</Box>
              : picked.map((a, i) => <Badge key={a.assetId} color="blue">{i + 1}. {a.displayTitle.slice(0, 28)}</Badge>)}
          </div>
          <SpaceBetween direction="horizontal" size="xs">
            {picked.length > 0 && <Button variant="link" onClick={() => setPicked([])}>{t('clearSelection')}</Button>}
            <Button variant="link" onClick={onDismiss}>{t('cancel')}</Button>
            <Button variant="primary" disabled={!picked.length} onClick={() => onConfirm(picked)}>
              {(confirmText ?? t('useSelected'))} ({picked.length})
            </Button>
          </SpaceBetween>
        </div>
      }>
      <SpaceBetween size="m">
        <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', alignItems: 'center' }}>
          <div style={{ flex: '1 1 320px' }}>
            <TextFilter filteringText={filter} filteringPlaceholder={t('pickFilter')}
              onChange={(e) => { setFilter(e.detail.filteringText); setMatch(null); }}
              countText={`${items.length}`} />
          </div>
          <Button iconName="search-gen-ai" loading={searching} disabled={!filter.trim()} onClick={aiSearch}>{t('aiSearch')}</Button>
          {match && <Button variant="link" onClick={() => setMatch(null)}>{t('clear')}</Button>}
          <SegmentedControl selectedId={kind} onChange={(e) => setKind(e.detail.selectedId)}
            options={KINDS.filter((k) => k === 'all' || !kinds || kinds.includes(k)).map((k) => ({ id: k, text: t(k as never) }))} />
          <FileUpload multiple value={[]} onChange={(e) => onFiles(e.detail.value)} showFileThumbnail={false}
            i18nStrings={{ uploadButtonText: () => uploading ? `${t('upload')} (${uploading})` : t('upload'), removeFileAriaLabel: () => t('remove') }} />
        </div>
        <Cards items={items} loading={loading && !assets.length} trackBy="assetId" selectionType="multi"
          selectedItems={picked} isItemDisabled={(a) => a.status !== 'ready'}
          onSelectionChange={(e) => setPicked(mergeOrdered(picked, e.detail.selectedItems))}
          cardsPerRow={[{ cards: 2 }, { minWidth: 700, cards: 3 }, { minWidth: 1000, cards: 4 }, { minWidth: 1400, cards: 6 }]}
          header={<Header variant="h3" counter={`(${picked.length}/${selectable.length})`}
            actions={<Button variant="inline-link" disabled={!selectable.length}
              onClick={() => setPicked(mergeOrdered(picked, [...picked, ...selectable]))}>{t('selectAllVisible')}</Button>}>
            {match ? t('matchedMoments') : t('navArchive')}</Header>}
          empty={<Box textAlign="center" color="text-body-secondary" padding="l">{match ? t('noResults') : t('noAssets')}</Box>}
          cardDefinition={{
            header: (a) => {
              const n = picked.findIndex((x) => x.assetId === a.assetId);
              return <Box fontWeight="bold" variant="small">{n >= 0 ? `${n + 1}. ` : ''}{a.displayTitle}</Box>;
            },
            sections: [
              { id: 't', content: (a) => <Thumb asset={a} /> },
              { id: 'm', content: (a) => (
                <SpaceBetween direction="horizontal" size="xs" alignItems="center">
                  {a.status !== 'ready' && <AssetStatus status={a.status} />}
                  <Box variant="small" color="text-body-secondary">{t((a.kind ?? 'other') as never)}{a.durationSec ? ` · ${fmtTime(a.durationSec)}` : ''}</Box>
                </SpaceBetween>) },
            ],
          }} />
      </SpaceBetween>
    </Modal>
  );
}
