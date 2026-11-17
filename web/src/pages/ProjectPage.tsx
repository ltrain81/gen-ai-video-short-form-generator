import Badge from '@cloudscape-design/components/badge';
import Box from '@cloudscape-design/components/box';
import BreadcrumbGroup from '@cloudscape-design/components/breadcrumb-group';
import ExpandableSection from '@cloudscape-design/components/expandable-section';
import SpaceBetween from '@cloudscape-design/components/space-between';
import Spinner from '@cloudscape-design/components/spinner';
import StatusIndicator from '@cloudscape-design/components/status-indicator';
import * as awsui from '@cloudscape-design/design-tokens';
import type { Item, Timeline } from '@shorts/timeline';
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useLocation, useNavigate, useParams } from 'react-router-dom';
import { useApp } from '../App';
import { api } from '../api/client';
import { AgentEvent, subscribeProject } from '../api/realtime';
import type { Asset, AssetMedia, ChatMessage, ProjectSummary } from '../api/types';
import ChatPanel, { ChatEntry } from '../components/ChatPanel';
import EditorPanel from '../components/EditorPanel';
import RendersModal from '../components/RendersModal';
import ButtonDropdown from '@cloudscape-design/components/button-dropdown';
import SegmentedControl from '@cloudscape-design/components/segmented-control';
import Button from '@cloudscape-design/components/button';
import type { EditOp } from '../components/TimelineStrip';
import Preview from '../components/Preview';
import TimelineStrip from '../components/TimelineStrip';
import { useI18n } from '../i18n';
import { fmtTime } from '../util';

function fromHistory(m: ChatMessage[]): ChatEntry[] {
  return m.map((x, i) => ({ id: `h${i}`, role: x.role, text: x.text, tools: x.tools, error: x.error }));
}

const CHAT_MIN = 320;
const CHAT_DEFAULT = 440;
const chatMax = () => Math.max(CHAT_MIN, Math.min(1100, Math.round(window.innerWidth * 0.65)));
const clampChat = (w: number) => Math.round(Math.min(chatMax(), Math.max(CHAT_MIN, w)));

export default function ProjectPage() {
  const { projectId = '' } = useParams();
  const location = useLocation();
  const { me, notify } = useApp();
  const { t } = useI18n();
  const navigate = useNavigate();
  const [project, setProject] = useState<ProjectSummary | null>(null);
  const [chatW, setChatW] = useState(() => clampChat(Number(localStorage.getItem('chatWidth')) || CHAT_DEFAULT));
  useEffect(() => { localStorage.setItem('chatWidth', String(chatW)); }, [chatW]);
  const startResize = (e: React.PointerEvent) => {
    e.preventDefault();
    const x0 = e.clientX, w0 = chatW;
    const move = (ev: PointerEvent) => setChatW(clampChat(w0 + (x0 - ev.clientX)));
    const up = () => { window.removeEventListener('pointermove', move); window.removeEventListener('pointerup', up); document.body.style.cursor = ''; document.body.style.userSelect = ''; };
    document.body.style.cursor = 'col-resize'; document.body.style.userSelect = 'none';
    window.addEventListener('pointermove', move); window.addEventListener('pointerup', up);
  };
  const [timeline, setTimeline] = useState<Timeline | null>(null);
  const [media, setMedia] = useState<Record<string, AssetMedia>>({});
  const [titles, setTitles] = useState<Record<string, string>>({});
  const [thumbs, setThumbs] = useState<Record<string, string | undefined>>({});
  const [entries, setEntries] = useState<ChatEntry[]>([]);
  const [busy, setBusy] = useState(false);
  const [time, setTime] = useState(0);
  const [selected, setSelected] = useState<{ item: Item; trackId?: string } | undefined>();
  const sentFirst = useRef(false);
  const [mode, setMode] = useState<'ai' | 'manual'>('ai');
  const [saving, setSaving] = useState(false);
  const [rendersOpen, setRendersOpen] = useState(false);
  const [renderKey, setRenderKey] = useState(0);
  const undoStack = useRef<number[]>([]);
  const redoStack = useRef<number[]>([]);

  const ensureMedia = useCallback(async (ids: string[]) => {
    const missing = ids.filter((id) => !(id in media));
    if (!missing.length) return;
    const results = await Promise.all(missing.map(async (id) => {
      try {
        const [m, a] = await Promise.all([api.media(id), api.asset(id)]);
        return [id, m, a.displayTitle, a.thumbnailUrl] as const;
      } catch { return [id, undefined, id, undefined] as const; }
    }));
    setMedia((x) => ({ ...x, ...Object.fromEntries(results.filter((r) => r[1]).map((r) => [r[0], r[1]!])) }));
    setTitles((x) => ({ ...x, ...Object.fromEntries(results.map((r) => [r[0], r[2]])) }));
    setThumbs((x) => ({ ...x, ...Object.fromEntries(results.map((r) => [r[0], r[3]])) }));
  }, [media]);

  const loadProject = useCallback(async () => {
    const r = await api.project(projectId);
    setProject(r.project);
    setTimeline(r.timeline);
    const ids = new Set<string>(r.project.assetIds ?? []);
    r.timeline.tracks.forEach((tr) => tr.items.forEach((i) => { if (i.type === 'clip') ids.add(i.assetId); }));
    ensureMedia([...ids]);
  }, [projectId, ensureMedia]);

  // Initial load + chat history.
  useEffect(() => {
    loadProject().catch((e) => notify('error', e.message));
    api.chat(projectId).then((c) => { setEntries(fromHistory(c.messages)); setBusy(c.busy); }).catch(() => undefined);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [projectId]);

  // Realtime.
  const onEvent = useCallback((e: AgentEvent) => {
    switch (e.type) {
      case 'turn.started':
        setBusy(true);
        setEntries((x) => x.some((m) => m.pending) ? x : [...x, { id: e.turn, role: 'assistant', text: '', pending: true, tools: [] }]);
        break;
      case 'assistant.delta':
        setEntries((x) => {
          const i = x.findIndex((m) => m.pending);
          if (i < 0) return [...x, { id: e.turn, role: 'assistant', text: e.text, pending: true, tools: [] }];
          const copy = [...x]; copy[i] = { ...copy[i], text: copy[i].text + e.text }; return copy;
        });
        break;
      case 'tool':
        setEntries((x) => x.map((m) => {
          if (!m.pending) return m;
          const tools = [...(m.tools ?? [])];
          const j = tools.findIndex((tt) => tt.name === e.name && tt.status === 'in_progress');
          if (j >= 0) tools[j] = { name: e.name, status: e.status }; else tools.push({ name: e.name, status: e.status });
          return { ...m, tools };
        }));
        break;
      case 'question':
        setEntries((x) => x.map((m) => (m.pending ? { ...m, question: e } : m)));
        break;
      case 'timeline.updated':
        loadProject().catch(() => undefined);
        break;
      case 'turn.completed':
      case 'turn.failed':
        setBusy(false);
        setEntries((x) => x.map((m) => (m.pending ? { ...m, pending: false, error: e.type === 'turn.failed' ? (e.error ?? 'failed') : undefined } : m)));
        loadProject().catch(() => undefined);
        // The saved transcript is authoritative (streamed deltas can be lost or repeated on reconnects).
        api.chat(projectId).then((c) => {
          setEntries((x) => {
            const questions = x.filter((m) => m.question);
            const hist = fromHistory(c.messages);
            questions.forEach((q) => { const i = hist.findIndex((h) => h.role === 'assistant' && q.text && h.text === q.text); if (i >= 0) hist[i] = { ...hist[i], question: q.question }; });
            const lastQ = questions[questions.length - 1];
            if (lastQ && !hist.some((h) => h.question)) { const i = hist.map((h) => h.role).lastIndexOf('assistant'); if (i >= 0) hist[i] = { ...hist[i], question: lastQ.question }; }
            return hist;
          });
        }).catch(() => undefined);
        break;
    }
  }, [loadProject]);

  useEffect(() => {
    if (!me) return;
    // StrictMode mounts effects twice; the connect is async, so guard against a leaked first subscription.
    let cancelled = false;
    let off: (() => void) | undefined;
    subscribeProject(projectId, me.sub, onEvent)
      .then((f) => { if (cancelled) f(); else off = f; })
      .catch((e) => notify('error', `realtime: ${e.message}`));
    return () => { cancelled = true; off?.(); };
  }, [projectId, me, onEvent, notify]);

  const selection = useMemo(() => {
    if (!selected) return undefined;
    const i = selected.item;
    return { itemId: i.id, track: selected.trackId, type: i.type, start: i.start, end: i.start + i.duration,
      ...(i.type === 'clip' ? { assetId: i.assetId, srcIn: i.srcIn, srcOut: i.srcOut } : {}),
      ...(i.type === 'text' ? { text: i.text } : {}) };
  }, [selected]);

  const send = useCallback(async (text: string, attach?: Asset[]) => {
    const note = attach?.length ? `\n📎 ${attach.map((a) => a.displayTitle).join(', ')}` : '';
    setEntries((x) => [...x, { id: `u${Date.now()}`, role: 'user', text: text + note }]);
    setBusy(true);
    try {
      await api.sendChat(projectId, { text, selection, attachAssetIds: attach?.map((a) => a.assetId) });
      if (attach?.length) loadProject().catch(() => undefined);
    } catch (e) {
      setBusy(false);
      notify('error', (e as Error).message);
    }
  }, [projectId, selection, notify, loadProject]);

  const answer = useCallback(async (questionId: string, value: unknown, display: string) => {
    setEntries((x) => [...x, { id: `u${Date.now()}`, role: 'user', text: display }]);
    setBusy(true);
    try { await api.sendChat(projectId, { text: display, answer: { questionId, value } }); }
    catch (e) { setBusy(false); notify('error', (e as Error).message); }
  }, [projectId, notify]);

  // Manual edits: same server engine as the agent; touched properties are pinned.
  const applyOps = useCallback(async (ops: EditOp[], message: string) => {
    if (!project || busy) return;
    setSaving(true);
    try {
      const before = project.currentVersion;
      const r = await api.applyOps(projectId, ops, before, message);
      const failed = r.results.filter((x) => !x.ok);
      if (failed.length) notify('warning', failed.map((f) => f.error).join('; '));
      if (r.currentVersion !== before) {
        undoStack.current.push(before);
        redoStack.current = [];
        setTimeline(r.timeline);
        setProject({ ...project, currentVersion: r.currentVersion });
        const added = r.results.find((x) => x.itemId);
        if (ops.some((o) => o.op === 'remove')) setSelected(undefined);
        if (added?.itemId) {
          const it = r.timeline.tracks.flatMap((tr) => tr.items.map((i) => ({ i, tr: tr.id }))).find((x) => x.i.id === added.itemId);
          if (it) setSelected({ item: it.i, trackId: it.tr });
        } else if (selected) {
          const it = r.timeline.tracks.flatMap((tr) => tr.items.map((i) => ({ i, tr: tr.id }))).find((x) => x.i.id === selected.item.id);
          setSelected(it ? { item: it.i, trackId: it.tr } : undefined);
        }
        const ids = ops.map((o) => o.assetId).filter(Boolean) as string[];
        if (ids.length) ensureMedia(ids);
      }
    } catch (e) {
      notify('error', (e as Error).message);
      loadProject().catch(() => undefined);
    } finally {
      setSaving(false);
    }
  }, [project, busy, projectId, notify, selected, ensureMedia, loadProject]);

  const restore = useCallback(async (v: number, push: 'undo' | 'redo' | 'none' = 'none') => {
    if (!project) return;
    const cur = project.currentVersion;
    await api.restoreVersion(projectId, v);
    if (push === 'undo') redoStack.current.push(cur); else if (push === 'redo') undoStack.current.push(cur); else undoStack.current.push(cur);
    await loadProject();
  }, [project, projectId, loadProject]);
  const undo = useCallback(() => { const v = undoStack.current.pop(); if (v != null) restore(v, 'undo'); }, [restore]);
  const redo = useCallback(() => { const v = redoStack.current.pop(); if (v != null) restore(v, 'redo'); }, [restore]);

  // Keyboard shortcuts in manual mode.
  useEffect(() => {
    if (mode !== 'manual') return;
    const onKey = (e: KeyboardEvent) => {
      const tag = (e.target as HTMLElement)?.tagName;
      if (tag === 'INPUT' || tag === 'TEXTAREA') return;
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'z') { e.preventDefault(); if (e.shiftKey) redo(); else undo(); return; }
      if (!selected) return;
      if (e.key === 'Delete' || e.key === 'Backspace') { e.preventDefault(); applyOps([{ op: 'remove', itemId: selected.item.id }], 'delete'); }
      if (e.key.toLowerCase() === 's') { e.preventDefault(); applyOps([{ op: 'split', itemId: selected.item.id, at: time }], 'split'); }
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [mode, selected, time, applyOps, undo, redo]);

  // First message typed on the Studio landing.
  useEffect(() => {
    const first = (location.state as { firstMessage?: string } | null)?.firstMessage;
    if (first && me && project && !sentFirst.current) {
      sentFirst.current = true;
      window.history.replaceState({}, '');
      send(first);
    }
  }, [location.state, me, project, send]);

  if (!project || !timeline) return <Box textAlign="center" padding="xxl"><Spinner size="large" /></Box>;

  const selLabel = selected ? (selected.item.type === 'clip' ? `${titles[selected.item.assetId] ?? ''} ${fmtTime(selected.item.start)}` : selected.item.type) : undefined;
  const hasEdit = timeline.tracks.some((tr) => tr.items.length > 0);
  const suggestions = hasEdit ? [t('sugShorter'), t('sugCaptions'), t('sugMusic'), t('sugLandscape')] : [];
  const divider = `1px solid ${awsui.colorBorderDividerDefault}`;

  return (
    <div className="studio-split" style={{ ['--chat-w' as string]: `${chatW}px` }}>
      {rendersOpen && <RendersModal projectId={projectId} refreshKey={renderKey} onClose={() => setRendersOpen(false)} />}
      {/* Left: canvas (preview) + docked timeline */}
      <section className="studio-canvas-pane">
        <div className="studio-toolbar" style={{ borderBottom: divider }}>
          <SpaceBetween direction="horizontal" size="s" alignItems="center">
            <BreadcrumbGroup items={[{ text: t('navStudio'), href: '/' }, { text: project.title, href: '#' }]}
              onFollow={(e) => { e.preventDefault(); if (e.detail.href === '/') navigate('/'); }} ariaLabel="breadcrumbs" />
          </SpaceBetween>
          <SpaceBetween direction="horizontal" size="xs" alignItems="center">
            <SegmentedControl selectedId={mode} onChange={(e) => setMode(e.detail.selectedId as 'ai' | 'manual')}
              options={[{ id: 'ai', text: t('modeAi'), iconName: 'gen-ai' }, { id: 'manual', text: t('modeManual'), iconName: 'edit' }]} />
            {mode === 'manual' && <>
              <Button variant="icon" iconName="undo" ariaLabel="undo" disabled={!undoStack.current.length || saving} onClick={undo} />
              <Button variant="icon" iconName="redo" ariaLabel="redo" disabled={!redoStack.current.length || saving} onClick={redo} />
            </>}
            {saving && <StatusIndicator type="loading">{t('saving')}</StatusIndicator>}
            <ButtonDropdown expandToViewport variant="primary" items={[
              { id: 'final', text: t('renderFinal'), description: t('renderFinalHint') },
              { id: 'preview', text: t('renderPreview'), description: t('renderPreviewHint') },
              { id: 'list', text: t('renders') },
              { id: 'nle', text: t('exportNle'), items: [
                { id: 'otio', text: 'OpenTimelineIO (.otio)' }, { id: 'fcpxml', text: 'FCPXML (Final Cut / DaVinci / Premiere)' }, { id: 'edl', text: 'EDL (CMX3600)' }] },
            ]} onItemClick={async ({ detail }) => {
              try {
                if (detail.id === 'final' || detail.id === 'preview') {
                  await api.requestRender(projectId, detail.id); setRenderKey((k) => k + 1); setRendersOpen(true);
                } else if (detail.id === 'list') setRendersOpen(true);
                else {
                  const r = await api.exportProject(projectId, detail.id as 'otio' | 'fcpxml' | 'edl');
                  const a = document.createElement('a');
                  a.href = URL.createObjectURL(new Blob([r.content], { type: r.contentType }));
                  a.download = r.filename; a.click(); URL.revokeObjectURL(a.href);
                }
              } catch (e) { notify('error', (e as Error).message); }
            }}>{t('export')}</ButtonDropdown>
            <Badge>{timeline.settings.aspect}</Badge>
            <Badge color="grey">v{project.currentVersion}</Badge>
            {busy && <StatusIndicator type="loading">{t('agentWorking')}</StatusIndicator>}
          </SpaceBetween>
        </div>
        <div className="studio-stage">
          <Preview timeline={timeline} media={media} time={time} onTime={setTime} selectedId={selected?.item.id} />
        </div>
        <div className="studio-timeline" style={{ borderTop: divider, background: awsui.colorBackgroundContainerContent }}>
          <ExpandableSection defaultExpanded variant="footer" headerText={t('timeline')}>
            <TimelineStrip timeline={timeline} time={time} onSeek={setTime} selectedId={selected?.item.id}
              onSelect={(item, trackId) => setSelected(item ? { item, trackId } : undefined)} titles={titles}
              editable={mode === 'manual' && !busy} onOps={applyOps} />
          </ExpandableSection>
        </div>
      </section>
      {/* Right: chat sidebar */}
      <aside className="studio-chat-pane" style={{ borderLeft: divider, background: awsui.colorBackgroundContainerContent }}>
        <div className="chat-resizer" role="separator" aria-orientation="vertical" aria-label={t('resizeChat')} tabIndex={0}
          aria-valuenow={chatW} aria-valuemin={CHAT_MIN} aria-valuemax={chatMax()}
          onPointerDown={startResize} onDoubleClick={() => setChatW(CHAT_DEFAULT)}
          onKeyDown={(e) => {
            if (e.key === 'ArrowLeft') setChatW((w) => clampChat(w + 40));
            if (e.key === 'ArrowRight') setChatW((w) => clampChat(w - 40));
          }} />
        {mode === 'manual' ? (
          <>
            {busy && <Box padding="s"><StatusIndicator type="loading">{t('lockedByAgent')}</StatusIndicator></Box>}
            <EditorPanel projectId={projectId} timeline={timeline} selected={selected?.item} time={time} titles={titles}
              onOps={applyOps} onRestore={(v) => restore(v)} version={project.currentVersion} disabled={busy || saving} />
          </>
        ) : <ChatPanel entries={entries} busy={busy} onSend={send} onAnswer={answer} thumbs={thumbs} titles={titles}
          suggestions={suggestions} selectionLabel={selLabel} onClearSelection={() => setSelected(undefined)}
          header={
            <div style={{ padding: '14px 20px', borderBottom: divider }}>
              <Box variant="h3" padding="n">{project.title}</Box>
              {project.synopsis && project.synopsis !== project.title && <Box variant="small" color="text-body-secondary">{project.synopsis}</Box>}
            </div>
          } />}
      </aside>
    </div>
  );
}
