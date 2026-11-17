import Box from '@cloudscape-design/components/box';
import Button from '@cloudscape-design/components/button';
import Cards from '@cloudscape-design/components/cards';
import Header from '@cloudscape-design/components/header';
import Link from '@cloudscape-design/components/link';
import PromptInput from '@cloudscape-design/components/prompt-input';
import SegmentedControl from '@cloudscape-design/components/segmented-control';
import SpaceBetween from '@cloudscape-design/components/space-between';
import TokenGroup from '@cloudscape-design/components/token-group';
import { useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { useApp } from '../App';
import { api } from '../api/client';
import type { Asset, ProjectSummary } from '../api/types';
import { useI18n } from '../i18n';
import { fmtDate, fmtTime } from '../util';
import { Thumb } from './ArchivePage';

/** Chat-first start: one prompt + optional attached archive assets → new project, then the editor view. */
export default function StudioPage() {
  const { t } = useI18n();
  const { notify } = useApp();
  const navigate = useNavigate();
  const [value, setValue] = useState('');
  const [aspect, setAspect] = useState('9:16');
  const [assets, setAssets] = useState<Asset[]>([]);
  const [picked, setPicked] = useState<Asset[]>([]);
  const [showPicker, setShowPicker] = useState(false);
  const [recent, setRecent] = useState<ProjectSummary[]>([]);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    api.assets().then((a) => setAssets(a.filter((x) => x.status === 'ready'))).catch(() => undefined);
    api.projects().then((p) => setRecent(p.slice(0, 6))).catch(() => undefined);
  }, []);

  const start = async () => {
    const text = value.trim();
    if (!text) return;
    setBusy(true);
    try {
      const { projectId } = await api.createProject({
        title: text.slice(0, 60), synopsis: text, aspect, assetIds: picked.map((a) => a.assetId),
      });
      navigate(`/studio/${projectId}`, { state: { firstMessage: text } });
    } catch (e) {
      notify('error', (e as Error).message);
      setBusy(false);
    }
  };

  return (
    <div className="studio-center">
      <SpaceBetween size="xs" alignItems="center">
        <Box variant="h1">{t('studioWelcome')}</Box>
        <Box color="text-body-secondary">{t('studioHint')}</Box>
      </SpaceBetween>
      <div className="prompt">
        <SpaceBetween size="s">
          <PromptInput value={value} onChange={(e) => setValue(e.detail.value)} onAction={start} minRows={3} maxRows={10}
            actionButtonIconName="send" actionButtonAriaLabel="send" disableActionButton={busy || !value.trim()}
            placeholder={t('studioPlaceholder')}
            secondaryActions={
              <SpaceBetween direction="horizontal" size="xs" alignItems="center">
                <Button variant="icon" iconName="upload" ariaLabel={t('attachFromArchive')} onClick={() => setShowPicker(!showPicker)} />
                <SegmentedControl selectedId={aspect} onChange={(e) => setAspect(e.detail.selectedId)}
                  options={['9:16', '1:1', '4:5', '16:9'].map((a) => ({ id: a, text: a }))} />
              </SpaceBetween>
            }
            secondaryContent={picked.length ? (
              <TokenGroup items={picked.map((a) => ({ label: a.displayTitle, dismissLabel: t('remove') }))}
                onDismiss={({ detail }) => setPicked(picked.filter((_, i) => i !== detail.itemIndex))} />
            ) : undefined}
          />
          {showPicker && (
            <Cards items={assets} selectionType="multi" selectedItems={picked} onSelectionChange={(e) => setPicked(e.detail.selectedItems)}
              trackBy="assetId" cardsPerRow={[{ cards: 2 }, { minWidth: 640, cards: 4 }]}
              header={<Header variant="h3" counter={`(${picked.length}/${assets.length})`}>{t('attachFromArchive')}</Header>}
              empty={<Box textAlign="center">{t('noAssets')}</Box>}
              cardDefinition={{
                header: (a) => <Box fontWeight="bold" variant="small">{a.displayTitle}</Box>,
                sections: [{ id: 't', content: (a) => <Thumb asset={a} /> },
                  { id: 'm', content: (a) => <Box variant="small" color="text-body-secondary">{t((a.kind ?? 'other') as never)} {a.durationSec ? `· ${fmtTime(a.durationSec)}` : ''}</Box> }],
              }} />
          )}
          {recent.length > 0 && (
            <SpaceBetween size="xxs">
              <Box variant="awsui-key-label">{t('recentProjects')}</Box>
              {recent.map((p) => (
                <Box key={p.projectId} variant="small">
                  <Link onFollow={(e) => { e.preventDefault(); navigate(`/studio/${p.projectId}`); }}>{p.title}</Link>
                  {' '}<span style={{ color: '#8d99a8' }}>· {p.aspect} · {fmtDate(p.updatedAt)}</span>
                </Box>
              ))}
            </SpaceBetween>
          )}
        </SpaceBetween>
      </div>
    </div>
  );
}
