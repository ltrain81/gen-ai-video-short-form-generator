import { accessToken } from '../auth';
import { getConfig } from '../config';
import type { Asset, Me, SearchResult, Analysis, AssetMedia, Grant, Collection, Group, TenantUser } from './types';

export class ApiError extends Error {
  constructor(public status: number, message: string) { super(message); }
}

async function call<T>(method: string, path: string, body?: unknown): Promise<T> {
  const res = await fetch(`${getConfig().apiUrl}${path}`, {
    method,
    headers: { Authorization: `Bearer ${await accessToken()}`, ...(body ? { 'Content-Type': 'application/json' } : {}) },
    body: body ? JSON.stringify(body) : undefined,
  });
  if (res.status === 204) return undefined as T;
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new ApiError(res.status, data.message ?? res.statusText);
  return data as T;
}

const enc = encodeURIComponent;

export const api = {
  me: () => call<Me>('GET', '/me'),
  updateTenant: (f: { name?: string; language?: string }) => call<{ name?: string; language?: string }>('PATCH', '/tenant', f),
  reanalyze: (id: string) => call<{ status: string; language: string }>('POST', `/assets/${id}/reanalyze`),
  assets: (q: { kind?: string; collectionId?: string; trash?: boolean } = {}) => {
    const p = new URLSearchParams();
    if (q.kind) p.set('kind', q.kind);
    if (q.collectionId) p.set('collectionId', q.collectionId);
    if (q.trash) p.set('trash', '1');
    return call<Asset[]>('GET', `/assets?${p}`);
  },
  asset: (id: string) => call<Asset>('GET', `/assets/${id}`),
  media: (id: string) => call<AssetMedia>('GET', `/assets/${id}/media`),
  analysis: (id: string) => call<Analysis>('GET', `/assets/${id}/analysis`),
  updateAsset: (id: string, fields: Partial<Pick<Asset, 'title' | 'tags' | 'people'>>) => call<Asset>('PATCH', `/assets/${id}`, fields),
  deleteAsset: (id: string) => call<void>('DELETE', `/assets/${id}`),
  restoreAsset: (id: string) => call<void>('POST', `/assets/${id}/restore`),
  download: (id: string) => call<{ url: string }>('GET', `/assets/${id}/download`),
  createAsset: (b: { filename: string; size: number; contentType: string; collectionId?: string; scope?: string; projectId?: string }) =>
    call<{ assetId: string; uploadId: string; partSize: number; partCount: number }>('POST', '/assets', b),
  partUrls: (id: string, partNumbers: number[]) => call<{ urls: Record<string, string> }>('POST', `/assets/${id}/upload/parts`, { partNumbers }),
  completeUpload: (id: string, parts: { partNumber: number; etag: string }[]) => call<void>('POST', `/assets/${id}/upload/complete`, { parts }),
  abortUpload: (id: string) => call<void>('POST', `/assets/${id}/upload/abort`),
  search: (b: { text?: string; imageBase64?: string; imageAssetId?: string; kinds?: string[]; modalities?: string[] }) =>
    call<SearchResult[]>('POST', '/search', b),
  grants: (id: string) => call<Grant[]>('GET', `/assets/${id}/grants`),
  putGrant: (id: string, g: { principal: string; role: string; expiresAt?: number }) => call<Grant>('PUT', `/assets/${id}/grants`, g),
  deleteGrant: (id: string, principal: string) => call<void>('DELETE', `/assets/${id}/grants/${enc(principal)}`),
  collections: () => call<Collection[]>('GET', '/collections'),
  createCollection: (name: string) => call<Collection>('POST', '/collections', { name }),
  addToCollection: (cid: string, assetIds: string[]) => call<void>('POST', `/collections/${cid}/assets`, { assetIds }),
  collectionGrants: (cid: string) => call<Grant[]>('GET', `/collections/${cid}/grants`),
  putCollectionGrant: (cid: string, g: { principal: string; role: string; expiresAt?: number }) => call<Grant>('PUT', `/collections/${cid}/grants`, g),
  deleteCollectionGrant: (cid: string, principal: string) => call<void>('DELETE', `/collections/${cid}/grants/${enc(principal)}`),
  users: () => call<TenantUser[]>('GET', '/users'),
  invite: (email: string, tenantRole: string) => call<TenantUser>('POST', '/users/invite', { email, tenantRole }),
  groups: () => call<Group[]>('GET', '/groups'),
  createGroup: (name: string) => call<Group>('POST', '/groups', { name }),
  groupMembers: (gid: string) => call<string[]>('GET', `/groups/${gid}/members`),
  addGroupMember: (gid: string, sub: string) => call<void>('POST', `/groups/${gid}/members`, { sub }),
  removeGroupMember: (gid: string, sub: string) => call<void>('DELETE', `/groups/${gid}/members/${sub}`),
  audit: (date?: string) => call<Record<string, unknown>[]>('GET', `/audit${date ? `?date=${date}` : ''}`),
};
