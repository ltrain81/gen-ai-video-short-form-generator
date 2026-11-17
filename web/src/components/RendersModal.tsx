import Box from '@cloudscape-design/components/box';
import Button from '@cloudscape-design/components/button';
import Link from '@cloudscape-design/components/link';
import Modal from '@cloudscape-design/components/modal';
import ProgressBar from '@cloudscape-design/components/progress-bar';
import SpaceBetween from '@cloudscape-design/components/space-between';
import StatusIndicator from '@cloudscape-design/components/status-indicator';
import { useCallback, useEffect, useState } from 'react';
import { api } from '../api/client';
import type { RenderInfo } from '../api/types';
import { useI18n } from '../i18n';
import { fmtDate, fmtTime } from '../util';

export default function RendersModal({ projectId, onClose, refreshKey }: { projectId: string; onClose: () => void; refreshKey: number }) {
  const { t } = useI18n();
  const [items, setItems] = useState<RenderInfo[]>([]);
  const load = useCallback(() => api.renders(projectId).then(setItems).catch(() => undefined), [projectId]);
  useEffect(() => { load(); }, [load, refreshKey]);
  const running = items.some((r) => r.status === 'queued' || r.status === 'running');
  useEffect(() => {
    if (!running) return;
    const h = window.setInterval(load, 4000);
    return () => window.clearInterval(h);
  }, [running, load]);
  return (
    <Modal visible onDismiss={onClose} header={t('renders')} size="large" footer={<Box float="right"><Button onClick={onClose}>{t('confirm')}</Button></Box>}>
      <SpaceBetween size="l">
        {!items.length && <Box color="text-body-secondary">{t('noRenders')}</Box>}
        {items.map((r) => (
          <div key={r.renderId} style={{ display: 'flex', gap: 16, alignItems: 'center' }}>
            {r.urls?.thumbnail ? <img src={r.urls.thumbnail} alt="" style={{ width: 72, borderRadius: 3 }} /> : <div className="thumb-placeholder" style={{ width: 72, aspectRatio: '9/16' }} />}
            <div style={{ flex: 1 }}>
              <Box fontWeight="bold">{r.quality === 'final' ? t('renderFinal') : t('renderPreview')} · v{r.version}</Box>
              <Box variant="small" color="text-body-secondary">{fmtDate(r.createdAt)}{r.durationSec ? ` · ${fmtTime(r.durationSec)} · ${r.width}×${r.height}` : ''}</Box>
              {(r.status === 'queued' || r.status === 'running') && (
                <ProgressBar value={Math.round((r.progress ?? 0) * 100)} additionalInfo={r.status === 'queued' ? t('st_queued') : r.message} />
              )}
              {r.status === 'failed' && <StatusIndicator type="error">{(r.error ?? '').slice(0, 200)}</StatusIndicator>}
              {r.status === 'done' && r.urls && (
                <SpaceBetween direction="horizontal" size="s">
                  <Link href={r.urls.video} external>MP4</Link>
                  <Link href={r.urls.srt} external>SRT</Link>
                  <Link href={r.urls.vtt} external>VTT</Link>
                </SpaceBetween>
              )}
            </div>
          </div>
        ))}
      </SpaceBetween>
    </Modal>
  );
}
