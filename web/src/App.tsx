import AppLayout from '@cloudscape-design/components/app-layout';
import Flashbar, { FlashbarProps } from '@cloudscape-design/components/flashbar';
import SideNavigation from '@cloudscape-design/components/side-navigation';
import Spinner from '@cloudscape-design/components/spinner';
import TopNavigation from '@cloudscape-design/components/top-navigation';
import { I18nProvider as CsI18nProvider } from '@cloudscape-design/components/i18n';
import csMessagesEn from '@cloudscape-design/components/i18n/messages/all.en';
import csMessagesKo from '@cloudscape-design/components/i18n/messages/all.ko';
import { createContext, useCallback, useContext, useEffect, useState } from 'react';
import { Navigate, Route, Routes, useLocation, useNavigate } from 'react-router-dom';
import { api } from './api/client';
import type { Me } from './api/types';
import { useAuth } from './auth';
import { useI18n } from './i18n';
import AdminPage from './pages/AdminPage';
import ProjectsPage from './pages/ProjectsPage';
import ArchivePage from './pages/ArchivePage';
import AssetPage from './pages/AssetPage';
import LoginPage from './pages/LoginPage';
import ProjectPage from './pages/ProjectPage';
import BatchPage from './pages/BatchPage';
import LibraryPage from './pages/LibraryPage';
import StudioPage from './pages/StudioPage';
import { applyColorPref, type ColorPref } from './theme';

interface AppCtx {
  me: Me | null;
  notify: (type: FlashbarProps.Type, content: string) => void;
}
const Ctx = createContext<AppCtx>(null as never);
export const useApp = () => useContext(Ctx);

export default function App() {
  const { user, loading, signOut } = useAuth();
  const { t, lang, setLang } = useI18n();
  const [me, setMe] = useState<Me | null>(null);
  const [flash, setFlash] = useState<FlashbarProps.MessageDefinition[]>([]);
  const navigate = useNavigate();
  const location = useLocation();
  const [colorPref, setColorPref] = useState<ColorPref>((localStorage.getItem('mode') as ColorPref) || 'system');

  const notify = useCallback((type: FlashbarProps.Type, content: string) => {
    const id = String(Date.now() + Math.random());
    setFlash((f) => [...f, { type, content, id, dismissible: true, onDismiss: () => setFlash((x) => x.filter((m) => m.id !== id)) }]);
    if (type === 'success') setTimeout(() => setFlash((x) => x.filter((m) => m.id !== id)), 4000);
  }, []);

  useEffect(() => {
    if (user) api.me().then(setMe).catch((e) => notify('error', e.message));
    else setMe(null);
  }, [user, notify]);

  const studio = location.pathname === '/' || location.pathname.startsWith('/studio/') || location.pathname === '/projects';
  // Navigation: collapsed by default on the chat-first studio and the editor (more canvas), open elsewhere;
  // always toggleable with the hamburger button. (Hooks must run before the early returns below.)
  const [navOpen, setNavOpen] = useState(!studio);
  useEffect(() => {
    const block = (e: DragEvent) => { if (e.dataTransfer?.types.includes('Files')) e.preventDefault(); };
    window.addEventListener('dragover', block);
    window.addEventListener('drop', block);
    return () => { window.removeEventListener('dragover', block); window.removeEventListener('drop', block); };
  }, []);
  useEffect(() => { setNavOpen(!studio); }, [studio]);

  if (loading) return <div className="login-wrap"><Spinner size="large" /></div>;
  if (!user) return <LoginPage />;

  const editor = location.pathname.startsWith('/studio/');
  return (
    <CsI18nProvider locale={lang} messages={[lang === 'ko' ? csMessagesKo : csMessagesEn]}>
      <Ctx.Provider value={{ me, notify }}>
        <div id="top-nav" style={{ position: 'sticky', top: 0, zIndex: 1002 }}>
          <TopNavigation
            identity={{ href: '/', title: `${t('appName')} — ${t('appSubtitle')}`, onFollow: (e) => { e.preventDefault(); navigate('/'); } }}
            utilities={[
              {
                type: 'menu-dropdown', text: t(`theme_${colorPref}` as never), ariaLabel: t('theme'),
                items: (['light', 'dark', 'system'] as ColorPref[]).map((m) => ({ id: m, text: t(`theme_${m}` as never), checked: colorPref === m, itemType: 'checkbox' as const })),
                onItemClick: (e) => { const m = e.detail.id as ColorPref; setColorPref(m); applyColorPref(m); },
              },
              { type: 'button', text: t('language'), onClick: () => setLang(lang === 'ko' ? 'en' : 'ko') },
              {
                type: 'menu-dropdown',
                text: me?.email ?? user.username,
                description: me?.tenantName,
                iconName: 'user-profile',
                items: [{ id: 'signout', text: t('signOut') }],
                onItemClick: (e) => { if (e.detail.id === 'signout') signOut(); },
              },
            ]}
          />
        </div>
        <AppLayout
          headerSelector="#top-nav"
          toolsHide
          disableContentPaddings={editor}
          maxContentWidth={editor ? Number.MAX_VALUE : undefined}
          navigationOpen={navOpen}
          onNavigationChange={({ detail }) => setNavOpen(detail.open)}
          ariaLabels={{ navigationToggle: t('openMenu'), navigationClose: t('closeMenu'), navigation: t('menu') }}
          navigation={
            <SideNavigation
              activeHref={location.pathname.startsWith('/studio') ? '/' : '/' + (location.pathname.split('/')[1] ?? '')}
              onFollow={(e) => { e.preventDefault(); navigate(e.detail.href); if (studio) setNavOpen(false); }}
              items={[
                { type: 'link', text: t('navStudio'), href: '/' },
                { type: 'link', text: t('navProjects'), href: '/projects' },
                { type: 'link', text: t('navArchive'), href: '/archive' },
                { type: 'link', text: t('navLibrary'), href: '/library' },
                ...(me?.tenantRole === 'admin' ? [{ type: 'link' as const, text: t('navAdmin'), href: '/admin' }] : []),
              ]}
            />
          }
          notifications={<Flashbar items={flash} stackItems />}
          content={
            <Routes>
              <Route path="/" element={<StudioPage />} />
              <Route path="/studio/:projectId" element={<ProjectPage />} />
              <Route path="/projects" element={<ProjectsPage />} />
              <Route path="/archive" element={<ArchivePage />} />
              <Route path="/library" element={<LibraryPage />} />
              <Route path="/batches/:batchId" element={<BatchPage />} />
              <Route path="/archive/:assetId" element={<AssetPage />} />
              <Route path="/admin" element={<AdminPage />} />
              <Route path="*" element={<Navigate to="/" />} />
            </Routes>
          }
        />
      </Ctx.Provider>
    </CsI18nProvider>
  );
}
