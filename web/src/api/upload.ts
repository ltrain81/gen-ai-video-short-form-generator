import { api } from './client';

export interface UploadProgress { assetId?: string; file: File; loaded: number; total: number; status: 'pending' | 'uploading' | 'done' | 'error'; error?: string }

const CONCURRENCY = 4;
const URL_BATCH = 50;

/** Resumable-friendly multipart upload straight to S3 via presigned part URLs. */
export async function uploadFile(file: File, onProgress: (p: UploadProgress) => void,
  opts: { collectionId?: string; scope?: 'archive' | 'project'; projectId?: string } = {}): Promise<string> {
  const progress: UploadProgress = { file, loaded: 0, total: file.size, status: 'uploading' };
  onProgress({ ...progress });
  const created = await api.createAsset({
    filename: file.name, size: file.size, contentType: file.type || 'application/octet-stream', ...opts,
  });
  progress.assetId = created.assetId;
  const { partSize, partCount, assetId } = created;
  const etags: { partNumber: number; etag: string }[] = [];
  const loadedByPart = new Map<number, number>();
  try {
    let next = 1;
    let urls: Record<string, string> = {};
    const getUrl = async (n: number) => {
      if (!urls[n]) {
        const batch = Array.from({ length: Math.min(URL_BATCH, partCount - n + 1) }, (_, i) => n + i);
        urls = { ...urls, ...(await api.partUrls(assetId, batch)).urls };
      }
      return urls[n];
    };
    const worker = async () => {
      while (next <= partCount) {
        const n = next++;
        const blob = file.slice((n - 1) * partSize, Math.min(n * partSize, file.size));
        const etag = await putPart(await getUrl(n), blob, (loaded) => {
          loadedByPart.set(n, loaded);
          progress.loaded = [...loadedByPart.values()].reduce((a, b) => a + b, 0);
          onProgress({ ...progress });
        });
        etags.push({ partNumber: n, etag });
      }
    };
    await Promise.all(Array.from({ length: Math.min(CONCURRENCY, partCount) }, worker));
    await api.completeUpload(assetId, etags);
    onProgress({ ...progress, loaded: file.size, status: 'done' });
    return assetId;
  } catch (e) {
    await api.abortUpload(assetId).catch(() => undefined);
    onProgress({ ...progress, status: 'error', error: (e as Error).message });
    throw e;
  }
}

function putPart(url: string, blob: Blob, onProgress: (loaded: number) => void, attempt = 1): Promise<string> {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open('PUT', url);
    xhr.upload.onprogress = (e) => onProgress(e.loaded);
    xhr.onload = () => {
      const etag = xhr.getResponseHeader('ETag');
      if (xhr.status >= 200 && xhr.status < 300 && etag) resolve(etag);
      else if (attempt < 4) setTimeout(() => putPart(url, blob, onProgress, attempt + 1).then(resolve, reject), 1000 * attempt);
      else reject(new Error(`Part upload failed (${xhr.status})`));
    };
    xhr.onerror = () => (attempt < 4
      ? setTimeout(() => putPart(url, blob, onProgress, attempt + 1).then(resolve, reject), 1000 * attempt)
      : reject(new Error('Network error during upload')));
    xhr.send(blob);
  });
}
