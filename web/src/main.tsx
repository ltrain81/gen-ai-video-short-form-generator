import '@cloudscape-design/global-styles/index.css';
import { setThemeClass, Theme } from '@cloudscape-design/global-styles';
import { applyColorPref, type ColorPref, watchSystemColor } from './theme';
import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import { BrowserRouter } from 'react-router-dom';
import App from './App';
import { AuthProvider, configureAuth } from './auth';
import { loadConfig } from './config';
import { I18nProvider } from './i18n';
import './styles.css';

// Cloudscape One Theme (public tokens). The Ember Modern typeface ships only with the internal global-styles
// package; the open-source release is planned for Q1 2027 — until then the default font is used.
setThemeClass(Theme.OneTheme, document.body);

applyColorPref((localStorage.getItem('mode') as ColorPref) || 'system');
watchSystemColor();

loadConfig().then(() => {
  configureAuth();
  createRoot(document.getElementById('root')!).render(
    <StrictMode>
      <I18nProvider>
        <AuthProvider>
          <BrowserRouter>
            <App />
          </BrowserRouter>
        </AuthProvider>
      </I18nProvider>
    </StrictMode>,
  );
}).catch((e) => {
  document.getElementById('root')!.textContent = String(e);
});
