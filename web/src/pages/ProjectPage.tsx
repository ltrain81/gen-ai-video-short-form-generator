import Badge from '@cloudscape-design/components/badge';
import Box from '@cloudscape-design/components/box';
import BreadcrumbGroup from '@cloudscape-design/components/breadcrumb-group';
import ExpandableSection from '@cloudscape-design/components/expandable-section';
import SpaceBetween from '@cloudscape-design/components/space-between';
import Spinner from '@cloudscape-design/components/spinner';
import StatusIndicator from '@cloudscape-design/components/status-indicator';
import * as awsui from '@cloudscape-design/design-tokens';
import type { Item, Timeline } from '@shorts/timeline';
import { timelineDuration } from '@shorts/timeline';
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useLocation, useNavigate, useParams } from 'react-router-dom';
import { useApp } from '../App';
import { api } from '../api/client';
import { AgentEvent, subscribeProject } from '../api/realtime';
import { uploadFile } from '../api/upload';
import type { Asset, AssetMedia, ChatMessage, ChatWidgetHistory, ProjectSummary, SeriesChild } from '../api/types';
import { questionToWidget, widgetAssetIds } from '../components/ChatWidget';
import { modelLabel } from '../components/AgentSteps';
import SpriteFrame from '../components/SpriteFrame';
import ChatPanel, { ChatEntry } from '../components/ChatPanel';
import EditorPanel from '../components/EditorPanel';
import RendersModal from '../components/RendersModal';
import ButtonDropdown from '@cloudscape-design/components/button-dropdown';
import Modal from '@cloudscape-design/components/modal';
import SegmentedControl from '@cloudscape-design/components/segmented-control';
import Button from '@cloudscape-design/components/button';
import type { DragAsset, DropTarget, EditOp, ItemAction } from '../components/TimelineStrip';
import Preview from '../components/Preview';
import TimelineStrip from '../components/TimelineStrip';
import { useI18n } from '../i18n';
import { fmtTime } from '../util';

function fromHistory(m: ChatMessage[], widgets: ChatWidgetHistory[] = []): ChatEntry[] {
  const out: ChatEntry[] = m.map((x, i) => ({ id: `h${i}`, role: x.role, text: x.text, tools: x.tools, steps: x.steps as ChatEntry['steps'],
    model: x.model, effort: x.effort, error: x.error }));
  // A widget belongs to the first assistant message saved after it (messages are saved at the end of a turn).
  for (const w of widgets) {
    const i = m.findIndex((x) => x.role === 'assistant' && x.createdAt >= w.createdAt);
    const at = i >= 0 ? i : out.map((x) => x.role).lastIndexOf('assistant');
    if (at < 0) continue;
    const entry = w.spec ? { uiId: w.uiId, spec: w.spec, answered: w.answered, answer: w.answer as Record<string, unknown> }
      : { uiId: w.uiId, spec: questionToWidget(w.question!), answered: w.answered, legacyKind: w.question!.kind };
    out[at] = { ...out[at], widgets: [...(out[at].widgets ?? []), entry] };
  }
  return out;
}

const SHORTCUTS: [string, string][] = [
  ['Space', 'scPlay'], ['← →', 'scFrame'], ['⇧← ⇧→', 'scSecond'], ['J L', 'scJump'], ['Home End', 'scEnds'],
  ['S', 'scSplit'], ['⌫', 'scDelete'], ['⌘C ⌘V', 'scCopyPaste'], ['⌘D', 'scDuplicate'], ['⌘Z ⇧⌘Z', 'scUndo'],
  ['⌘/Ctrl+Wheel', 'scZoom'], ['Esc', 'scDeselect'], ['?', 'shortcuts'],
];
const CHAT_MIN = 320;
const CHAT_DEFAULT = 440;
const chatMax = () => Math.max(CHAT_MIN, Math.min(1100, Math.round(window.innerWidth * 0.65)));
const clampChat = (w: number) => Math.round(Math.min(chatMax(), Math.max(CHAT_MIN, w)));

export default function ProjectPage() {
  const { projectId = '' } = useParams();
  const location = useLocation();
  // Series workspace: this project's chat orchestrates; tabs switch the preview/timeline between its shorts.
  const [root, setRoot] = useState<ProjectSummary | null>(null);
  const [children, setChildren] = useState<SeriesChild[]>([]);
  const [view, setView] = useState<string>('self'); // 'self' | 'overview' | <child projectId>
  const viewId = view !== 'self' && view !== 'overview' ? view : projectId;
  const viewIdRef = useRef(viewId);
  viewIdRef.current = viewId;
  useEffect(() => { setView('self'); }, [projectId]);
  const { me, notify } = useApp();
  const { t } = useI18n();
  const navigate = useNavigate();
  const [project, setProject] = useState<ProjectSummary | null>(null);
  const projectRef = useRef<ProjectSummary | null>(null);
  projectRef.current = project;
  const agentDefaults = useRef<{ model: string; effort: string } | null>(null);
  const [agentModels, setAgentModels] = useState<{ id: string; label: string; tier: string }[]>([]);
  const [agentEfforts, setAgentEfforts] = useState<string[]>([]);
  useEffect(() => {
    api.agentModels().then((r) => { agentDefaults.current = r.default; setAgentModels(r.models); setAgentEfforts(r.efforts); }).catch(() => undefined);
  }, []);
  const setAgent = async (patch: { agentModel?: string; agentEffort?: string }) => {
    if (!project) return;
    try {
      await api.updateProject(projectId, patch);
      setRoot((r) => (r ? { ...r, ...patch } : r));
      if (viewIdRef.current === projectId) setProject({ ...project, ...patch });
      notify('success', t('agentSettingsSaved'));
    } catch (err) { notify('error', (err as Error).message); }
  };
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
  const [playing, setPlaying] = useState(false);
  const [folderPath, setFolderPath] = useState<{ folderId: string; name: string }[]>([]);
  useEffect(() => {
    const fid = (project as { folderId?: string | null } | null)?.folderId;
    if (!fid) { setFolderPath([]); return; }
    api.folders().then((fs) => {
      const out: { folderId: string; name: string }[] = [];
      let cur = fs.find((f) => f.folderId === fid);
      while (cur) { out.unshift(cur); cur = fs.find((f) => f.folderId === cur!.parentId); }
      setFolderPath(out);
    }).catch(() => undefined);
  }, [(project as { folderId?: string | null } | null)?.folderId]); // eslint-disable-line react-hooks/exhaustive-deps
  const [hoverItem, setHoverItem] = useState<string | undefined>();
  const [helpOpen, setHelpOpen] = useState(false);
  const [pending, setPending] = useState<Record<string, { name: string; status: string; pct: number }>>({});
  const clipboard = useRef<{ item: Item; trackId: string } | null>(null);
  const [hasClip, setHasClip] = useState(false);

  const mediaRef = useRef<Set<string>>(new Set());
  const ensureMedia = useCallback(async (ids: string[]) => {
    const missing = [...new Set(ids)].filter((id) => id && !mediaRef.current.has(id));
    if (!missing.length) return;
    missing.forEach((id) => mediaRef.current.add(id));
    const results = await Promise.all(missing.map(async (id) => {
      try {
        const [m, a] = await Promise.all([api.media(id), api.asset(id)]);
        return [id, m, a.displayTitle, a.thumbnailUrl] as const;
      } catch { return [id, undefined, id, undefined] as const; }
    }));
    setMedia((x) => ({ ...x, ...Object.fromEntries(results.filter((r) => r[1]).map((r) => [r[0], r[1]!])) }));
    setTitles((x) => ({ ...x, ...Object.fromEntries(results.map((r) => [r[0], r[2]])) }));
    setThumbs((x) => ({ ...x, ...Object.fromEntries(results.map((r) => [r[0], r[3]])) }));
  }, []);

  const loadRoot = useCallback(async () => {
    const r = await api.project(projectId);
    setRoot(r.project);
    if (r.project.childIds?.length) {
      setChildren(await api.children(projectId));
      setView((v) => (v === 'self' ? 'overview' : v));
    } else setChildren([]);
    return r;
  }, [projectId]);
  useEffect(() => { loadRoot().catch(() => undefined); }, [loadRoot]);
  useEffect(() => { // covers for the overview cards
    const ids = children.map((c) => c.coverAssetId).filter((x): x is string => !!x);
    if (ids.length) ensureMedia(ids);
  }, [children]); // eslint-disable-line react-hooks/exhaustive-deps

  const loadProject = useCallback(async () => {
    const r = await api.project(viewId);
    setProject(r.project);
    setTimeline(r.timeline);
    const ids = new Set<string>(r.project.assetIds ?? []);
    r.timeline.tracks.forEach((tr) => tr.items.forEach((i) => { if (i.type === 'clip') ids.add(i.assetId); }));
    ensureMedia([...ids]);
  }, [viewId, ensureMedia]);

  useEffect(() => { setSelected(undefined); loadProject().catch((e) => notify('error', e.message)); }, [viewId]); // eslint-disable-line react-hooks/exhaustive-deps
  // Initial load + chat history.
  useEffect(() => {
    api.chat(projectId).then((c) => {
      setEntries(fromHistory(c.messages, c.widgets)); setBusy(c.busy);
      ensureMedia((c.widgets ?? []).flatMap((w) => w.spec ? widgetAssetIds(w.spec) : (w.question?.clips ?? []).map((x) => x.assetId)));
    }).catch(() => undefined);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [projectId]);

  // Realtime.
  const onEvent = useCallback((e: AgentEvent) => {
    switch (e.type) {
      case 'turn.started':
        setBusy(true);
        setEntries((x) => x.some((m) => m.pending) ? x : [...x, { id: e.turn, role: 'assistant', text: '', pending: true, tools: [], steps: [],
          model: projectRef.current?.agentModel ?? agentDefaults.current?.model, effort: projectRef.current?.agentEffort ?? agentDefaults.current?.effort }]);
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
          const steps = [...(m.steps ?? [])];
          const j = steps.map((st) => st.kind === 'tool' && st.name === e.name && st.status === 'in_progress').lastIndexOf(true);
          const step = { kind: 'tool' as const, name: e.name, status: e.status, summary: e.summary || (j >= 0 ? steps[j].summary : undefined),
            detail: e.detail, ms: e.ms, error: e.error };
          if (j >= 0 && e.status !== 'in_progress') steps[j] = step; else steps.push(step);
          return { ...m, steps, tools: steps.filter((st) => st.kind === 'tool').map((st) => ({ name: st.name!, status: st.status! })) };
        }));
        break;
      case 'thought':
        setEntries((x) => x.map((m) => (m.pending ? { ...m, steps: [...(m.steps ?? []), { kind: 'thought' as const, text: e.text }] } : m)));
        break;
      case 'question':
        setEntries((x) => x.map((m) => (m.pending ? { ...m, question: e } : m)));
        ensureMedia((e.clips ?? []).map((c) => c.assetId));
        break;
      case 'ui':
        setEntries((x) => {
          const w = { uiId: e.uiId, spec: e.spec };
          const i = x.map((m) => m.pending ? 1 : 0).lastIndexOf(1);
          if (i >= 0) return x.map((m, j) => j === i ? { ...m, widgets: [...(m.widgets ?? []).filter((y) => y.uiId !== e.uiId), w] } : m);
          return [...x, { id: e.uiId, role: 'assistant', text: '', widgets: [w] }];
        });
        ensureMedia(widgetAssetIds(e.spec));
        break;
      case 'download': {
        const ev = e as unknown as { url: string; filename: string };
        const a = document.createElement('a');
        a.href = ev.url; a.download = ev.filename; a.rel = 'noopener'; a.click();
        notify('success', `${t('downloadReady')}: ${ev.filename}`);
        break;
      }
      case 'batch.created': {
        const ev = e as unknown as { batchId: string; count: number };
        notify('success', `${t('seriesCreated', { n: ev.count })} → /batches/${ev.batchId}`);
        window.open(`/batches/${ev.batchId}`, '_blank', 'noopener');
        break;
      }
      case 'render.requested':
        setRenderKey((k) => k + 1);
        setRendersOpen(true);
        break;
      case 'timeline.updated': {
        const pid = (e as { projectId?: string }).projectId;
        if (!pid || pid === viewIdRef.current) loadProject().catch(() => undefined);
        if (pid && pid !== projectId) api.children(projectId).then(setChildren).catch(() => undefined);
        break;
      }
      case 'series.updated':
        loadRoot().catch(() => undefined);
        break;
      case 'turn.completed':
      case 'turn.failed':
        setBusy(false);
        setEntries((x) => x.map((m) => (m.pending ? { ...m, pending: false, error: e.type === 'turn.failed' ? (e.error ?? 'failed') : undefined } : m)));
        loadProject().catch(() => undefined);
        // The saved transcript is authoritative (streamed deltas can be lost or repeated on reconnects).
        api.chat(projectId).then((c) => {
          setEntries(fromHistory(c.messages, c.widgets));
          ensureMedia((c.widgets ?? []).flatMap((w) => w.spec ? widgetAssetIds(w.spec) : (w.question?.clips ?? []).map((x) => x.assetId)));
        }).catch(() => undefined);
        break;
    }
  }, [loadProject, ensureMedia]);

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
      const r = await api.applyOps(viewId, ops, before, message);
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
  }, [project, busy, viewId, notify, selected, ensureMedia, loadProject]);

  const restore = useCallback(async (v: number, push: 'undo' | 'redo' | 'none' = 'none') => {
    if (!project) return;
    const cur = project.currentVersion;
    await api.restoreVersion(viewId, v);
    if (push === 'undo') redoStack.current.push(cur); else if (push === 'redo') undoStack.current.push(cur); else undoStack.current.push(cur);
    await loadProject();
  }, [project, viewId, loadProject]);
  const undo = useCallback(() => { const v = undoStack.current.pop(); if (v != null) restore(v, 'undo'); }, [restore]);
  const redo = useCallback(() => { const v = redoStack.current.pop(); if (v != null) restore(v, 'redo'); }, [restore]);

  const applyRef = useRef(applyOps);
  applyRef.current = applyOps;

  /** Ops that recreate `item` on `trackId` — after the original (duplicate) or at timeline time `at` (paste). */
  const cloneOps = useCallback((item: Item, trackId: string, at?: number): EditOp[] | null => {
    if (!timeline) return null;
    const tr = timeline.tracks.find((x) => x.id === trackId);
    if (!tr) return null;
    const indexAt = (tm: number) => tr.items.filter((x) => x.start + x.duration / 2 < tm).length;
    const place = tr.kind === 'video'
      ? { index: at == null ? tr.items.findIndex((x) => x.id === item.id) + 1 : indexAt(at) }
      : { start: +(at ?? item.start + item.duration).toFixed(3) };
    if (item.type === 'clip') {
      const keep = Object.fromEntries(['transform', 'volume', 'fadeIn', 'fadeOut', 'transitionIn', 'transitionOut', 'effects', 'reframe', 'opacity', 'kenBurns', 'page']
        .filter((k) => (item as unknown as Record<string, unknown>)[k] != null).map((k) => [k, (item as unknown as Record<string, unknown>)[k]]));
      return [{ op: 'add_clip', assetId: item.assetId, track: trackId, srcIn: item.srcIn, srcOut: item.srcOut, speed: item.speed ?? 1, duration: item.duration, ...keep, ...place }];
    }
    if (item.type === 'text') {
      return [{ op: 'add_text', track: trackId, text: item.text, role: item.role, style: item.style, position: item.position,
        animation: item.animation, duration: item.duration, start: place.start ?? item.start + item.duration }];
    }
    return null;
  }, [timeline]);

  const itemAction = useCallback((action: ItemAction, item: Item, trackId: string) => {
    if (action === 'copy') { clipboard.current = { item, trackId }; setHasClip(true); notify('info', t('copied')); return; }
    if (action === 'paste') {
      const c = clipboard.current;
      const ops = c && cloneOps(c.item, c.trackId, time);
      if (ops) applyOps(ops, 'paste'); else if (c) notify('warning', t('cannotCopy'));
      return;
    }
    if (action === 'duplicate') { const ops = cloneOps(item, trackId); if (ops) applyOps(ops, 'duplicate'); else notify('warning', t('cannotCopy')); return; }
    if (action === 'delete') { applyOps([{ op: 'remove', itemId: item.id }], 'delete'); return; }
    if (action === 'split') { applyOps([{ op: 'split', itemId: item.id, at: time }], 'split'); return; }
    if (action === 'pin') {
      const on = timeline?.pins.some((p) => p.split('/')[1] === item.id);
      applyOps([{ op: 'pin', path: `items/${item.id}`, on: !on }], on ? 'unpin' : 'pin');
    }
  }, [cloneOps, applyOps, time, timeline, notify, t]);

  /** Place an archive asset where it was dropped. */
  const dropAsset = useCallback((a: DragAsset, at: DropTarget) => {
    const dur = a.durationSec ?? 0;
    let op: EditOp;
    if (a.kind === 'audio' || (at.trackKind === 'audio' && a.kind === 'video')) {
      const track = at.trackKind === 'audio' ? at.trackId : 'a1';
      op = { op: 'add_clip', assetId: a.assetId, track, start: at.trackKind === 'audio' ? at.time : time, srcIn: 0, srcOut: dur || 30 };
    } else if (at.trackKind === 'audio') {
      notify('warning', t('audioOnlyTrack')); return;
    } else if (at.trackKind === 'video') {
      op = { op: 'add_clip', assetId: a.assetId, index: at.index, srcIn: 0, srcOut: Math.min(dur || 5, 10), duration: 3 };
    } else {
      const track = at.trackKind === 'overlay' ? at.trackId : 'o1';
      op = { op: 'add_clip', assetId: a.assetId, track, start: at.time, srcIn: 0, srcOut: Math.min(dur || 3, 3), duration: 3 };
    }
    applyRef.current([op], 'drop');
    api.attachAssets(viewId, [a.assetId]).catch(() => undefined);
  }, [viewId, time, notify, t]);

  /** Files from the desktop: upload → wait for analysis → place where they were dropped. */
  const dropFiles = useCallback((files: File[], at: DropTarget) => {
    files.forEach((file, k) => {
      const key = `${file.name}-${file.size}-${Date.now()}-${k}`;
      const set = (v: { status: string; pct: number } | null) => setPending((x) => {
        const n = { ...x };
        if (v) n[key] = { name: file.name, ...v }; else delete n[key];
        return n;
      });
      set({ status: 'uploading', pct: 0 });
      (async () => {
        try {
          const id = await uploadFile(file, (pr) => set({ status: 'uploading', pct: Math.round((pr.loaded / Math.max(1, pr.total)) * 100) }), { scope: 'project', projectId: viewId });
          set({ status: 'processing', pct: 100 });
          api.attachAssets(viewId, [id]).catch(() => undefined);
          for (let i = 0; i < 360; i++) {
            const a = await api.asset(id);
            if (a.status === 'ready') {
              set(null);
              dropAsset({ assetId: id, kind: a.kind, durationSec: a.durationSec }, { ...at, index: at.index + k });
              return;
            }
            if (a.status === 'failed' || a.status === 'aborted') throw new Error(a.error ?? a.status);
            set({ status: a.processing?.status ?? a.status, pct: 100 });
            await new Promise((r) => setTimeout(r, 4000));
          }
          throw new Error('timed out');
        } catch (e) {
          set(null);
          notify('error', `${file.name}: ${(e as Error).message}`);
        }
      })();
    });
  }, [viewId, dropAsset, notify]);
  const mainTarget = useCallback((): DropTarget => {
    const main = timeline?.tracks.find((x) => x.kind === 'video');
    return { trackId: main?.id ?? 'v1', trackKind: 'video', time: 0, index: main?.items.length ?? 0 };
  }, [timeline]);

  // Keyboard shortcuts (playback everywhere; editing in manual mode). `?` lists them.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const el = e.target as HTMLElement;
      if (el?.tagName === 'INPUT' || el?.tagName === 'TEXTAREA' || el?.isContentEditable || el?.closest?.('[role=dialog]')) return;
      const mod = e.metaKey || e.ctrlKey;
      const dur = timeline ? timelineDuration(timeline) : 0;
      if (e.key === '?') { e.preventDefault(); setHelpOpen(true); return; }
      if (e.key === ' ' || e.key.toLowerCase() === 'k') { e.preventDefault(); setPlaying((p) => !p); return; }
      if (e.key === 'ArrowLeft' || e.key === 'ArrowRight') {
        e.preventDefault(); setPlaying(false);
        const step = (e.shiftKey ? 1 : 1 / 30) * (e.key === 'ArrowLeft' ? -1 : 1);
        setTime((x) => Math.min(dur, Math.max(0, +(x + step).toFixed(3)))); return;
      }
      if (e.key.toLowerCase() === 'j' || e.key.toLowerCase() === 'l') { setPlaying(false); setTime((x) => Math.min(dur, Math.max(0, x + (e.key.toLowerCase() === 'j' ? -5 : 5)))); return; }
      if (e.key === 'Home') { e.preventDefault(); setTime(0); return; }
      if (e.key === 'End') { e.preventDefault(); setTime(dur); return; }
      if (e.key === 'Escape') { setSelected(undefined); return; }
      if (mode !== 'manual' || busy) return;
      if (mod && e.key.toLowerCase() === 'z') { e.preventDefault(); if (e.shiftKey) redo(); else undo(); return; }
      if (mod && e.key.toLowerCase() === 'v') { e.preventDefault(); if (clipboard.current) itemAction('paste', clipboard.current.item, clipboard.current.trackId); return; }
      if (!selected) return;
      const tid = selected.trackId ?? '';
      if (mod && e.key.toLowerCase() === 'c') { e.preventDefault(); itemAction('copy', selected.item, tid); return; }
      if (mod && e.key.toLowerCase() === 'd') { e.preventDefault(); itemAction('duplicate', selected.item, tid); return; }
      if (e.key === 'Delete' || e.key === 'Backspace') { e.preventDefault(); itemAction('delete', selected.item, tid); return; }
      if (!mod && e.key.toLowerCase() === 's') { e.preventDefault(); itemAction('split', selected.item, tid); }
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [mode, busy, selected, timeline, itemAction, undo, redo]);

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
  const hp = root ?? project; // the series (or plain) project the chat belongs to
  const isSeries = !!root?.childIds?.length;

  const selLabel = selected ? (selected.item.type === 'clip' ? `${titles[selected.item.assetId] ?? ''} ${fmtTime(selected.item.start)}` : selected.item.type) : undefined;
  const hasEdit = timeline.tracks.some((tr) => tr.items.length > 0);
  const suggestions = hasEdit ? [t('sugShorter'), t('sugCaptions'), t('sugMusic'), t('sugLandscape')] : [];
  const divider = `1px solid ${awsui.colorBorderDividerDefault}`;

  return (
    <div className="studio-split" style={{ ['--chat-w' as string]: `${chatW}px` }}>
      <Modal visible={helpOpen} onDismiss={() => setHelpOpen(false)} header={t('shortcuts')}>
        <SpaceBetween size="xs">
          {SHORTCUTS.map(([keys, label]) => (
            <div key={label} style={{ display: 'flex', justifyContent: 'space-between', gap: 16 }}>
              <span>{t(label as never)}</span>
              <span>{keys.split(' ').map((k) => <span key={k} className="kbd" style={{ marginLeft: 4 }}>{k}</span>)}</span>
            </div>
          ))}
          <Box variant="small" color="text-body-secondary">{t('shortcutsNote')}</Box>
        </SpaceBetween>
      </Modal>
      {rendersOpen && <RendersModal projectId={viewId} refreshKey={renderKey} onClose={() => setRendersOpen(false)} />}
      {/* Left: canvas (preview) + docked timeline */}
      <section className="studio-canvas-pane">
        <div className="studio-toolbar" style={{ borderBottom: divider }}>
          <SpaceBetween direction="horizontal" size="s" alignItems="center">
            <BreadcrumbGroup items={[{ text: t('navProjects'), href: '/projects' }, ...folderPath.map((f) => ({ text: f.name, href: '/projects' })),
              ...(root?.parentId ? [{ text: t('series'), href: `/studio/${root.parentId}` }] : []),
              { text: hp.title, href: '#' }, ...(isSeries && view !== 'overview' && view !== 'self' ? [{ text: project.title, href: '#' }] : [])]}
              onFollow={(e) => { e.preventDefault(); if (e.detail.href !== '#') navigate(e.detail.href); }} ariaLabel="breadcrumbs" />
          </SpaceBetween>
          <SpaceBetween direction="horizontal" size="xs" alignItems="center">
            <SegmentedControl selectedId={mode} onChange={(e) => setMode(e.detail.selectedId as 'ai' | 'manual')}
              options={[{ id: 'ai', text: t('modeAi'), iconName: 'gen-ai' }, { id: 'manual', text: t('modeManual'), iconName: 'edit' }]} />
            {mode === 'manual' && <>
              <Button variant="icon" iconName="undo" ariaLabel="undo" disabled={!undoStack.current.length || saving} onClick={undo} />
              <Button variant="icon" iconName="redo" ariaLabel="redo" disabled={!redoStack.current.length || saving} onClick={redo} />
            </>}
            <Button variant="icon" iconName="keyboard" ariaLabel={t('shortcuts')} onClick={() => setHelpOpen(true)} />
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
                  await api.requestRender(viewId, detail.id); setRenderKey((k) => k + 1); setRendersOpen(true);
                } else if (detail.id === 'list') setRendersOpen(true);
                else {
                  const r = await api.exportProject(viewId, detail.id as 'otio' | 'fcpxml' | 'edl');
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
        {isSeries && (
          <div className="series-tabs" role="tablist">
            <button type="button" role="tab" aria-selected={view === 'overview'} className={view === 'overview' ? 'on' : ''} onClick={() => setView('overview')}>
              ▦ {t('seriesOverview')} <span className="series-count">{children.length}</span>
            </button>
            {children.map((c, i) => (
              <button key={c.projectId} type="button" role="tab" aria-selected={view === c.projectId} className={view === c.projectId ? 'on' : ''}
                onClick={() => setView(c.projectId)} title={c.topic ?? c.title}>
                <span className="series-n">{i + 1}</span> {(c.topic || c.title || '').slice(0, 24)}
                {c.busy ? <span className="series-dot busy" /> : c.version ? <span className="series-dot" /> : null}
              </button>
            ))}
          </div>
        )}
        {isSeries && view === 'overview' ? (
          <div className="series-overview">
            <Box variant="p" color="text-body-secondary">{t('seriesHint')}</Box>
            <div className="series-grid">
              {children.map((c, i) => {
                const m = c.coverAssetId ? media[c.coverAssetId] : undefined;
                return (
                  <button key={c.projectId} type="button" className="series-card" onClick={() => setView(c.projectId)}>
                    <div className="series-cover">
                      {m?.sprites ? <SpriteFrame m={m} t={c.coverAt ?? c.sourceRange?.[0] ?? 0} style={{ position: 'absolute', inset: 0, width: '100%', height: '100%', aspectRatio: 'auto' }} />
                        : <span>{c.version ? '' : t('seriesNotBuilt')}</span>}
                      <span className="series-badge">#{i + 1}{c.durationSec ? ` · ${fmtTime(c.durationSec)}` : ''}</span>
                      {c.busy && <span className="series-busy"><Spinner /> {t('agentWorking')}</span>}
                    </div>
                    <span className="series-title">{c.topic || c.title}</span>
                    <span className="series-meta">{c.sourceRange ? `${t('sourceRange')} ${fmtTime(c.sourceRange[0])}–${fmtTime(c.sourceRange[1])}` : ''} · v{c.version ?? 0}</span>
                  </button>
                );
              })}
            </div>
          </div>
        ) : (<>
        <div className="studio-stage">
          <Preview timeline={timeline} media={media} time={time} onTime={setTime} selectedId={selected?.item.id}
            playing={playing} onPlayingChange={setPlaying} prefetchId={hoverItem}
            editable={!busy && !saving} onOps={applyOps} onSelect={(item, trackId) => setSelected(item ? { item, trackId } : undefined)}
            onDropFiles={busy ? undefined : (f) => dropFiles(f, mainTarget())}
            onDropAsset={busy ? undefined : (raw) => { try { dropAsset(JSON.parse(raw) as DragAsset, mainTarget()); } catch { /* ignore */ } }} />
        </div>
        <div className="studio-timeline" style={{ borderTop: divider, background: awsui.colorBackgroundContainerContent }}>
          <ExpandableSection defaultExpanded variant="footer" headerText={t('timeline')}>
            {Object.keys(pending).length > 0 && (
              <SpaceBetween direction="horizontal" size="s">
                {Object.entries(pending).map(([k, v]) => (
                  <StatusIndicator key={k} type="loading">{v.name} · {v.status === 'uploading' ? `${t('uploading')} ${v.pct}%` : t('analyzingThenAdd')}</StatusIndicator>
                ))}
              </SpaceBetween>
            )}
            <TimelineStrip timeline={timeline} time={time} onSeek={(x) => { setPlaying(false); setTime(x); }} selectedId={selected?.item.id}
              onSelect={(item, trackId) => setSelected(item ? { item, trackId } : undefined)} titles={titles} thumbs={thumbs}
              editable={mode === 'manual' && !busy} droppable={!busy} onOps={applyOps}
              onDropAsset={dropAsset} onDropFiles={dropFiles} onAction={itemAction} canPaste={hasClip} onHoverItem={setHoverItem} />
          </ExpandableSection>
        </div>
        </>)}
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
            <EditorPanel projectId={viewId} timeline={timeline} selected={selected?.item} time={time} titles={titles}
              onOps={applyOps} onRestore={(v) => restore(v)} version={project.currentVersion} disabled={busy || saving} />
          </>
        ) : <ChatPanel entries={entries} busy={busy} onSend={send} projectId={projectId} media={media} onError={(m) => notify('error', m)} onAnswer={answer} thumbs={thumbs} titles={titles}
          suggestions={suggestions} selectionLabel={selLabel} onClearSelection={() => setSelected(undefined)}
          header={
            <div style={{ padding: '14px 20px', borderBottom: divider }}>
              <div style={{ display: 'flex', alignItems: 'flex-start', gap: 8 }}>
                <div style={{ flex: 1, minWidth: 0 }}><Box variant="h3" padding="n">{hp.title}</Box></div>
                {agentModels.length > 0 && (
                  <ButtonDropdown expandToViewport variant="inline-icon" ariaLabel={t('agentSettings')} items={[
                    { id: 'm', text: t('agentModel'), items: agentModels.map((m) => ({ id: `model:${m.id}`, text: m.label,
                      description: t(`tier_${m.tier}` as never), checked: (hp.agentModel ?? agentDefaults.current?.model) === m.id, itemType: 'checkbox' as const })) },
                    { id: 'e', text: t('agentEffort'), items: agentEfforts.map((ef) => ({ id: `effort:${ef}`, text: t(`effort_${ef}` as never),
                      checked: (hp.agentEffort ?? agentDefaults.current?.effort) === ef, itemType: 'checkbox' as const })) },
                  ]} onItemClick={({ detail }) => {
                    const [k, v] = detail.id.split(':');
                    setAgent(k === 'model' ? { agentModel: v } : { agentEffort: v });
                  }} />
                )}
              </div>
              <Box variant="small" color="text-body-secondary">AI · {modelLabel(hp.agentModel ?? agentDefaults.current?.model)} · {t(`effort_${hp.agentEffort ?? agentDefaults.current?.effort ?? 'high'}` as never)}</Box>
              {hp.synopsis && hp.synopsis !== hp.title && <Box variant="small" color="text-body-secondary">{hp.synopsis}</Box>}
            </div>
          } />}
      </aside>
    </div>
  );
}
