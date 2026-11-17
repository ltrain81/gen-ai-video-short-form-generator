export interface RuntimeConfig {
  apiUrl: string;
  region: string;
  userPoolId: string;
  userPoolClientId: string;
  mode: 'edit' | 'full';
}

let config: RuntimeConfig | undefined;

/** Loaded at startup from /config.json (written by the WebDeploy stack, or public/config.json in dev). */
export async function loadConfig(): Promise<RuntimeConfig> {
  if (!config) {
    const res = await fetch('/config.json', { cache: 'no-store' });
    if (!res.ok) throw new Error('config.json missing — deploy the stack or create web/public/config.json');
    config = (await res.json()) as RuntimeConfig;
  }
  return config;
}

export function getConfig(): RuntimeConfig {
  if (!config) throw new Error('Config not loaded');
  return config;
}
