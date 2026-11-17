import Box from '@cloudscape-design/components/box';
import Cards from '@cloudscape-design/components/cards';
import ContentLayout from '@cloudscape-design/components/content-layout';
import Header from '@cloudscape-design/components/header';
import Link from '@cloudscape-design/components/link';
import StatusIndicator from '@cloudscape-design/components/status-indicator';
import { useCallback, useEffect, useState } from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import { useApp } from '../App';
import { api } from '../api/client';
import type { BatchInfo } from '../api/types';
import { useI18n } from '../i18n';
import { fmtTime } from '../util';

export default function BatchPage() {
  const { batchId = '' } = useParams();
  const { t } = useI18n();
  const { notify } = useApp();
  const navigate = useNavigate();
  const [b, setB] = useState<BatchInfo | null>(null);
  const load = useCallback(() => api.batch(batchId).then(setB).catch((e) => notify('error', e.message)), [batchId, notify]);
  useEffect(() => { load(); }, [load]);
  const running = b?.items?.some((i) => i.busy);
  useEffect(() => {
    if (!running) return;
    const h = window.setInterval(load, 6000);
    return () => window.clearInterval(h);
  }, [running, load]);
  return (
    <ContentLayout header={<Header variant="h1" description={t('batchDescription')}
      counter={b?.items ? `(${b.items.length})` : undefined}>{t('batch')}</Header>}>
      <Cards items={b?.items ?? []} loading={!b} trackBy="projectId"
        cardsPerRow={[{ cards: 1 }, { minWidth: 600, cards: 2 }, { minWidth: 1000, cards: 3 }]}
        cardDefinition={{
          header: (i) => <Link fontSize="heading-s" onFollow={(e) => { e.preventDefault(); navigate(`/studio/${i.projectId}`); }}>{i.title}</Link>,
          sections: [
            { id: 'range', header: t('sourceRange'), content: (i) => `${fmtTime(i.range[0])} – ${fmtTime(i.range[1])}` },
            { id: 'status', content: (i) => i.busy ? <StatusIndicator type="loading">{t('agentWorking')}</StatusIndicator>
              : i.version > 0 ? <StatusIndicator type="success">v{i.version}</StatusIndicator> : <StatusIndicator type="pending">{t('st_queued')}</StatusIndicator> },
          ],
        }}
        empty={<Box textAlign="center">—</Box>} />
    </ContentLayout>
  );
}
