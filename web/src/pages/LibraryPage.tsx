import Box from '@cloudscape-design/components/box';
import Button from '@cloudscape-design/components/button';
import ColumnLayout from '@cloudscape-design/components/column-layout';
import ContentLayout from '@cloudscape-design/components/content-layout';
import FormField from '@cloudscape-design/components/form-field';
import Header from '@cloudscape-design/components/header';
import Input from '@cloudscape-design/components/input';
import Modal from '@cloudscape-design/components/modal';
import Select from '@cloudscape-design/components/select';
import SpaceBetween from '@cloudscape-design/components/space-between';
import Table from '@cloudscape-design/components/table';
import Tabs from '@cloudscape-design/components/tabs';
import Textarea from '@cloudscape-design/components/textarea';
import { useCallback, useEffect, useState } from 'react';
import { useApp } from '../App';
import { api } from '../api/client';
import type { Asset, BrandKit, Recipe, Skill } from '../api/types';
import { useI18n } from '../i18n';

/** L5: workspace recipes (formats), skills (agent playbooks) and brand kits. */
export default function LibraryPage() {
  const { t } = useI18n();
  return (
    <ContentLayout header={<Header variant="h1" description={t('libraryHint')}>{t('navLibrary')}</Header>}>
      <Tabs tabs={[
        { id: 'recipes', label: t('recipes'), content: <Recipes /> },
        { id: 'skills', label: t('skills'), content: <Skills /> },
        { id: 'brand', label: t('brandKits'), content: <BrandKits /> },
      ]} />
    </ContentLayout>
  );
}

function Recipes() {
  const { t, lang } = useI18n();
  const { notify } = useApp();
  const [items, setItems] = useState<Recipe[]>([]);
  const [edit, setEdit] = useState<Partial<Recipe> & { id?: string } | null>(null);
  const [skills, setSkills] = useState<Skill[]>([]);
  const load = useCallback(() => api.recipes().then(setItems).catch((e) => notify('error', e.message)), [notify]);
  useEffect(() => { load(); api.skills().then(setSkills).catch(() => undefined); }, [load]);
  const save = async () => {
    if (!edit?.id) return;
    try { await api.putRecipe(edit.id, edit); setEdit(null); load(); } catch (e) { notify('error', (e as Error).message); }
  };
  return (
    <>
      <Table items={items} header={<Header actions={<Button onClick={() => setEdit({ id: '', aspect: '9:16', lengthSec: 45, captions: 'clean' })}>{t('newRecipe')}</Button>}>{t('recipes')}</Header>}
        columnDefinitions={[
          { id: 'n', header: t('name'), cell: (r) => r.name?.[lang] ?? r.id },
          { id: 's', header: t('skills'), cell: (r) => r.skill },
          { id: 'f', header: t('format'), cell: (r) => `${r.aspect ?? ''} · ${r.lengthSec ?? '–'}s · ${r.captions ?? ''}` },
          { id: 'i', header: t('instruction'), cell: (r) => <Box variant="small">{(r.instruction ?? '').slice(0, 120)}</Box> },
          { id: 'a', header: '', cell: (r) => r.builtin ? <Box variant="small" color="text-body-secondary">{t('builtin')}</Box> : (
            <SpaceBetween direction="horizontal" size="xs">
              <Button variant="inline-link" onClick={() => setEdit({ ...r })}>{t('edit')}</Button>
              <Button variant="inline-link" onClick={async () => { await api.deleteRecipe(r.id); load(); }}>{t('delete')}</Button>
            </SpaceBetween>) },
        ]} />
      {edit && (
        <Modal visible onDismiss={() => setEdit(null)} header={t('recipe')} footer={<Box float="right"><SpaceBetween direction="horizontal" size="xs">
          <Button onClick={() => setEdit(null)}>{t('cancel')}</Button><Button variant="primary" onClick={save}>{t('save')}</Button></SpaceBetween></Box>}>
          <SpaceBetween size="m">
            <ColumnLayout columns={2}>
              <FormField label="ID (slug)"><Input value={edit.id ?? ''} onChange={(e) => setEdit({ ...edit, id: e.detail.value.toLowerCase() })} /></FormField>
              <FormField label={t('name')}><Input value={edit.name?.[lang] ?? ''} onChange={(e) => setEdit({ ...edit, name: { ...edit.name, [lang]: e.detail.value } })} /></FormField>
            </ColumnLayout>
            <ColumnLayout columns={3}>
              <FormField label={t('aspect')}><Select expandToViewport selectedOption={{ value: edit.aspect ?? '9:16', label: edit.aspect ?? '9:16' }}
                options={['9:16', '1:1', '4:5', '16:9'].map((a) => ({ value: a, label: a }))} onChange={(e) => setEdit({ ...edit, aspect: e.detail.selectedOption.value })} /></FormField>
              <FormField label={t('lengthSec')}><Input type="number" value={String(edit.lengthSec ?? 45)} onChange={(e) => setEdit({ ...edit, lengthSec: Number(e.detail.value) })} /></FormField>
              <FormField label={t('captions')}><Select expandToViewport selectedOption={{ value: edit.captions ?? 'clean', label: edit.captions ?? 'clean' }}
                options={['clean', 'bold', 'karaoke', 'pop', 'boxed', 'minimal', 'none'].map((c) => ({ value: c, label: c }))} onChange={(e) => setEdit({ ...edit, captions: e.detail.selectedOption.value })} /></FormField>
            </ColumnLayout>
            <FormField label={t('skills')}><Select expandToViewport selectedOption={edit.skill ? { value: edit.skill, label: edit.skill } : null}
              options={skills.map((s) => ({ value: s.name, label: s.name, description: s.description }))} onChange={(e) => setEdit({ ...edit, skill: e.detail.selectedOption.value })} /></FormField>
            <FormField label={t('instruction')}><Textarea rows={5} value={edit.instruction ?? ''} onChange={(e) => setEdit({ ...edit, instruction: e.detail.value })} /></FormField>
          </SpaceBetween>
        </Modal>
      )}
    </>
  );
}

function Skills() {
  const { t } = useI18n();
  const { notify } = useApp();
  const [items, setItems] = useState<Skill[]>([]);
  const [edit, setEdit] = useState<{ name: string; description: string; body: string; isNew: boolean } | null>(null);
  const load = useCallback(() => api.skills().then(setItems).catch((e) => notify('error', e.message)), [notify]);
  useEffect(() => { load(); }, [load]);
  const open = async (s: Skill, asCopy = false) => {
    const full = await api.skill(s.name);
    setEdit({ name: asCopy ? `${s.name}-custom` : s.name, description: full.description, body: full.body ?? '', isNew: asCopy });
  };
  return (
    <>
      <Table items={items} header={<Header description={t('skillsHint')} actions={<Button onClick={() => setEdit({ name: '', description: '', body: '', isNew: true })}>{t('newSkill')}</Button>}>{t('skills')}</Header>}
        columnDefinitions={[
          { id: 'n', header: t('name'), cell: (s) => <Box variant="code">{s.name}</Box> },
          { id: 'd', header: t('description'), cell: (s) => s.description },
          { id: 'a', header: '', cell: (s) => (
            <SpaceBetween direction="horizontal" size="xs">
              {s.builtin ? <Button variant="inline-link" onClick={() => open(s, true)}>{t('customize')}</Button>
                : <Button variant="inline-link" onClick={() => open(s)}>{t('edit')}</Button>}
              {!s.builtin && <Button variant="inline-link" onClick={async () => { await api.deleteSkill(s.name); load(); }}>{t('delete')}</Button>}
            </SpaceBetween>) },
        ]} />
      {edit && (
        <Modal visible size="large" onDismiss={() => setEdit(null)} header={t('skill')} footer={<Box float="right"><SpaceBetween direction="horizontal" size="xs">
          <Button onClick={() => setEdit(null)}>{t('cancel')}</Button>
          <Button variant="primary" onClick={async () => { try { await api.putSkill(edit.name, edit.description, edit.body); setEdit(null); load(); } catch (e) { notify('error', (e as Error).message); } }}>{t('save')}</Button>
        </SpaceBetween></Box>}>
          <SpaceBetween size="m">
            <FormField label="name (slug)"><Input value={edit.name} disabled={!edit.isNew} onChange={(e) => setEdit({ ...edit, name: e.detail.value.toLowerCase() })} /></FormField>
            <FormField label={t('description')}><Input value={edit.description} onChange={(e) => setEdit({ ...edit, description: e.detail.value })} /></FormField>
            <FormField label={t('playbook')} description={t('playbookHint')}><Textarea rows={14} value={edit.body} onChange={(e) => setEdit({ ...edit, body: e.detail.value })} /></FormField>
          </SpaceBetween>
        </Modal>
      )}
    </>
  );
}

function BrandKits() {
  const { t } = useI18n();
  const { me, notify } = useApp();
  const [items, setItems] = useState<BrandKit[]>([]);
  const [assets, setAssets] = useState<Asset[]>([]);
  const [edit, setEdit] = useState<Partial<BrandKit> | null>(null);
  const load = useCallback(() => api.brandKits().then(setItems).catch((e) => notify('error', e.message)), [notify]);
  useEffect(() => { load(); api.assets().then((a) => setAssets(a.filter((x) => x.status === 'ready'))).catch(() => undefined); }, [load]);
  const opt = (kinds: string[]) => [{ value: '', label: '—' }, ...assets.filter((a) => kinds.includes(a.kind ?? '')).map((a) => ({ value: a.assetId, label: a.displayTitle }))];
  const sel = (id?: string) => id ? { value: id, label: assets.find((a) => a.assetId === id)?.displayTitle ?? id } : { value: '', label: '—' };
  const save = async () => {
    if (!edit) return;
    try { if (edit.brandKitId) await api.updateBrandKit(edit.brandKitId, edit); else await api.createBrandKit(edit); setEdit(null); load(); }
    catch (e) { notify('error', (e as Error).message); }
  };
  return (
    <>
      <Table items={items} header={<Header description={t('brandHint')} actions={<Button onClick={() => setEdit({ name: '', logoCorner: 'tr', logoScale: 0.18, logoOpacity: 0.9, captionPreset: 'bold' })}>{t('newBrandKit')}</Button>}>{t('brandKits')}</Header>}
        columnDefinitions={[
          { id: 'n', header: t('name'), cell: (k) => k.name },
          { id: 'g', header: t('group'), cell: (k) => k.groupId ? (me?.groups.find((g) => g.groupId === k.groupId)?.name ?? k.groupId) : t('tenantAll') },
          { id: 'c', header: t('color'), cell: (k) => <SpaceBetween direction="horizontal" size="xxs">{[k.textColor, k.accentColor].filter(Boolean).map((c) => <span key={c} style={{ display: 'inline-block', width: 16, height: 16, background: c, borderRadius: 3, border: '1px solid #8884' }} />)}</SpaceBetween> },
          { id: 'l', header: 'Logo', cell: (k) => k.logoAssetId ? sel(k.logoAssetId).label : '—' },
          { id: 'a', header: '', cell: (k) => (
            <SpaceBetween direction="horizontal" size="xs">
              <Button variant="inline-link" onClick={() => setEdit({ ...k })}>{t('edit')}</Button>
              <Button variant="inline-link" onClick={async () => { await api.deleteBrandKit(k.brandKitId); load(); }}>{t('delete')}</Button>
            </SpaceBetween>) },
        ]} />
      {edit && (
        <Modal visible size="large" onDismiss={() => setEdit(null)} header={t('brandKit')} footer={<Box float="right"><SpaceBetween direction="horizontal" size="xs">
          <Button onClick={() => setEdit(null)}>{t('cancel')}</Button><Button variant="primary" onClick={save}>{t('save')}</Button></SpaceBetween></Box>}>
          <SpaceBetween size="m">
            <ColumnLayout columns={2}>
              <FormField label={t('name')}><Input value={edit.name ?? ''} onChange={(e) => setEdit({ ...edit, name: e.detail.value })} /></FormField>
              <FormField label={t('group')}><Select expandToViewport selectedOption={edit.groupId ? { value: edit.groupId, label: me?.groups.find((g) => g.groupId === edit.groupId)?.name } : { value: '', label: t('tenantAll') }}
                options={[{ value: '', label: t('tenantAll') }, ...(me?.groups ?? []).map((g) => ({ value: g.groupId, label: g.name }))]}
                onChange={(e) => setEdit({ ...edit, groupId: e.detail.selectedOption.value || null })} /></FormField>
            </ColumnLayout>
            <ColumnLayout columns={3}>
              <FormField label="Logo"><Select expandToViewport selectedOption={sel(edit.logoAssetId)} options={opt(['image'])} filteringType="auto" onChange={(e) => setEdit({ ...edit, logoAssetId: e.detail.selectedOption.value || undefined })} /></FormField>
              <FormField label={t('logoCorner')}><Select expandToViewport selectedOption={{ value: edit.logoCorner ?? 'tr', label: edit.logoCorner ?? 'tr' }}
                options={['tl', 'tr', 'bl', 'br'].map((c) => ({ value: c, label: c }))} onChange={(e) => setEdit({ ...edit, logoCorner: e.detail.selectedOption.value as BrandKit['logoCorner'] })} /></FormField>
              <FormField label={t('logoSize')}><Input type="number" value={String(edit.logoScale ?? 0.18)} onChange={(e) => setEdit({ ...edit, logoScale: Number(e.detail.value) })} /></FormField>
            </ColumnLayout>
            <ColumnLayout columns={2}>
              <FormField label={t('intro')}><Select expandToViewport selectedOption={sel(edit.introAssetId)} options={opt(['video', 'image'])} filteringType="auto" onChange={(e) => setEdit({ ...edit, introAssetId: e.detail.selectedOption.value || undefined })} /></FormField>
              <FormField label={t('outro')}><Select expandToViewport selectedOption={sel(edit.outroAssetId)} options={opt(['video', 'image'])} filteringType="auto" onChange={(e) => setEdit({ ...edit, outroAssetId: e.detail.selectedOption.value || undefined })} /></FormField>
            </ColumnLayout>
            <ColumnLayout columns={3}>
              <FormField label={t('textColor')}><Input value={edit.textColor ?? ''} placeholder="#FFFFFF" onChange={(e) => setEdit({ ...edit, textColor: e.detail.value })} /></FormField>
              <FormField label={t('accentColor')}><Input value={edit.accentColor ?? ''} placeholder="#FFD400" onChange={(e) => setEdit({ ...edit, accentColor: e.detail.value })} /></FormField>
              <FormField label={t('captions')}><Select expandToViewport selectedOption={{ value: edit.captionPreset ?? 'bold', label: edit.captionPreset ?? 'bold' }}
                options={['clean', 'bold', 'karaoke', 'pop', 'boxed', 'minimal'].map((c) => ({ value: c, label: c }))} onChange={(e) => setEdit({ ...edit, captionPreset: e.detail.selectedOption.value })} /></FormField>
            </ColumnLayout>
          </SpaceBetween>
        </Modal>
      )}
    </>
  );
}
