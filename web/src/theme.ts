import { applyMode, Mode } from '@cloudscape-design/global-styles';

export type ColorPref = 'light' | 'dark' | 'system';
const media = typeof window !== 'undefined' ? window.matchMedia?.('(prefers-color-scheme: dark)') : undefined;

/** Light / dark / follow-system color mode (Cloudscape tokens switch automatically). */
export function applyColorPref(pref: ColorPref) {
  localStorage.setItem('mode', pref);
  const dark = pref === 'dark' || (pref === 'system' && !!media?.matches);
  applyMode(dark ? Mode.Dark : Mode.Light);
  document.documentElement.style.colorScheme = dark ? 'dark' : 'light';
}

export function watchSystemColor() {
  media?.addEventListener('change', () => { if ((localStorage.getItem('mode') || 'system') === 'system') applyColorPref('system'); });
}
