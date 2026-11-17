import Avatar from '@cloudscape-design/chat-components/avatar';
import LoadingBar from '@cloudscape-design/chat-components/loading-bar';
import SupportPromptGroup from '@cloudscape-design/chat-components/support-prompt-group';
import Box from '@cloudscape-design/components/box';
import Button from '@cloudscape-design/components/button';
import ExpandableSection from '@cloudscape-design/components/expandable-section';
import PromptInput from '@cloudscape-design/components/prompt-input';
import SpaceBetween from '@cloudscape-design/components/space-between';
import StatusIndicator from '@cloudscape-design/components/status-indicator';
import Token from '@cloudscape-design/components/token';
import TokenGroup from '@cloudscape-design/components/token-group';
import * as awsui from '@cloudscape-design/design-tokens';
import { ReactNode, useEffect, useRef, useState } from 'react';
import type { AgentEvent } from '../api/realtime';
import type { Asset, AssetMedia } from '../api/types';
import AssetPicker from './AssetPicker';
import ChatWidget, { legacyValue, questionToWidget, type WidgetEntry } from './ChatWidget';
import { ASSET_MIME } from './TimelineStrip';
import { uploadAndFetch } from '../api/upload';
import { useI18n } from '../i18n';

export interface ChatEntry {
  id: string;
  role: 'user' | 'assistant';
  text: string;
  tools?: { name: string; status: string }[];
  question?: Extract<AgentEvent, { type: 'question' }>;
  widgets?: (WidgetEntry & { legacyKind?: string })[];
  pending?: boolean;
  error?: string;
}

interface Props {
  entries: ChatEntry[];
  busy: boolean;
  selectionLabel?: string;
  onClearSelection?: () => void;
  onSend: (text: string, attach?: Asset[]) => void;
  onAnswer: (questionId: string, value: unknown, display: string) => void;
  thumbs: Record<string, string | undefined>;
  titles: Record<string, string>;
  media?: Record<string, AssetMedia>;
  suggestions?: string[];
  header?: ReactNode;
  projectId?: string;
  onError?: (msg: string) => void;
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

export default function ChatPanel({ entries, busy, selectionLabel, onClearSelection, onSend, onAnswer, thumbs, titles, suggestions, header, projectId, onError, media = {} }: Props) {
  const { t } = useI18n();
  const [value, setValue] = useState('');
  const [attach, setAttach] = useState<Asset[]>([]);
  const [picker, setPicker] = useState(false);
  const [dropOver, setDropOver] = useState(false);
  const [uploading, setUploading] = useState(0);
  const onFiles = (files: File[]) => {
    setUploading((n) => n + files.length);
    files.forEach((f) => uploadAndFetch(f, projectId ? { scope: 'project', projectId } : {})
      .then((a) => setAttach((x) => [...x, a]))
      .catch((e) => onError?.(`${f.name}: ${(e as Error).message}`))
      .finally(() => setUploading((n) => n - 1)));
  };
  const endRef = useRef<HTMLDivElement>(null);
  useEffect(() => { endRef.current?.scrollIntoView({ behavior: 'smooth', block: 'end' }); }, [entries, busy]);

  const send = (text = value) => {
    const v = text.trim();
    if (!v || busy) return;
    onSend(v, attach.length ? attach : undefined);
    setValue('');
    setAttach([]);
  };
  const last = entries[entries.length - 1];
  const showSuggestions = !busy && !!suggestions?.length && last?.role === 'assistant' && !last.question && !last.widgets?.some((w) => w.spec.interactive && !w.answered);

  return (
    <div className={dropOver ? 'drop-zone-active' : undefined} style={{ display: 'flex', flexDirection: 'column', height: '100%', minHeight: 0 }}
      onDragOver={(e) => { if (!e.dataTransfer.types.includes('Files') && !e.dataTransfer.types.includes(ASSET_MIME)) return; e.preventDefault(); setDropOver(true); }}
      onDragLeave={(e) => { if (!e.currentTarget.contains(e.relatedTarget as Node)) setDropOver(false); }}
      onDrop={(e) => {
        setDropOver(false);
        const raw = e.dataTransfer.getData(ASSET_MIME);
        if (raw) { e.preventDefault(); try { const a = JSON.parse(raw); setAttach((x) => x.some((y) => y.assetId === a.assetId) ? x : [...x, { ...a, displayTitle: a.title ?? a.assetId } as Asset]); } catch { /* ignore */ } return; }
        if (e.dataTransfer.files.length) { e.preventDefault(); onFiles([...e.dataTransfer.files]); }
      }}>
      {header}
      <div style={{ flex: 1, overflowY: 'auto', padding: '20px 20px 8px' }}>
        {entries.length === 0 && !busy && (
          <Box textAlign="center" color="text-body-secondary" padding={{ top: 'xxxl' }}>{t('chatEmpty')}</Box>
        )}
        <SpaceBetween size="l">
          {entries.map((e) => (e.role === 'user' ? <UserMessage key={e.id} text={e.text} /> : (
            <AssistantMessage key={e.id} entry={e} onAnswer={onAnswer} thumbs={thumbs} titles={titles} media={media} />
          )))}
          {busy && !entries.some((e) => e.pending) && (
            <div style={{ display: 'flex', gap: 12 }}>
              <Avatar color="gen-ai" iconName="gen-ai" ariaLabel="AI" loading />
              <div style={{ flex: 1, paddingTop: 10 }}><LoadingBar variant="gen-ai" /></div>
            </div>
          )}
          {showSuggestions && (
            <div style={{ paddingLeft: 44 }}>
              <SupportPromptGroup ariaLabel={t('suggestions')} alignment="horizontal"
                items={suggestions!.map((s, i) => ({ id: String(i), text: s }))}
                onItemClick={({ detail }) => send(suggestions![Number(detail.id)])} />
            </div>
          )}
          <div ref={endRef} />
        </SpaceBetween>
      </div>
      <div style={{ padding: '12px 16px 16px', borderTop: `1px solid ${awsui.colorBorderDividerDefault}` }}>
        <PromptInput value={value} onChange={(e) => setValue(e.detail.value)} onAction={() => send()}
          actionButtonIconName="send" actionButtonAriaLabel="send" disableActionButton={busy || !value.trim()}
          placeholder={busy ? t('agentWorking') : uploading ? `${t('uploading')} (${uploading})…` : t('chatPlaceholder')} minRows={2} maxRows={8}
          secondaryActions={<Button variant="icon" iconName="folder-open" ariaLabel={t('attachFromArchive')} onClick={() => setPicker(true)} />}
          secondaryContent={selectionLabel || attach.length ? (
            <SpaceBetween size="xs">
              {selectionLabel && <Token label={`${t('selected')}: ${selectionLabel}`} onDismiss={onClearSelection} dismissLabel={t('clear')} />}
              {attach.length > 0 && <TokenGroup items={attach.map((a, i) => ({ label: `${i + 1}. ${a.displayTitle}`, description: a.status && a.status !== 'ready' ? t('analyzing') : t((a.kind ?? 'other') as never), dismissLabel: t('remove') }))}
                onDismiss={({ detail }) => setAttach(attach.filter((_, i) => i !== detail.itemIndex))} />}
            </SpaceBetween>
          ) : undefined} />
        <AssetPicker visible={picker} initial={attach} onDismiss={() => setPicker(false)} confirmText={t('attach')}
          onConfirm={(a) => { setAttach(a); setPicker(false); }} />
      </div>
    </div>
  );
}

function UserMessage({ text }: { text: string }) {
  return (
    <div style={{ display: 'flex', justifyContent: 'flex-end' }}>
      <div style={{
        maxWidth: '82%', padding: '10px 14px', whiteSpace: 'pre-wrap', lineHeight: 1.5,
        background: awsui.colorBackgroundChatBubbleIncoming,
        borderRadius: awsui.borderRadiusChatBubble,
      }}>{text}</div>
    </div>
  );
}

function AssistantMessage({ entry: e, onAnswer, thumbs, titles, media }: { entry: ChatEntry; onAnswer: Props['onAnswer']; thumbs: Props['thumbs']; titles: Props['titles']; media: Record<string, AssetMedia> }) {
  const { t, lang } = useI18n();
  const tools = e.tools ?? [];
  const running = tools.find((x) => x.status === 'in_progress');
  return (
    <div style={{ display: 'flex', gap: 12, alignItems: 'flex-start' }}>
      <Avatar color="gen-ai" iconName="gen-ai" ariaLabel="AI" tooltipText="AI" loading={e.pending && !e.text} />
      <div style={{ flex: 1, minWidth: 0, paddingTop: 4, lineHeight: 1.6 }}>
        <SpaceBetween size="s">
          {tools.length > 0 && (
            <ExpandableSection variant="footer" headerText={
              <StatusIndicator type={running || e.pending ? 'loading' : tools.some((x) => x.status === 'failed') ? 'warning' : 'success'} colorOverride="grey">
                {running ? (TOOL_LABELS[running.name]?.[lang] ?? running.name) : e.pending ? t('agentWorking') : t('stepsDone', { n: tools.length })}
              </StatusIndicator>}>
              <ol style={{ margin: 0, paddingLeft: 18, color: awsui.colorTextBodySecondary, fontSize: 13 }}>
                {tools.map((x, i) => <li key={i}>{TOOL_LABELS[x.name]?.[lang] ?? x.name}{x.status === 'failed' ? ' ⚠︎' : ''}</li>)}
              </ol>
            </ExpandableSection>
          )}
          {e.text && <Markdown text={e.text} />}
          {e.pending && !e.text && !tools.length && <LoadingBar variant="gen-ai" />}
          {e.question && !e.widgets?.some((w) => w.uiId === e.question!.questionId) && (
            <ChatWidget w={{ uiId: e.question.questionId, spec: questionToWidget(e.question) }} media={media} thumbs={thumbs} titles={titles}
              onSubmit={(id, v, display) => onAnswer(id, legacyValue(e.question!.kind, v as Record<string, unknown>), display)} />
          )}
          {e.widgets?.map((w) => (
            <ChatWidget key={w.uiId} w={w} media={media} thumbs={thumbs} titles={titles}
              onSubmit={(id, v, display) => onAnswer(id, w.legacyKind ? legacyValue(w.legacyKind, v as Record<string, unknown>) : v, display)} />
          ))}
          {e.error && <StatusIndicator type="error">{e.error}</StatusIndicator>}
        </SpaceBetween>
      </div>
    </div>
  );
}

/** Minimal, safe Markdown (bold, inline code, bullets, line breaks) — no HTML injection. */
function Markdown({ text }: { text: string }) {
  const inline = (line: string, key: number) => {
    const parts = line.split(/(\*\*[^*]+\*\*|`[^`]+`)/g);
    return <span key={key}>{parts.map((p, i) => p.startsWith('**') && p.endsWith('**') ? <b key={i}>{p.slice(2, -2)}</b>
      : p.startsWith('`') && p.endsWith('`') ? <code key={i}>{p.slice(1, -1)}</code> : p)}</span>;
  };
  const lines = text.replace(/\s+$/, '').split('\n');
  const out: ReactNode[] = [];
  let list: ReactNode[] = [];
  const flush = () => { if (list.length) { out.push(<ul key={`l${out.length}`} style={{ margin: '4px 0', paddingLeft: 20 }}>{list}</ul>); list = []; } };
  lines.forEach((l, i) => {
    const m = l.match(/^\s*(?:[-*•]|\d+[.)])\s+(.*)$/);
    if (m) list.push(<li key={i}>{inline(m[1], i)}</li>);
    else { flush(); out.push(l.trim() ? <div key={i}>{inline(l.trim(), i)}</div> : <div key={i} style={{ height: 6 }} />); }
  });
  flush();
  return <div>{out}</div>;
}
