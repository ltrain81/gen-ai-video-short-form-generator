import { createContext, ReactNode, useContext, useMemo, useState } from 'react';
import { ko } from './i18n/ko';
import { en } from './i18n/en';

export type Lang = 'ko' | 'en';
type Dict = typeof ko;
const dicts: Record<Lang, Dict> = { ko, en };

const I18nContext = createContext<{ lang: Lang; setLang: (l: Lang) => void; t: (k: keyof Dict, vars?: Record<string, string | number>) => string }>(
  null as never,
);

export function I18nProvider({ children }: { children: ReactNode }) {
  const [lang, setLangState] = useState<Lang>((localStorage.getItem('lang') as Lang) || (navigator.language.startsWith('ko') ? 'ko' : 'en'));
  const value = useMemo(() => ({
    lang,
    setLang: (l: Lang) => { localStorage.setItem('lang', l); setLangState(l); document.documentElement.lang = l; },
    t: (k: keyof Dict, vars?: Record<string, string | number>) =>
      Object.entries(vars ?? {}).reduce((s, [vk, vv]) => s.replace(`{${vk}}`, String(vv)), dicts[lang][k] ?? String(k)),
  }), [lang]);
  return <I18nContext.Provider value={value}>{children}</I18nContext.Provider>;
}

export const useI18n = () => useContext(I18nContext);
