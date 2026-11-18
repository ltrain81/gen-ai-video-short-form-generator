import Box from '@cloudscape-design/components/box';
import Icon from '@cloudscape-design/components/icon';
import StatusIndicator from '@cloudscape-design/components/status-indicator';
import * as awsui from '@cloudscape-design/design-tokens';
import { useState } from 'react';
import { useI18n } from '../i18n';

/** One thing the agent did: a tool call (with a readable summary + raw arguments) or a reasoning summary. */
export interface AgentStep {
  kind: 'tool' | 'thought';
  name?: string;
  status?: string;
  summary?: string;
  detail?: string;
  ms?: number;
  error?: string;
  text?: string;
}

export const TOOL_LABELS: Record<string, { ko: string; en: string }> = {
  get_context: { ko: '프로젝트 확인', en: 'Reading project' },
  search_archive: { ko: '아카이브 검색', en: 'Searching archive' },
  get_asset_overview: { ko: '영상 파악', en: 'Understanding video' },
  get_scenes: { ko: '장면 확인', en: 'Reading scenes' },
  read_transcript: { ko: '대사 읽기', en: 'Reading transcript' },
  look: { ko: '화면 보기', en: 'Looking at frames' },
  ask_video: { ko: '영상에 질문', en: 'Asking the video model' },
  snap: { ko: '컷 지점 맞추기', en: 'Snapping cut points' },
  analyze_framing: { ko: '세로 프레이밍 분석', en: 'Analyzing framing' },
  get_timeline: { ko: '타임라인 확인', en: 'Reading timeline' },
  edit_timeline: { ko: '편집 적용', en: 'Editing timeline' },
  ask_user: { ko: '질문', en: 'Asking you' },
  show_ui: { ko: '선택 화면 표시', en: 'Showing choices' },
  remember: { ko: '선호 저장', en: 'Saving preference' },
  list_skills: { ko: '편집 스킬 목록', en: 'Listing skills' },
  read_skill: { ko: '편집 스킬 읽기', en: 'Reading skill' },
  apply_brand_kit: { ko: '브랜드 킷 적용', en: 'Applying brand kit' },
  estimate_generation: { ko: '생성 비용 추정', en: 'Estimating generation' },
  generate_media: { ko: '미디어 생성', en: 'Generating media' },
  list_generation_jobs: { ko: '생성 작업 확인', en: 'Checking generation jobs' },
  analyze_audio: { ko: '오디오 분석', en: 'Analyzing audio' },
  preview_frames: { ko: '결과 화면 확인', en: 'Checking rendered frames' },
  render_video: { ko: '영상 렌더링 시작', en: 'Starting a render' },
  list_renders: { ko: '렌더 목록 확인', en: 'Checking renders' },
  list_versions: { ko: '버전 목록', en: 'Listing versions' },
  restore_version: { ko: '버전 복원', en: 'Restoring a version' },
  get_item: { ko: '항목 상세 확인', en: 'Reading an item' },
  export_timeline: { ko: '편집 파일 내보내기', en: 'Exporting timeline' },
  plan_topic_series: { ko: '주제별 구간 찾기', en: 'Planning topic shorts' },
  create_series: { ko: '주제별 숏폼 만들기', en: 'Creating topic shorts' },
};

const MODEL_LABELS: Record<string, string> = {
  'openai.gpt-6.1-sol': 'GPT-6.1 Sol', 'openai.gpt-6.1-terra': 'GPT-6.1 Terra', 'openai.gpt-6-luna': 'GPT-6 Luna',
  'openai.gpt-5.6-sol': 'GPT-5.6 Sol', 'openai.gpt-5.6-luna': 'GPT-5.6 Luna', 'us.openai.gpt-6.1-sol': 'GPT-6.1 Sol (Strands)',
};
export const modelLabel = (id?: string) => (id ? MODEL_LABELS[id] ?? id : '');

const secs = (ms?: number) => (ms == null ? '' : ms < 1000 ? `${ms}ms` : `${(ms / 1000).toFixed(ms < 10000 ? 1 : 0)}s`);

function StepRow({ s, lang, live }: { s: AgentStep; lang: 'ko' | 'en'; live: boolean }) {
  const [open, setOpen] = useState(false);
  if (s.kind === 'thought') {
    return (
      <div onClick={() => setOpen(!open)} style={{ display: 'flex', gap: 8, cursor: 'pointer', color: awsui.colorTextBodySecondary, fontSize: 13 }}>
        <span style={{ width: 16, textAlign: 'center', flexShrink: 0 }}>💭</span>
        <span style={{ fontStyle: 'italic', whiteSpace: 'pre-wrap', ...(open ? {} : { display: '-webkit-box', WebkitLineClamp: 2, WebkitBoxOrient: 'vertical', overflow: 'hidden' }) }}>{s.text}</span>
      </div>
    );
  }
  const label = TOOL_LABELS[s.name ?? '']?.[lang] ?? s.name;
  const status = s.status === 'in_progress' ? (live ? 'loading' : 'stopped') : s.status === 'failed' ? 'error' : 'success';
  return (
    <div style={{ fontSize: 13 }}>
      <div onClick={() => s.detail && setOpen(!open)} style={{ display: 'flex', gap: 8, alignItems: 'baseline', cursor: s.detail ? 'pointer' : 'default' }}>
        <span style={{ width: 16, flexShrink: 0 }}><StatusIndicator type={status} iconAriaLabel={s.status} /></span>
        <span style={{ flex: 1, minWidth: 0 }}>
          <b style={{ fontWeight: 600 }}>{label}</b>
          {s.summary && <span style={{ color: awsui.colorTextBodySecondary }}> · {s.summary}</span>}
          {s.error && <div style={{ color: awsui.colorTextStatusError }}>{s.error}</div>}
        </span>
        <span style={{ color: awsui.colorTextBodySecondary, fontSize: 12, flexShrink: 0 }}>{secs(s.ms)}</span>
        {s.detail && <Icon name={open ? 'caret-up' : 'caret-down'} size="small" variant="subtle" />}
      </div>
      {open && s.detail && (
        <pre style={{ margin: '4px 0 2px 24px', padding: 8, borderRadius: 6, fontSize: 11, whiteSpace: 'pre-wrap', wordBreak: 'break-all',
          background: 'rgba(127,127,127,0.1)', color: awsui.colorTextBodySecondary, maxHeight: 220, overflow: 'auto' }}>{pretty(s.detail)}</pre>
      )}
    </div>
  );
}

function pretty(s: string): string {
  try { return JSON.stringify(JSON.parse(s), null, 2); } catch { return s; }
}

/** "What the AI did": live while the turn runs, collapsible afterwards. */
// Harness-internal calls (MCP discovery) are not editing work; keep them out of the list.
const HIDDEN = new Set(['list_mcp_resources', 'list_mcp_resource_templates', 'read_mcp_resource']);

export default function AgentSteps({ steps: all, pending, model, effort }: { steps: AgentStep[]; pending?: boolean; model?: string; effort?: string }) {
  const steps = all.filter((s) => !(s.kind === 'tool' && HIDDEN.has(s.name ?? '')))
    .map((s) => (s.kind === 'thought' ? { ...s, text: (s.text ?? '').replace(/\*\*/g, '').trim() } : s));
  const { t, lang } = useI18n();
  const [userOpen, setUserOpen] = useState<boolean | null>(null);
  const open = userOpen ?? !!pending;
  const tools = steps.filter((s) => s.kind === 'tool');
  const running = [...tools].reverse().find((s) => s.status === 'in_progress');
  const failed = tools.filter((s) => s.status === 'failed').length;
  const total = tools.reduce((a, s) => a + (s.ms ?? 0), 0);
  const lastThought = [...steps].reverse().find((s) => s.kind === 'thought');
  const head = pending
    ? (running ? `${TOOL_LABELS[running.name ?? '']?.[lang] ?? running.name}${running.summary ? ` · ${running.summary}` : ''}` : lastThought ? t('thinking') : t('agentWorking'))
    : total > 0 ? t('stepsSummary', { n: tools.length, s: secs(total) }) : t('stepsDone', { n: tools.length });
  return (
    <div style={{ border: `1px solid ${awsui.colorBorderDividerDefault}`, borderRadius: 8, padding: '6px 10px' }}>
      <div onClick={() => setUserOpen(!open)} role="button" tabIndex={0} aria-expanded={open}
        onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); setUserOpen(!open); } }}
        style={{ display: 'flex', alignItems: 'center', gap: 8, cursor: 'pointer', fontSize: 13 }}>
        <Icon name={open ? 'caret-down-filled' : 'caret-right-filled'} size="small" variant="subtle" />
        <StatusIndicator type={pending ? 'loading' : failed ? 'warning' : 'success'} colorOverride="grey">
          <span style={{ overflow: 'hidden', textOverflow: 'ellipsis' }}>{head}</span>
        </StatusIndicator>
        <span style={{ marginLeft: 'auto', color: awsui.colorTextBodySecondary, fontSize: 12, whiteSpace: 'nowrap' }}>
          {modelLabel(model)}{effort ? ` · ${effort}` : ''}
        </span>
      </div>
      {open && (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 6, padding: '8px 0 4px 4px' }}>
          {steps.map((s, i) => <StepRow key={i} s={s} lang={lang} live={!!pending} />)}
          {!steps.length && <Box variant="small" color="text-body-secondary">{t('agentWorking')}</Box>}
        </div>
      )}
    </div>
  );
}
