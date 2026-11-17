import Box from '@cloudscape-design/components/box';
import PromptInput from '@cloudscape-design/components/prompt-input';
import SpaceBetween from '@cloudscape-design/components/space-between';
import { useState } from 'react';
import { useI18n } from '../i18n';

/** Phase 3 replaces this with the chat-first studio (chat center → slides right, live preview left). */
export default function StudioPage() {
  const { t } = useI18n();
  const [value, setValue] = useState('');
  return (
    <div className="studio-center">
      <SpaceBetween size="xs" alignItems="center">
        <Box variant="h1">{t('studioWelcome')}</Box>
        <Box color="text-body-secondary">{t('studioHint')}</Box>
      </SpaceBetween>
      <div className="prompt">
        <PromptInput value={value} onChange={(e) => setValue(e.detail.value)} actionButtonIconName="send" disabled
          placeholder={t('comingSoon')} actionButtonAriaLabel="send" />
      </div>
    </div>
  );
}
