import Avatar from '@cloudscape-design/chat-components/avatar';
import ChatBubble from '@cloudscape-design/chat-components/chat-bubble';
import LoadingBar from '@cloudscape-design/chat-components/loading-bar';
import Badge from '@cloudscape-design/components/badge';
import Box from '@cloudscape-design/components/box';
import Button from '@cloudscape-design/components/button';
import PromptInput from '@cloudscape-design/components/prompt-input';
import SpaceBetween from '@cloudscape-design/components/space-between';
import StatusIndicator from '@cloudscape-design/components/status-indicator';
import { ReactNode, useEffect, useRef, useState } from 'react';
import type { AgentEvent } from '../api/realtime';
import { useI18n } from '../i18n';
import { fmtTime } from '../util';

export interface ChatEntry {
  id: string;
  role: 'user' | 'assistant';
  text: string;
  tools?: { name: string; status: string }[];
  question?: Extract<AgentEvent, { type: 'question' }>;
  pending?: boolean;
  error?: string;
}

interface Props {
  entries: ChatEntry[];
  busy: boolean;
  selectionLabel?: string;
  onClearSelection?: () => void;
  onSend: (text: string) => void;
  onAnswer: (questionId: string, value: unknown, display: string) => void;
  thumbs: Record<string, string | undefined>;
  titles: Record<string, string>;
  compact?: boolean;
  header?: ReactNode;
}

const TOOL_LABELS: Record<string, { ko: string; en: string }> = {
  get_context: { ko: '프로젝트 확인', en: 'Reading project' },
  search_archive: { ko: '아카이브 검색', en: 'Searching archive' },
  get_asset_overview: { ko: '영상 파악', en: 'Understanding video' },
  get_scenes: { ko: '장면 확인', en: 'Reading scenes' },
  read_transcript: { ko: '대사 읽기', en: 'Reading transcript' },
  look: { ko: '화면 보기', en: 'Looking at frames' },
  ask_video: { ko: '영상 질의', en: 'Asking the video model' },
  snap: { ko: '컷 지점 맞추기', en: 'Snapping cut points' },
  analyze_framing: { ko: '세로 프레이밍 분석', en: 'Analyzing framing' },
  get_timeline: { ko: '타임라인 확인', en: 'Reading timeline' },
  edit_timeline: { ko: '편집 적용', en: 'Editing timeline' },
  ask_user: { ko: '질문', en: 'Asking you' },
  remember: { ko: '선호 저장', en: 'Saving preference' },
};

export default function ChatPanel({ entries, busy, selectionLabel, onClearSelection, onSend, onAnswer, thumbs, titles, compact, header }: Props) {
  const { t, lang } = useI18n();
  const [value, setValue] = useState('');
  const endRef = useRef<HTMLDivElement>(null);
  useEffect(() => { endRef.current?.scrollIntoView({ behavior: 'smooth', block: 'end' }); }, [entries]);

  const send = () => {
    const v = value.trim();
    if (!v || busy) return;
    onSend(v);
    setValue('');
  };

  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: compact ? 'auto' : 'calc(100vh - 140px)', gap: 12 }}>
      {header}
      <div style={{ flex: 1, overflowY: 'auto', paddingRight: 4 }}>
        <SpaceBetween size="m">
          {entries.map((e) => (
            <ChatBubble key={e.id} type={e.role === 'user' ? 'outgoing' : 'incoming'} ariaLabel={e.role}
              avatar={e.role === 'user' ? <Avatar ariaLabel="you" tooltipText="you" initials="U" />
                : <Avatar color="gen-ai" iconName="gen-ai" ariaLabel="AI" tooltipText="AI" loading={e.pending && !e.text} />}
              showLoadingBar={e.pending}>
              <SpaceBetween size="xs">
                {e.text && <div style={{ whiteSpace: 'pre-wrap' }}>{e.text}</div>}
                {!!e.tools?.length && (
                  <SpaceBetween direction="horizontal" size="xxs">
                    {e.tools.map((x, i) => (
                      <StatusIndicator key={i} type={x.status === 'completed' ? 'success' : x.status === 'failed' ? 'error' : 'loading'}>
                        {TOOL_LABELS[x.name]?.[lang] ?? x.name}
                      </StatusIndicator>
                    ))}
                  </SpaceBetween>
                )}
                {e.question && <QuestionCard q={e.question} onAnswer={onAnswer} thumbs={thumbs} titles={titles} />}
                {e.error && <StatusIndicator type="error">{e.error}</StatusIndicator>}
              </SpaceBetween>
            </ChatBubble>
          ))}
          {busy && !entries.some((e) => e.pending) && <LoadingBar variant="gen-ai" />}
          <div ref={endRef} />
        </SpaceBetween>
      </div>
      {selectionLabel && (
        <SpaceBetween direction="horizontal" size="xs" alignItems="center">
          <Badge color="blue">{t('selected')}: {selectionLabel}</Badge>
          <Button variant="inline-link" onClick={onClearSelection}>{t('clear')}</Button>
        </SpaceBetween>
      )}
      <PromptInput value={value} onChange={(e) => setValue(e.detail.value)} onAction={send}
        actionButtonIconName="send" actionButtonAriaLabel="send" disableActionButton={busy || !value.trim()}
        placeholder={busy ? t('agentWorking') : t('chatPlaceholder')} minRows={compact ? 3 : 2} maxRows={8} />
    </div>
  );
}

function QuestionCard({ q, onAnswer, thumbs, titles }: { q: Extract<AgentEvent, { type: 'question' }>; onAnswer: Props['onAnswer']; thumbs: Props['thumbs']; titles: Props['titles'] }) {
  const { t } = useI18n();
  const [picked, setPicked] = useState<number[]>([]);
  const [done, setDone] = useState(false);
  const answer = (value: unknown, display: string) => { setDone(true); onAnswer(q.questionId, value, display); };
  return (
    <Box padding="s">
      <SpaceBetween size="s">
        <Box fontWeight="bold">{q.question}</Box>
        {q.kind === 'choice' && (
          <SpaceBetween direction="horizontal" size="xs">
            {(q.options ?? []).map((o) => <Button key={o} disabled={done} onClick={() => answer(o, o)}>{o}</Button>)}
          </SpaceBetween>
        )}
        {q.kind === 'confirm' && (
          <SpaceBetween size="xs">
            {q.estimate && <Box variant="small" color="text-body-secondary">{Object.entries(q.estimate).map(([k, v]) => `${k}: ${v}`).join(' · ')}</Box>}
            <SpaceBetween direction="horizontal" size="xs">
              <Button variant="primary" disabled={done} onClick={() => answer(true, t('yes'))}>{t('yes')}</Button>
              <Button disabled={done} onClick={() => answer(false, t('no'))}>{t('no')}</Button>
            </SpaceBetween>
          </SpaceBetween>
        )}
        {q.kind === 'clips' && (
          <SpaceBetween size="xs">
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(140px, 1fr))', gap: 8 }}>
              {(q.clips ?? []).map((c, i) => (
                <div key={i} onClick={() => !done && setPicked(q.multiple ? (picked.includes(i) ? picked.filter((x) => x !== i) : [...picked, i]) : [i])}
                  style={{ cursor: done ? 'default' : 'pointer', borderRadius: 8, padding: 4, outline: picked.includes(i) ? '2px solid #0972d3' : '1px solid rgba(127,127,127,0.3)' }}>
                  {thumbs[c.assetId] ? <img className="thumb" src={thumbs[c.assetId]} alt="" /> : <div className="thumb-placeholder" />}
                  <Box variant="small" fontWeight="bold">{c.label ?? titles[c.assetId] ?? c.assetId}</Box>
                  <Box variant="small" color="text-body-secondary">{fmtTime(c.startSec)}–{fmtTime(c.endSec)}</Box>
                </div>
              ))}
            </div>
            <Button variant="primary" disabled={done || !picked.length}
              onClick={() => answer(picked.map((i) => q.clips![i]), picked.map((i) => q.clips![i].label ?? `#${i + 1}`).join(', '))}>{t('confirm')}</Button>
          </SpaceBetween>
        )}
      </SpaceBetween>
    </Box>
  );
}
