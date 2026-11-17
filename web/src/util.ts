export function fmtTime(sec?: number): string {
  if (sec == null || Number.isNaN(sec)) return '–';
  const s = Math.max(0, sec);
  const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), r = Math.floor(s % 60);
  return h ? `${h}:${String(m).padStart(2, '0')}:${String(r).padStart(2, '0')}` : `${m}:${String(r).padStart(2, '0')}`;
}

export function fmtBytes(n?: number): string {
  if (!n) return '–';
  const u = ['B', 'KB', 'MB', 'GB', 'TB'];
  const i = Math.min(u.length - 1, Math.floor(Math.log(n) / Math.log(1024)));
  return `${(n / 1024 ** i).toFixed(i ? 1 : 0)} ${u[i]}`;
}

export function fmtDate(ms?: number): string {
  return ms ? new Date(ms).toLocaleString() : '–';
}

export function fileToBase64(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const r = new FileReader();
    r.onload = () => resolve(String(r.result).split(',')[1]);
    r.onerror = () => reject(r.error);
    r.readAsDataURL(file);
  });
}

/** Keep the order in which the user clicked (it becomes the clip order). */
export function mergeOrdered<T extends { assetId: string }>(prev: T[], next: T[]): T[] {
  const ids = new Set(next.map((a) => a.assetId));
  const kept = prev.filter((a) => ids.has(a.assetId));
  const keptIds = new Set(kept.map((a) => a.assetId));
  return [...kept, ...next.filter((a) => !keptIds.has(a.assetId))];
}
