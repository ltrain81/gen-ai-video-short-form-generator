import Box from '@cloudscape-design/components/box';
import Button from '@cloudscape-design/components/button';
import FormField from '@cloudscape-design/components/form-field';
import Input from '@cloudscape-design/components/input';
import Icon, { IconProps } from '@cloudscape-design/components/icon';
import Popover from '@cloudscape-design/components/popover';
import PromptInput from '@cloudscape-design/components/prompt-input';
import SegmentedControl from '@cloudscape-design/components/segmented-control';
import Select from '@cloudscape-design/components/select';
import SpaceBetween from '@cloudscape-design/components/space-between';
import Toggle from '@cloudscape-design/components/toggle';
import { useEffect, useMemo, useRef, useState } from 'react';
import { useLocation, useNavigate } from 'react-router-dom';
import { useApp } from '../App';
import AssetPicker from '../components/AssetPicker';
import SpriteFrame from '../components/SpriteFrame';
import { api } from '../api/client';
import { uploadAndFetch } from '../api/upload';
import type { Asset, AssetMedia, BrandKit, ProjectSummary, Recipe } from '../api/types';
import { useI18n } from '../i18n';
import { fmtTime } from '../util';
import { Thumb } from './ArchivePage';

const LENGTHS = [15, 30, 45, 60, 90, 180];
const CAPTIONS = ['auto', 'clean', 'bold', 'karaoke', 'pop', 'boxed', 'minimal', 'none'];
const ASPECTS = ['9:16', '1:1', '4:5', '16:9'];
const lenLabel = (l: number) => (l >= 120 ? `${Math.round(l / 60)}m` : `${l}s`);

function ago(ms: number | undefined, lang: string): string {
  if (!ms) return '';
  const d = (Date.now() - ms) / 1000;
  const rtf = new Intl.RelativeTimeFormat(lang, { numeric: 'auto' });
  if (d < 3600) return rtf.format(-Math.max(1, Math.round(d / 60)), 'minute');
  if (d < 86400) return rtf.format(-Math.round(d / 3600), 'hour');
  return rtf.format(-Math.round(d / 86400), 'day');
}

/** Home: one composer (describe → AI edits), templates, recent work and the archive one click away. */
export default function StudioPage() {
  const { t, lang } = useI18n();
  const { notify, me } = useApp();
  const navigate = useNavigate();
  const location = useLocation();
  const [value, setValue] = useState('');
  const [aspect, setAspect] = useState('9:16');
  const [picked, setPicked] = useState<Asset[]>(() => (location.state as { assets?: Asset[] } | null)?.assets ?? []);
  const [showPicker, setShowPicker] = useState(false);
  const [recent, setRecent] = useState<ProjectSummary[]>([]);
  const [archive, setArchive] = useState<Asset[]>([]);
  const [covers, setCovers] = useState<Record<string, AssetMedia>>({});
  const [recipes, setRecipes] = useState<Recipe[]>([]);
  const [recipe, setRecipe] = useState<Recipe | null>(null);
  const [length, setLength] = useState<number | null>(null);
  const [captions, setCaptions] = useState('auto');
  const [music, setMusic] = useState(false);
  const [kits, setKits] = useState<BrandKit[]>([]);
  const [kit, setKit] = useState<string | null>(null);
  const [batch, setBatch] = useState(false);
  const [count, setCount] = useState(5);
  const [batchMode, setBatchMode] = useState<'count' | 'topics'>('count');
  const [topicText, setTopicText] = useState('');
  const topicList = topicText.split(/[,\n]/).map((x) => x.trim()).filter(Boolean).slice(0, 10);
  const [busy, setBusy] = useState(false);
  const [dropOver, setDropOver] = useState(false);
  const [uploading, setUploading] = useState(0);
  const [allRecipes, setAllRecipes] = useState(false);
  const composerRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    api.projects().then((p) => setRecent(p.filter((x) => !x.archived).slice(0, 6))).catch(() => undefined);
    api.assets().then((a) => setArchive(a.filter((x) => x.status === 'ready').slice(0, 10))).catch(() => undefined);
    api.recipes().then(setRecipes).catch(() => undefined);
    api.brandKits().then(setKits).catch(() => undefined);
  }, []);
  useEffect(() => {
    const ids = [...new Set(recent.map((p) => p.coverAssetId).filter((x): x is string => !!x))];
    Promise.all(ids.map((id) => api.media(id).then((m) => [id, m] as const).catch(() => null)))
      .then((rs) => setCovers(Object.fromEntries(rs.filter((r): r is readonly [string, AssetMedia] => !!r))));
  }, [recent]);

  const onFiles = (files: File[]) => {
    setUploading((n) => n + files.length);
    files.forEach((f) => uploadAndFetch(f)
      .then((a) => setPicked((x) => [...x, a]))
      .catch((e) => notify('error', `${f.name}: ${(e as Error).message}`))
      .finally(() => setUploading((n) => n - 1)));
  };
  const toggleAsset = (a: Asset) => setPicked((x) => (x.some((y) => y.assetId === a.assetId) ? x.filter((y) => y.assetId !== a.assetId) : [...x, a]));
  const choose = (r: Recipe | null) => {
    setRecipe(r);
    if (r) { setAspect(r.aspect ?? aspect); setLength(r.lengthSec ?? null); setCaptions(r.captions ?? 'auto'); }
    composerRef.current?.querySelector('textarea')?.focus();
  };
  const canBatch = picked.length === 1 && picked[0].kind === 'video';
  const name = (r: Recipe) => r.name?.[lang] ?? r.name?.en ?? r.id;
  const options = useMemo(() => ({ recipe: recipe?.id, aspect, lengthSec: length ?? undefined,
    captions: captions === 'auto' ? undefined : captions, music: music || undefined, brandKitId: kit ?? undefined }),
  [recipe, aspect, length, captions, music, kit]);
  const summary = [length ? lenLabel(length) : t('lengthAuto'), captions === 'auto' ? t('captionsAuto') : captions === 'none' ? t('captionsNone') : captions,
    music ? t('music') : null, kit ? kits.find((k) => k.brandKitId === kit)?.name : null].filter(Boolean).join(' · ');

  const start = async () => {
    const text = value.trim();
    if (!text && !recipe && !(batch && batchMode === 'topics' && topicList.length)) return;
    setBusy(true);
    try {
      if (batch && canBatch) {
        // A series: one orchestrated project (tabs per short) instead of independent fire-and-forget runs.
        const a = picked[0];
        const ask = batchMode === 'topics' && topicList.length
          ? t('seriesAskTopics', { topics: topicList.join(', '), len: length ?? 45 })
          : t('seriesAskCount', { n: count, len: length ?? 45 });
        const { projectId } = await api.createProject({
          title: `${t('series')}: ${batchMode === 'topics' && topicList.length ? topicList.slice(0, 3).join(', ') : a.displayTitle}`.slice(0, 80),
          synopsis: text, assetIds: [a.assetId], ...options,
        });
        navigate(`/studio/${projectId}`, { state: { firstMessage: `${ask}${text ? `\n${text}` : ''}` } });
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

  const shownRecipes = allRecipes ? recipes : recipes.slice(0, 8);
  const hello = me?.email ? me.email.split('@')[0] : '';

  return (
    <div className={`home${dropOver ? ' home-drop' : ''}`}
      onDragOver={(e) => { if (e.dataTransfer.types.includes('Files')) { e.preventDefault(); setDropOver(true); } }}
      onDragLeave={(e) => { if (!e.currentTarget.contains(e.relatedTarget as Node)) setDropOver(false); }}
      onDrop={(e) => { setDropOver(false); if (e.dataTransfer.files.length) { e.preventDefault(); onFiles([...e.dataTransfer.files]); } }}>
      <section className="home-hero">
        <div className="home-eyebrow"><Icon name="gen-ai" /> {t('appSubtitle')}</div>
        <h1>{hello ? t('homeHello', { name: hello }) : t('studioWelcome')}</h1>
        <p>{t('homeSub')}</p>
      </section>

      <section className="home-composer" ref={composerRef}>
        <div className={`home-attach${picked.length ? '' : ' empty'}`}>
          {picked.map((a) => (
            <div key={a.assetId} className="home-att" title={a.displayTitle}>
              <Thumb asset={a} />
              <span className="home-att-name">{a.status !== 'ready' ? t('analyzing') : a.displayTitle}</span>
              <button type="button" aria-label={t('remove')} onClick={() => toggleAsset(a)}>×</button>
            </div>
          ))}
          <button type="button" className="home-att-add" onClick={() => setShowPicker(true)}>
            <Icon name="folder-open" /><span>{t('attachFromArchive')}</span>
          </button>
          <label className="home-att-add">
            <Icon name="upload" /><span>{uploading ? `${t('uploading')} (${uploading})` : t('uploadFiles')}</span>
            <input type="file" multiple hidden onChange={(e) => { if (e.target.files?.length) onFiles([...e.target.files]); e.target.value = ''; }} />
          </label>
        </div>
        {recipe && (
          <div className="home-recipe-on">
            <Icon name={(recipe.icon as IconProps.Name) ?? 'settings'} /> {name(recipe)}
            <button type="button" aria-label={t('remove')} onClick={() => choose(null)}>×</button>
          </div>
        )}
        <PromptInput value={value} onChange={(e) => setValue(e.detail.value)} onAction={start} minRows={3} maxRows={12}
          actionButtonIconName="send" actionButtonAriaLabel="send" disableActionButton={busy || (!value.trim() && !recipe && !(batch && batchMode === 'topics' && topicList.length))}
          placeholder={recipe ? `${name(recipe)} — ${t('recipeHint')}` : t('studioPlaceholder')}
          secondaryActions={
            <div className="home-opts">
              <SegmentedControl selectedId={aspect} onChange={(e) => setAspect(e.detail.selectedId)} options={ASPECTS.map((a) => ({ id: a, text: a }))} />
              <Popover triggerType="custom" size="medium" position="top" dismissButton={false} header={t('options')} content={
                <SpaceBetween size="s">
                  <FormField label={t('length')}>
                    <Select expandToViewport selectedOption={{ value: String(length ?? ''), label: length ? lenLabel(length) : t('lengthAuto') }}
                      options={[{ value: '', label: t('lengthAuto') }, ...LENGTHS.map((l) => ({ value: String(l), label: lenLabel(l) }))]}
                      onChange={(e) => setLength(e.detail.selectedOption.value ? Number(e.detail.selectedOption.value) : null)} />
                  </FormField>
                  <FormField label={t('captions')}>
                    <Select expandToViewport selectedOption={{ value: captions, label: captions === 'auto' ? t('captionsAuto') : captions === 'none' ? t('captionsNone') : captions }}
                      options={CAPTIONS.map((c) => ({ value: c, label: c === 'auto' ? t('captionsAuto') : c === 'none' ? t('captionsNone') : c }))}
                      onChange={(e) => setCaptions(e.detail.selectedOption.value!)} />
                  </FormField>
                  {kits.length > 0 && (
                    <FormField label={t('brandKit')}>
                      <Select expandToViewport selectedOption={kit ? { value: kit, label: kits.find((k) => k.brandKitId === kit)?.name } : { value: '', label: t('noBrandKit') }}
                        options={[{ value: '', label: t('noBrandKit') }, ...kits.map((k) => ({ value: k.brandKitId, label: k.name }))]}
                        onChange={(e) => setKit(e.detail.selectedOption.value || null)} />
                    </FormField>
                  )}
                  <Toggle checked={music} onChange={(e) => setMusic(e.detail.checked)}>{t('music')}</Toggle>
                </SpaceBetween>
              }>
                <button type="button" className="home-opt-btn"><Icon name="settings" size="small" /> {summary}</button>
              </Popover>
            </div>
          }
          secondaryContent={canBatch ? (
            <SpaceBetween direction="horizontal" size="xs" alignItems="center">
              <Toggle checked={batch} onChange={(e) => setBatch(e.detail.checked)}>{t('batchToggle')}</Toggle>
              {batch && <SegmentedControl selectedId={batchMode} onChange={(e) => setBatchMode(e.detail.selectedId as 'count' | 'topics')}
                options={[{ id: 'count', text: t('batchByCount') }, { id: 'topics', text: t('batchByTopic') }]} />}
              {batch && batchMode === 'count' && <Select expandToViewport selectedOption={{ value: String(count), label: `${count}` }}
                options={[2, 3, 4, 5, 6, 8, 10].map((n) => ({ value: String(n), label: String(n) }))}
                onChange={(e) => setCount(Number(e.detail.selectedOption.value))} />}
              {batch && batchMode === 'topics' && (
                <div style={{ minWidth: 320, flex: 1 }}>
                  <Input value={topicText} onChange={(e) => setTopicText(e.detail.value)} placeholder={t('topicsPlaceholder')} />
                  {topicList.length > 0 && <Box variant="small" color="text-body-secondary">{t('topicsCount', { n: topicList.length })}: {topicList.join(' · ')}</Box>}
                </div>
              )}
            </SpaceBetween>
          ) : undefined} />
        <div className="home-hint">{t('homeDropHint')}</div>
      </section>

      {recipes.length > 0 && (
        <section className="home-section">
          <div className="home-section-head">
            <h2>{t('startFromTemplate')}</h2>
            {recipes.length > 8 && <Button variant="inline-link" onClick={() => setAllRecipes(!allRecipes)}>{allRecipes ? t('showLess') : t('showAll', { n: recipes.length })}</Button>}
          </div>
          <div className="home-recipes">
            {shownRecipes.map((r) => (
              <button key={r.id} type="button" className={`home-recipe${recipe?.id === r.id ? ' on' : ''}`} onClick={() => choose(recipe?.id === r.id ? null : r)}>
                <span className="home-recipe-icon"><Icon name={(r.icon as IconProps.Name) ?? 'settings'} /></span>
                <span className="home-recipe-name">{name(r)}</span>
                <span className="home-recipe-meta">{[r.aspect, r.lengthSec ? lenLabel(r.lengthSec) : null].filter(Boolean).join(' · ')}</span>
                <span className="home-recipe-desc">{t(`recipeDesc_${r.id}` as never) !== `recipeDesc_${r.id}` ? t(`recipeDesc_${r.id}` as never) : (r.instruction ?? '').slice(0, 70)}</span>
              </button>
            ))}
          </div>
        </section>
      )}

      {recent.length > 0 && (
        <section className="home-section">
          <div className="home-section-head">
            <h2>{t('continueWhereLeft')}</h2>
            <Button variant="inline-link" onClick={() => navigate('/projects')}>{t('allProjects')} →</Button>
          </div>
          <div className="home-recent">
            {recent.map((p) => {
              const m = p.coverAssetId ? covers[p.coverAssetId] : undefined;
              return (
                <button key={p.projectId} type="button" className="home-proj" onClick={() => navigate(`/studio/${p.projectId}`)}>
                  <div className="home-proj-cover">
                    {m?.sprites ? <SpriteFrame m={m} t={p.coverAt ?? 0} style={{ position: 'absolute', inset: 0, width: '100%', height: '100%', aspectRatio: 'auto' }} />
                      : p.coverUrl ? <img src={p.coverUrl} alt="" /> : <span>{t('emptyTimeline')}</span>}
                    <span className="home-proj-badge">{p.aspect}{p.durationSec ? ` · ${fmtTime(p.durationSec)}` : ''}</span>
                  </div>
                  <span className="home-proj-title">{p.title}</span>
                  <span className="home-proj-meta">v{p.currentVersion} · {ago(p.updatedAt, lang)}</span>
                </button>
              );
            })}
          </div>
        </section>
      )}

      {archive.length > 0 && (
        <section className="home-section">
          <div className="home-section-head">
            <h2>{t('fromYourArchive')}</h2>
            <Button variant="inline-link" onClick={() => navigate('/archive')}>{t('navArchive')} →</Button>
          </div>
          <div className="home-archive">
            {archive.map((a) => {
              const on = picked.some((x) => x.assetId === a.assetId);
              return (
                <button key={a.assetId} type="button" className={`home-asset${on ? ' on' : ''}`} onClick={() => toggleAsset(a)} title={a.displayTitle}>
                  <Thumb asset={a} />
                  <span className="home-asset-check">{on ? '✓' : '+'}</span>
                  <span className="home-asset-name">{a.displayTitle}</span>
                  <span className="home-asset-meta">{t((a.kind ?? 'other') as never)}{a.durationSec ? ` · ${fmtTime(a.durationSec)}` : ''}</span>
                </button>
              );
            })}
          </div>
        </section>
      )}

      <AssetPicker visible={showPicker} initial={picked} onDismiss={() => setShowPicker(false)}
        onConfirm={(a) => { setPicked(a); setShowPicker(false); }} />
      <Box padding={{ bottom: 'xxl' }} />
    </div>
  );
}
