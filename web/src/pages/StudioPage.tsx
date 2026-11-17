import Box from '@cloudscape-design/components/box';
import Button from '@cloudscape-design/components/button';
import Checkbox from '@cloudscape-design/components/checkbox';
import Link from '@cloudscape-design/components/link';
import PromptInput from '@cloudscape-design/components/prompt-input';
import SegmentedControl from '@cloudscape-design/components/segmented-control';
import Select from '@cloudscape-design/components/select';
import SpaceBetween from '@cloudscape-design/components/space-between';
import Toggle from '@cloudscape-design/components/toggle';
import TokenGroup from '@cloudscape-design/components/token-group';
import * as awsui from '@cloudscape-design/design-tokens';
import { IconProps } from '@cloudscape-design/components/icon';
import Icon from '@cloudscape-design/components/icon';
import { useEffect, useMemo, useState } from 'react';
import { useLocation, useNavigate } from 'react-router-dom';
import { useApp } from '../App';
import AssetPicker from '../components/AssetPicker';
import { api } from '../api/client';
import { uploadAndFetch } from '../api/upload';
import type { Asset, BrandKit, ProjectSummary, Recipe } from '../api/types';
import { useI18n } from '../i18n';
import { fmtDate } from '../util';

const LENGTHS = [15, 30, 45, 60, 90, 180];
const CAPTIONS = ['auto', 'clean', 'bold', 'karaoke', 'pop', 'boxed', 'minimal', 'none'];

/** Chat-first start: pick a format recipe (optional), attach footage, describe → project (or a batch). */
export default function StudioPage() {
  const { t, lang } = useI18n();
  const { notify } = useApp();
  const navigate = useNavigate();
  const [value, setValue] = useState('');
  const [aspect, setAspect] = useState('9:16');
  const location = useLocation();
  const [picked, setPicked] = useState<Asset[]>(() => (location.state as { assets?: Asset[] } | null)?.assets ?? []);
  const [showPicker, setShowPicker] = useState(false);
  const [recent, setRecent] = useState<ProjectSummary[]>([]);
  const [recipes, setRecipes] = useState<Recipe[]>([]);
  const [recipe, setRecipe] = useState<Recipe | null>(null);
  const [length, setLength] = useState<number | null>(null);
  const [captions, setCaptions] = useState('auto');
  const [music, setMusic] = useState(false);
  const [kits, setKits] = useState<BrandKit[]>([]);
  const [kit, setKit] = useState<string | null>(null);
  const [batch, setBatch] = useState(false);
  const [count, setCount] = useState(5);
  const [busy, setBusy] = useState(false);
  const [dropOver, setDropOver] = useState(false);
  const [uploading, setUploading] = useState(0);
  const onFiles = (files: File[]) => {
    setUploading((n) => n + files.length);
    files.forEach((f) => uploadAndFetch(f)
      .then((a) => setPicked((x) => [...x, a]))
      .catch((e) => notify('error', `${f.name}: ${(e as Error).message}`))
      .finally(() => setUploading((n) => n - 1)));
  };

  useEffect(() => {
    api.projects().then((p) => setRecent(p.slice(0, 6))).catch(() => undefined);
    api.recipes().then(setRecipes).catch(() => undefined);
    api.brandKits().then(setKits).catch(() => undefined);
  }, []);
  const choose = (r: Recipe | null) => {
    setRecipe(r);
    if (r) { setAspect(r.aspect ?? aspect); setLength(r.lengthSec ?? null); setCaptions(r.captions ?? 'auto'); }
  };
  const canBatch = picked.length === 1 && picked[0].kind === 'video';
  const name = (r: Recipe) => r.name?.[lang] ?? r.name?.en ?? r.id;
  const options = useMemo(() => ({ recipe: recipe?.id, aspect, lengthSec: length ?? undefined,
    captions: captions === 'auto' ? undefined : captions, music: music || undefined, brandKitId: kit ?? undefined }),
  [recipe, aspect, length, captions, music, kit]);

  const start = async () => {
    const text = value.trim();
    if (!text && !recipe) return;
    setBusy(true);
    try {
      if (batch && canBatch) {
        const b = await api.createBatch({ assetId: picked[0].assetId, count, instruction: text, ...options });
        navigate(`/batches/${b.batchId}`);
        return;
      }
      const { projectId } = await api.createProject({
        title: (text || (recipe ? name(recipe) : 'Untitled')).slice(0, 60), synopsis: text,
        assetIds: picked.map((a) => a.assetId), ...options,
      });
      navigate(`/studio/${projectId}`, { state: { firstMessage: text || (recipe ? name(recipe) : '') } });
    } catch (e) {
      notify('error', (e as Error).message);
      setBusy(false);
    }
  };

  return (
    <div className="studio-center">
      <SpaceBetween size="xs" alignItems="center">
        <Box variant="h1">{t('studioWelcome')}</Box>
        <Box color="text-body-secondary">{t('studioHint')} {t('dropFilesHint')}</Box>
      </SpaceBetween>
      <div className={`prompt${dropOver ? ' drop-zone-active' : ''}`}
        onDragOver={(e) => { if (!e.dataTransfer.types.includes('Files')) return; e.preventDefault(); setDropOver(true); }}
        onDragLeave={(e) => { if (!e.currentTarget.contains(e.relatedTarget as Node)) setDropOver(false); }}
        onDrop={(e) => { setDropOver(false); if (e.dataTransfer.files.length) { e.preventDefault(); onFiles([...e.dataTransfer.files]); } }}>
        <SpaceBetween size="m">
          <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8, justifyContent: 'center' }}>
            {recipes.map((r) => {
              const on = recipe?.id === r.id;
              return (
                <button key={r.id} type="button" onClick={() => choose(on ? null : r)} className="recipe-chip"
                  style={{ borderColor: on ? '#0972d3' : awsui.colorBorderDividerDefault, background: on ? 'rgba(9,114,211,0.08)' : 'transparent', color: 'inherit' }}>
                  <Icon name={(r.icon as IconProps.Name) ?? 'settings'} size="small" /> {name(r)}
                  {r.lengthSec ? <span style={{ color: '#8d99a8', marginLeft: 4 }}>{r.lengthSec >= 120 ? `${Math.round(r.lengthSec / 60)}m` : `${r.lengthSec}s`}</span> : null}
                </button>
              );
            })}
          </div>
          <PromptInput value={value} onChange={(e) => setValue(e.detail.value)} onAction={start} minRows={3} maxRows={10}
            actionButtonIconName="send" actionButtonAriaLabel="send" disableActionButton={busy || (!value.trim() && !recipe)}
            placeholder={uploading ? `${t('uploading')} (${uploading})…` : recipe ? `${name(recipe)} — ${t('recipeHint')}` : t('studioPlaceholder')}
            secondaryActions={
              <SpaceBetween direction="horizontal" size="xs" alignItems="center">
                <Button iconName="folder-open" onClick={() => setShowPicker(true)}>
                  {picked.length ? `${t('attachFromArchive')} (${picked.length})` : t('attachFromArchive')}
                </Button>
                <SegmentedControl selectedId={aspect} onChange={(e) => setAspect(e.detail.selectedId)}
                  options={['9:16', '1:1', '4:5', '16:9'].map((a) => ({ id: a, text: a }))} />
                <Select expandToViewport selectedOption={{ value: String(length ?? ''), label: length ? (length >= 120 ? `${length / 60}m` : `${length}s`) : t('lengthAuto') }}
                  options={[{ value: '', label: t('lengthAuto') }, ...LENGTHS.map((l) => ({ value: String(l), label: l >= 120 ? `${l / 60}m` : `${l}s` }))]}
                  onChange={(e) => setLength(e.detail.selectedOption.value ? Number(e.detail.selectedOption.value) : null)} />
                <Select expandToViewport selectedOption={{ value: captions, label: captions === 'auto' ? t('captionsAuto') : captions === 'none' ? t('captionsNone') : captions }}
                  options={CAPTIONS.map((c) => ({ value: c, label: c === 'auto' ? t('captionsAuto') : c === 'none' ? t('captionsNone') : c }))}
                  onChange={(e) => setCaptions(e.detail.selectedOption.value!)} />
                <Checkbox checked={music} onChange={(e) => setMusic(e.detail.checked)}>{t('music')}</Checkbox>
                {kits.length > 0 && (
                  <Select expandToViewport selectedOption={kit ? { value: kit, label: kits.find((k) => k.brandKitId === kit)?.name } : { value: '', label: t('noBrandKit') }}
                    options={[{ value: '', label: t('noBrandKit') }, ...kits.map((k) => ({ value: k.brandKitId, label: k.name }))]}
                    onChange={(e) => setKit(e.detail.selectedOption.value || null)} />
                )}
              </SpaceBetween>
            }
            secondaryContent={picked.length ? (
              <SpaceBetween size="xs">
                <TokenGroup items={picked.map((a, i) => ({ label: `${i + 1}. ${a.displayTitle}`, description: a.status !== 'ready' ? t('analyzing') : t((a.kind ?? 'other') as never), dismissLabel: t('remove') }))}
                  onDismiss={({ detail }) => setPicked(picked.filter((_, i) => i !== detail.itemIndex))} />
                {canBatch && (
                  <SpaceBetween direction="horizontal" size="xs" alignItems="center">
                    <Toggle checked={batch} onChange={(e) => setBatch(e.detail.checked)}>{t('batchToggle')}</Toggle>
                    {batch && <Select expandToViewport selectedOption={{ value: String(count), label: `${count}` }}
                      options={[2, 3, 4, 5, 6, 8, 10].map((n) => ({ value: String(n), label: String(n) }))}
                      onChange={(e) => setCount(Number(e.detail.selectedOption.value))} />}
                  </SpaceBetween>
                )}
              </SpaceBetween>
            ) : undefined}
          />
          <AssetPicker visible={showPicker} initial={picked} onDismiss={() => setShowPicker(false)}
            onConfirm={(a) => { setPicked(a); setShowPicker(false); }} />
          {recent.length > 0 && (
            <SpaceBetween size="xxs">
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline' }}>
                <Box variant="awsui-key-label">{t('recentProjects')}</Box>
                <Link onFollow={(e) => { e.preventDefault(); navigate('/projects'); }}>{t('allProjects')} →</Link>
              </div>
              {recent.map((p) => (
                <Box key={p.projectId} variant="small">
                  <Link onFollow={(e) => { e.preventDefault(); navigate(`/studio/${p.projectId}`); }}>{p.title}</Link>
                  {' '}<span style={{ color: '#8d99a8' }}>· {p.aspect} · {fmtDate(p.updatedAt)}</span>
                </Box>
              ))}
            </SpaceBetween>
          )}
        </SpaceBetween>
      </div>
    </div>
  );
}
