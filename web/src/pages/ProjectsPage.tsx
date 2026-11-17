import Badge from '@cloudscape-design/components/badge';
import Box from '@cloudscape-design/components/box';
import Button from '@cloudscape-design/components/button';
import ButtonDropdown from '@cloudscape-design/components/button-dropdown';
import FormField from '@cloudscape-design/components/form-field';
import Header from '@cloudscape-design/components/header';
import Input from '@cloudscape-design/components/input';
import Link from '@cloudscape-design/components/link';
import Modal from '@cloudscape-design/components/modal';
import SegmentedControl from '@cloudscape-design/components/segmented-control';
import Select from '@cloudscape-design/components/select';
import SpaceBetween from '@cloudscape-design/components/space-between';
import StatusIndicator from '@cloudscape-design/components/status-indicator';
import Table from '@cloudscape-design/components/table';
import Tabs from '@cloudscape-design/components/tabs';
import TextFilter from '@cloudscape-design/components/text-filter';
import * as awsui from '@cloudscape-design/design-tokens';
import { DragEvent, ReactElement, useCallback, useEffect, useMemo, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { useApp } from '../App';
import { api } from '../api/client';
import type { AssetMedia, BatchInfo, ChatMessage, Folder, ProjectSummary, RenderInfo } from '../api/types';
import SpriteFrame from '../components/SpriteFrame';
import { useI18n } from '../i18n';
import { fmtDate, fmtTime } from '../util';

type Scope = { kind: 'all' } | { kind: 'starred' } | { kind: 'archived' } | { kind: 'unfiled' } | { kind: 'folder'; id: string } | { kind: 'batch'; id: string };
const PROJECT_MIME = 'application/x-shorts-project';

function rel(ms: number | undefined, lang: string): string {
  if (!ms) return '';
  const d = (Date.now() - ms) / 1000;
  const rtf = new Intl.RelativeTimeFormat(lang, { numeric: 'auto' });
  if (d < 60) return rtf.format(-Math.round(d), 'second');
  if (d < 3600) return rtf.format(-Math.round(d / 60), 'minute');
  if (d < 86400) return rtf.format(-Math.round(d / 3600), 'hour');
  if (d < 86400 * 30) return rtf.format(-Math.round(d / 86400), 'day');
  return fmtDate(ms);
}

/** All projects as a hierarchy: folders (nestable), starred, batches, archive — with versions/renders per project. */
export default function ProjectsPage() {
  const { t, lang } = useI18n();
  const { notify } = useApp();
  const navigate = useNavigate();
  const [items, setItems] = useState<ProjectSummary[]>([]);
  const [folders, setFolders] = useState<Folder[]>([]);
  const [batches, setBatches] = useState<BatchInfo[]>([]);
  const [loading, setLoading] = useState(true);
  const [scope, setScope] = useState<Scope>({ kind: 'all' });
  const [filter, setFilter] = useState('');
  const [sort, setSort] = useState<'updated' | 'created' | 'title'>('updated');
  const [view, setView] = useState<'cards' | 'table'>('cards');
  const [selected, setSelected] = useState<ProjectSummary | null>(null);
  const [open, setOpen] = useState<Record<string, boolean>>({});
  const [dropOn, setDropOn] = useState<string | null>(null);
  const [nameDialog, setNameDialog] = useState<{ title: string; value: string; onOk: (v: string) => void } | null>(null);
  const [moveDialog, setMoveDialog] = useState<ProjectSummary | null>(null);
  const [media, setMedia] = useState<Record<string, AssetMedia>>({});
  // Each project's own first frame (from the asset's sprite sheet at the clip's in-point), not the asset thumbnail.
  useEffect(() => {
    const ids = [...new Set(items.map((p) => (p as ProjectSummary & { coverAssetId?: string }).coverAssetId).filter((x): x is string => !!x))].filter((id) => !media[id]);
    if (!ids.length) return;
    Promise.all(ids.map((id) => api.media(id).then((m) => [id, m] as const).catch(() => null))).then((rs) => {
      setMedia((cur) => ({ ...cur, ...Object.fromEntries(rs.filter((r): r is readonly [string, AssetMedia] => !!r)) }));
    });
  }, [items]); // eslint-disable-line react-hooks/exhaustive-deps

  const load = useCallback(async () => {
    try {
      const [p, f, b] = await Promise.all([api.projects(), api.folders(), api.batches().catch(() => [] as BatchInfo[])]);
      setItems(p); setFolders(f); setBatches(b);
      setSelected((s) => (s ? p.find((x) => x.projectId === s.projectId) ?? null : s));
    } catch (e) { notify('error', (e as Error).message); } finally { setLoading(false); }
  }, [notify]);
  useEffect(() => { load(); }, [load]);

  const children = useMemo(() => {
    const m = new Map<string | null, Folder[]>();
    for (const f of folders) { const k = f.parentId ?? null; m.set(k, [...(m.get(k) ?? []), f]); }
    for (const v of m.values()) v.sort((a, b) => a.name.localeCompare(b.name));
    return m;
  }, [folders]);
  const descendants = useCallback((id: string): Set<string> => {
    const out = new Set([id]);
    const walk = (x: string) => (children.get(x) ?? []).forEach((c) => { out.add(c.folderId); walk(c.folderId); });
    walk(id);
    return out;
  }, [children]);
  const pathOf = useCallback((id?: string | null): Folder[] => {
    const out: Folder[] = [];
    let cur = folders.find((f) => f.folderId === id);
    while (cur) { out.unshift(cur); cur = folders.find((f) => f.folderId === cur!.parentId); }
    return out;
  }, [folders]);

  const visible = useMemo(() => {
    const q = filter.trim().toLowerCase();
    let xs = items.filter((p) => (scope.kind === 'archived' ? p.archived : !p.archived));
    if (scope.kind === 'starred') xs = xs.filter((p) => p.starred);
    if (scope.kind === 'unfiled') xs = xs.filter((p) => !p.folderId);
    if (scope.kind === 'folder') { const ids = descendants(scope.id); xs = xs.filter((p) => p.folderId && ids.has(p.folderId)); }
    if (scope.kind === 'batch') xs = xs.filter((p) => p.batchId === scope.id);
    if (q) xs = xs.filter((p) => [p.title, p.synopsis, p.lastMessage].some((s) => s?.toLowerCase().includes(q)));
    const key = sort === 'title' ? (p: ProjectSummary) => p.title.toLowerCase() : sort === 'created' ? (p: ProjectSummary) => -p.createdAt : (p: ProjectSummary) => -(p.updatedAt ?? 0);
    return [...xs].sort((a, b) => (key(a) < key(b) ? -1 : key(a) > key(b) ? 1 : 0));
  }, [items, scope, filter, sort, descendants]);

  const count = (pred: (p: ProjectSummary) => boolean) => items.filter((p) => !p.archived && pred(p)).length;
  const patch = async (p: ProjectSummary, b: Record<string, unknown>) => {
    try { await api.updateProject(p.projectId, b); await load(); } catch (e) { notify('error', (e as Error).message); }
  };
  const moveTo = (projectId: string, folderId: string | null) => {
    const p = items.find((x) => x.projectId === projectId);
    if (p && (p.folderId ?? null) !== folderId) patch(p, { folderId });
  };
  const newFolder = (parentId: string | null) => setNameDialog({
    title: t('newFolder'), value: '', onOk: async (name) => {
      try { await api.createFolder({ name, parentId }); if (parentId) setOpen((o) => ({ ...o, [parentId]: true })); await load(); }
      catch (e) { notify('error', (e as Error).message); }
    },
  });
  const projectAction = async (id: string, p: ProjectSummary) => {
    if (id === 'open') navigate(`/studio/${p.projectId}`);
    if (id === 'rename') setNameDialog({ title: t('rename'), value: p.title, onOk: (title) => patch(p, { title }) });
    if (id === 'move') setMoveDialog(p);
    if (id === 'star') patch(p, { starred: !p.starred });
    if (id === 'archive') patch(p, { archived: !p.archived });
    if (id === 'duplicate') {
      try { const r = await api.duplicateProject(p.projectId); notify('success', t('duplicated')); await load(); navigate(`/studio/${r.projectId}`); }
      catch (e) { notify('error', (e as Error).message); }
    }
  };
  const actions = (p: ProjectSummary) => [
    { id: 'open', text: t('open') }, { id: 'rename', text: t('rename') }, { id: 'move', text: t('moveToFolder') },
    { id: 'duplicate', text: t('duplicateProject') }, { id: 'star', text: p.starred ? t('unstar') : t('star') },
    { id: 'archive', text: p.archived ? t('unarchive') : t('archiveProject') },
  ];

  // ---------------------------------------------------------------- sidebar tree
  const dropProps = (key: string, folderId: string | null) => ({
    onDragOver: (e: DragEvent) => { if (e.dataTransfer.types.includes(PROJECT_MIME)) { e.preventDefault(); setDropOn(key); } },
    onDragLeave: () => setDropOn((d) => (d === key ? null : d)),
    onDrop: (e: DragEvent) => { e.preventDefault(); setDropOn(null); const id = e.dataTransfer.getData(PROJECT_MIME); if (id) moveTo(id, folderId); },
  });
  const Row = ({ label, k, active, onClick, depth = 0, n, folder, toggle }: { label: string; k: string; active: boolean; onClick: () => void; depth?: number; n?: number; folder?: Folder; toggle?: boolean }) => (
    <div className={`pj-row${active ? ' on' : ''}${dropOn === k ? ' drop' : ''}`} style={{ paddingLeft: 8 + depth * 14 }}
      {...(folder || k === 'unfiled' ? dropProps(k, folder?.folderId ?? null) : {})}>
      {toggle !== undefined ? (
        <button type="button" className="pj-caret" onClick={() => folder && setOpen((o) => ({ ...o, [folder.folderId]: !o[folder.folderId] }))}>{toggle ? '▾' : '▸'}</button>
      ) : <span className="pj-caret" />}
      <button type="button" className="pj-label" onClick={onClick}>{label}</button>
      {n != null && <span className="pj-count">{n}</span>}
      {folder && (
        <ButtonDropdown variant="inline-icon" ariaLabel={t('folderMenu')} expandToViewport items={[
          { id: 'sub', text: t('newSubfolder') }, { id: 'rename', text: t('rename') }, { id: 'delete', text: t('deleteFolder') }]}
          onItemClick={async ({ detail }) => {
            if (detail.id === 'sub') newFolder(folder.folderId);
            if (detail.id === 'rename') setNameDialog({ title: t('rename'), value: folder.name, onOk: async (name) => { await api.updateFolder(folder.folderId, { name }); load(); } });
            if (detail.id === 'delete') { await api.deleteFolder(folder.folderId); if (scope.kind === 'folder' && scope.id === folder.folderId) setScope({ kind: 'all' }); load(); }
          }} />
      )}
    </div>
  );
  const tree = (parent: string | null, depth: number): ReactElement[] => (children.get(parent) ?? []).flatMap((f) => {
    const kids = children.get(f.folderId) ?? [];
    const isOpen = open[f.folderId] ?? depth < 1;
    const ids = descendants(f.folderId);
    return [
      <Row key={f.folderId} k={f.folderId} label={`📁 ${f.name}`} depth={depth} folder={f} toggle={kids.length ? isOpen : undefined}
        n={count((p) => !!p.folderId && ids.has(p.folderId))} active={scope.kind === 'folder' && scope.id === f.folderId}
        onClick={() => setScope({ kind: 'folder', id: f.folderId })} />,
      ...(isOpen ? tree(f.folderId, depth + 1) : []),
    ];
  });
  const scopeTitle = scope.kind === 'folder' ? pathOf(scope.id).map((f) => f.name).join(' / ')
    : scope.kind === 'batch' ? `${t('batch')} · ${fmtDate(batches.find((b) => b.batchId === scope.id)?.createdAt ?? 0)}`
      : t(`scope_${scope.kind}` as never);

  // ---------------------------------------------------------------- main
  const card = (p: ProjectSummary) => {
    const path = pathOf(p.folderId);
    return (
      <div key={p.projectId} className={`pj-card${selected?.projectId === p.projectId ? ' on' : ''}`} draggable
        onDragStart={(e) => { e.dataTransfer.setData(PROJECT_MIME, p.projectId); e.dataTransfer.effectAllowed = 'move'; }}
        onClick={() => setSelected(p)} onDoubleClick={() => navigate(`/studio/${p.projectId}`)}>
        <div className="pj-cover" style={{ aspectRatio: '16 / 10' }}>
          {(() => {
            const cp = p as ProjectSummary & { coverAssetId?: string; coverAt?: number };
            const m = cp.coverAssetId ? media[cp.coverAssetId] : undefined;
            if (m?.sprites) return <SpriteFrame m={m} t={cp.coverAt ?? 0} style={{ position: 'absolute', inset: 0, width: '100%', height: '100%', aspectRatio: 'auto' }} />;
            return p.coverUrl ? <img src={p.coverUrl} alt="" loading="lazy" /> : <span>{p.currentVersion ? '' : t('emptyTimeline')}</span>;
          })()}
          <span className="pj-aspect">{p.aspect}</span>
          <span className="pj-dur">{p.durationSec ? fmtTime(p.durationSec) : '—'}</span>
          {p.starred && <span className="pj-star">★</span>}
        </div>
        <div style={{ padding: '8px 10px' }}>
          <div style={{ display: 'flex', gap: 6, alignItems: 'flex-start' }}>
            <Box fontWeight="bold" variant="small"><span className="pj-title">{p.title}</span></Box>
            <div style={{ marginLeft: 'auto' }} onClick={(e) => e.stopPropagation()}>
              <ButtonDropdown variant="inline-icon" ariaLabel={t('projectMenu')} expandToViewport items={actions(p)} onItemClick={({ detail }) => projectAction(detail.id, p)} />
            </div>
          </div>
          <Box variant="small" color="text-body-secondary">
            {p.aspect} · v{p.currentVersion} · {rel(p.updatedAt, lang)}{path.length ? ` · 📁 ${path.map((f) => f.name).join('/')}` : ''}
          </Box>
          {p.lastMessage && <div className="pj-last">{p.lastMessage}</div>}
          <SpaceBetween direction="horizontal" size="xxs">
            {p.batchId && <Badge color="blue">{t('batch')}</Badge>}
            {p.duplicatedFrom && <Badge>{t('copyBadge')}</Badge>}
            {p.recipe && <Badge color="grey">{p.recipe}</Badge>}
          </SpaceBetween>
        </div>
      </div>
    );
  };

  return (
    <div className="pj-layout">
      <aside className="pj-side" style={{ borderRight: `1px solid ${awsui.colorBorderDividerDefault}` }}>
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', padding: '4px 8px 8px' }}>
          <Box variant="h3" padding="n">{t('navProjects')}</Box>
          <Button variant="icon" iconName="add-plus" ariaLabel={t('newFolder')} onClick={() => newFolder(null)} />
        </div>
        <Row k="all" label={`🗂 ${t('scope_all')}`} n={count(() => true)} active={scope.kind === 'all'} onClick={() => setScope({ kind: 'all' })} />
        <Row k="starred" label={`★ ${t('scope_starred')}`} n={count((p) => !!p.starred)} active={scope.kind === 'starred'} onClick={() => setScope({ kind: 'starred' })} />
        <Row k="unfiled" label={`📄 ${t('scope_unfiled')}`} n={count((p) => !p.folderId)} active={scope.kind === 'unfiled'} onClick={() => setScope({ kind: 'unfiled' })} />
        <div className="pj-section">{t('folders')}</div>
        {tree(null, 0)}
        {!folders.length && <Box variant="small" color="text-body-secondary" padding={{ left: 's' }}>{t('noFolders')}</Box>}
        {batches.length > 0 && <div className="pj-section">{t('batches')}</div>}
        {batches.slice(0, 20).map((b) => (
          <Row key={b.batchId} k={b.batchId} label={`🎞 ${b.recipe ?? t('batch')} · ${fmtDate(b.createdAt).split(',')[0]}`} n={count((p) => p.batchId === b.batchId)}
            active={scope.kind === 'batch' && scope.id === b.batchId} onClick={() => setScope({ kind: 'batch', id: b.batchId })} />
        ))}
        <div className="pj-section" />
        <Row k="archived" label={`🗄 ${t('scope_archived')}`} n={items.filter((p) => p.archived).length} active={scope.kind === 'archived'} onClick={() => setScope({ kind: 'archived' })} />
      </aside>

      <main className="pj-main">
        <Header variant="h2" counter={`(${visible.length})`} description={t('projectsHint')}
          actions={<Button variant="primary" iconName="gen-ai" onClick={() => navigate('/')}>{t('newProject')}</Button>}>{scopeTitle}</Header>
        <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', alignItems: 'center', margin: '12px 0' }}>
          <div style={{ flex: '1 1 260px' }}><TextFilter filteringText={filter} filteringPlaceholder={t('searchProjects')} onChange={(e) => setFilter(e.detail.filteringText)} /></div>
          <Select expandToViewport selectedOption={{ value: sort, label: t(`sort_${sort}` as never) }} onChange={(e) => setSort(e.detail.selectedOption.value as typeof sort)}
            options={(['updated', 'created', 'title'] as const).map((v) => ({ value: v, label: t(`sort_${v}` as never) }))} />
          <SegmentedControl selectedId={view} onChange={(e) => setView(e.detail.selectedId as typeof view)}
            options={[{ id: 'cards', iconName: 'grid-view', iconAlt: 'cards' }, { id: 'table', iconName: 'list-view', iconAlt: 'table' }]} />
        </div>
        {loading ? <StatusIndicator type="loading">{t('loading')}</StatusIndicator> : view === 'cards' ? (
          visible.length ? <div className="pj-grid">{visible.map(card)}</div>
            : <Box textAlign="center" color="text-body-secondary" padding="xxl">{t('noProjectsHere')}</Box>
        ) : (
          <Table variant="embedded" items={visible} trackBy="projectId" selectionType="single" selectedItems={selected ? [selected] : []}
            onSelectionChange={(e) => setSelected(e.detail.selectedItems[0] ?? null)} sortingDisabled
            columnDefinitions={[
              { id: 'title', header: t('title'), cell: (p) => <Link onFollow={(e) => { e.preventDefault(); navigate(`/studio/${p.projectId}`); }}>{p.title}</Link> },
              { id: 'folder', header: t('folder'), cell: (p) => pathOf(p.folderId).map((f) => f.name).join(' / ') || '—' },
              { id: 'aspect', header: t('aspect'), cell: (p) => p.aspect },
              { id: 'dur', header: t('lengthSec'), cell: (p) => (p.durationSec ? fmtTime(p.durationSec) : '—') },
              { id: 'ver', header: 'v', cell: (p) => p.currentVersion },
              { id: 'upd', header: t('sort_updated'), cell: (p) => rel(p.updatedAt, lang) },
              { id: 'act', header: '', cell: (p) => <ButtonDropdown variant="inline-icon" ariaLabel={t('projectMenu')} expandToViewport items={actions(p)} onItemClick={({ detail }) => projectAction(detail.id, p)} /> },
            ]} />
        )}
      </main>

      {selected && <ProjectDetail p={selected} onClose={() => setSelected(null)} onChanged={load} folderPath={pathOf(selected.folderId).map((f) => f.name).join(' / ')}
        cover={selected.coverAssetId ? media[selected.coverAssetId] : undefined} />}

      <Modal visible={!!nameDialog} onDismiss={() => setNameDialog(null)} header={nameDialog?.title}
        footer={<Box float="right"><SpaceBetween direction="horizontal" size="xs">
          <Button variant="link" onClick={() => setNameDialog(null)}>{t('cancel')}</Button>
          <Button variant="primary" disabled={!nameDialog?.value.trim()} onClick={() => { nameDialog?.onOk(nameDialog.value.trim()); setNameDialog(null); }}>{t('save')}</Button>
        </SpaceBetween></Box>}>
        <Input autoFocus value={nameDialog?.value ?? ''} onChange={(e) => setNameDialog((d) => (d ? { ...d, value: e.detail.value } : d))}
          onKeyDown={(e) => { if (e.detail.key === 'Enter' && nameDialog?.value.trim()) { nameDialog.onOk(nameDialog.value.trim()); setNameDialog(null); } }} />
      </Modal>
      <Modal visible={!!moveDialog} onDismiss={() => setMoveDialog(null)} header={t('moveToFolder')}>
        <FormField label={t('folder')}>
          <Select expandToViewport filteringType="auto" selectedOption={null} placeholder={moveDialog?.title}
            options={[{ value: '', label: t('scope_unfiled') }, ...folders.map((f) => ({ value: f.folderId, label: pathOf(f.folderId).map((x) => x.name).join(' / ') }))]}
            onChange={(e) => { if (moveDialog) moveTo(moveDialog.projectId, e.detail.selectedOption.value || null); setMoveDialog(null); }} />
        </FormField>
      </Modal>
    </div>
  );
}

/** Right panel: the project's history — versions (restore / branch), renders (download), recent chat, sources. */
function ProjectDetail({ p, onClose, onChanged, folderPath, cover }: { p: ProjectSummary; onClose: () => void; onChanged: () => void; folderPath: string; cover?: AssetMedia }) {
  const { t, lang } = useI18n();
  const { notify } = useApp();
  const navigate = useNavigate();
  const [versions, setVersions] = useState<{ version: number; author: string; message: string; createdAt: number }[]>([]);
  const [renders, setRenders] = useState<RenderInfo[]>([]);
  const [chat, setChat] = useState<ChatMessage[]>([]);
  useEffect(() => {
    api.versions(p.projectId).then(setVersions).catch(() => setVersions([]));
    api.renders(p.projectId).then(setRenders).catch(() => setRenders([]));
    api.chat(p.projectId).then((c) => setChat(c.messages)).catch(() => setChat([]));
  }, [p.projectId, p.currentVersion]);
  const restore = async (v: number) => {
    try { await api.restoreVersion(p.projectId, v); notify('success', t('restoredVersion', { v })); onChanged(); navigate(`/studio/${p.projectId}`); }
    catch (e) { notify('error', (e as Error).message); }
  };
  const branch = async (v: number) => {
    try { const r = await api.duplicateProject(p.projectId, { version: v, title: `${p.title} (v${v})` }); onChanged(); navigate(`/studio/${r.projectId}`); }
    catch (e) { notify('error', (e as Error).message); }
  };
  return (
    <aside className="pj-detail" style={{ borderLeft: `1px solid ${awsui.colorBorderDividerDefault}`, background: awsui.colorBackgroundContainerContent }}>
      <div style={{ display: 'flex', alignItems: 'flex-start', gap: 8 }}>
        <div style={{ flex: 1, minWidth: 0 }}>
          <Box variant="h3" padding="n">{p.title}</Box>
          <Box variant="small" color="text-body-secondary">{[folderPath && `📁 ${folderPath}`, p.aspect, p.durationSec ? fmtTime(p.durationSec) : null, `v${p.currentVersion}`].filter(Boolean).join(' · ')}</Box>
        </div>
        <Button variant="icon" iconName="close" ariaLabel={t('close')} onClick={onClose} />
      </div>
      <SpaceBetween direction="horizontal" size="xs">
        <Button variant="primary" onClick={() => navigate(`/studio/${p.projectId}`)}>{t('open')}</Button>
        <Button onClick={() => branch(p.currentVersion)}>{t('duplicateProject')}</Button>
      </SpaceBetween>
      {cover?.sprites ? <SpriteFrame m={cover} t={p.coverAt ?? 0} style={{ width: '100%', borderRadius: 6, margin: '10px 0' }} />
        : p.coverUrl && <img src={p.coverUrl} alt="" style={{ width: '100%', borderRadius: 6, margin: '10px 0', maxHeight: 220, objectFit: 'cover' }} />}
      <Tabs tabs={[
        { id: 'versions', label: `${t('versions')} (${versions.length})`, content: (
          <SpaceBetween size="xs">
            {versions.map((v) => (
              <div key={v.version} className="pj-ver">
                <div style={{ flex: 1, minWidth: 0 }}>
                  <Box variant="small"><b>v{v.version}</b> · {v.author === 'agent' ? 'AI' : v.author === 'user' ? t('you') : v.author} · {rel(v.createdAt, lang)}</Box>
                  <Box variant="small" color="text-body-secondary">{v.message || '—'}</Box>
                </div>
                {v.version !== p.currentVersion ? (
                  <ButtonDropdown variant="inline-icon" ariaLabel="version" expandToViewport
                    items={[{ id: 'restore', text: t('restore') }, { id: 'branch', text: t('branchFromVersion') }]}
                    onItemClick={({ detail }) => (detail.id === 'restore' ? restore(v.version) : branch(v.version))} />
                ) : <Badge color="green">{t('current')}</Badge>}
              </div>
            ))}
          </SpaceBetween>
        ) },
        { id: 'renders', label: `${t('renders')} (${renders.length})`, content: renders.length ? (
          <SpaceBetween size="xs">
            {renders.map((r) => (
              <div key={r.renderId} className="pj-ver">
                <div style={{ flex: 1 }}>
                  <Box variant="small"><b>{r.quality}</b> · v{r.version} · {rel(r.createdAt, lang)}</Box>
                  <StatusIndicator type={r.status === 'done' ? 'success' : r.status === 'failed' ? 'error' : 'in-progress'}>{r.status}{r.progress && r.status === 'running' ? ` ${Math.round(r.progress * 100)}%` : ''}</StatusIndicator>
                </div>
                {r.urls?.video && <Button variant="inline-link" href={r.urls.video} iconName="download">{t('download')}</Button>}
              </div>
            ))}
          </SpaceBetween>
        ) : <Box variant="small" color="text-body-secondary">{t('noRenders')}</Box> },
        { id: 'chat', label: t('conversation'), content: (
          <SpaceBetween size="xs">
            {chat.slice(-8).map((m, i) => (
              <div key={i} style={{ fontSize: 12, padding: '6px 8px', borderRadius: 6, background: m.role === 'user' ? awsui.colorBackgroundChatBubbleIncoming : 'transparent' }}>
                <b>{m.role === 'user' ? t('you') : 'AI'}</b> · <span style={{ color: awsui.colorTextBodySecondary }}>{rel(m.createdAt, lang)}</span>
                <div style={{ whiteSpace: 'pre-wrap' }}>{(m.text || m.error || '').slice(0, 300)}</div>
              </div>
            ))}
            {!chat.length && <Box variant="small" color="text-body-secondary">—</Box>}
            <Button variant="link" onClick={() => navigate(`/studio/${p.projectId}`)}>{t('continueChat')}</Button>
          </SpaceBetween>
        ) },
      ]} />
    </aside>
  );
}
