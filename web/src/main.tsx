import '@cloudscape-design/global-styles/index.css';
import { applyMode, Mode } from '@cloudscape-design/global-styles';
import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import { BrowserRouter } from 'react-router-dom';
import App from './App';
import { AuthProvider, configureAuth } from './auth';
import { loadConfig } from './config';
import { I18nProvider } from './i18n';
import './styles.css';

const prefersDark = window.matchMedia?.('(prefers-color-scheme: dark)').matches;
applyMode(localStorage.getItem('mode') === 'dark' || (prefersDark && !localStorage.getItem('mode')) ? Mode.Dark : Mode.Light);

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
