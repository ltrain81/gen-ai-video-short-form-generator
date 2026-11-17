export type AssetKind = 'video' | 'image' | 'audio' | 'document' | 'font' | 'other';
export type Role = 'owner' | 'editor' | 'user';

export interface Asset {
  assetId: string;
  kind?: AssetKind;
  status: 'uploading' | 'queued' | 'processing' | 'ready' | 'failed' | 'aborted';
  title?: string;
  aiTitle?: string;
  aiSummary?: string;
  displayTitle: string;
  filename: string;
  contentType?: string;
  durationSec?: number;
  width?: number;
  height?: number;
  pageCount?: number;
  sizeBytes?: number;
  tags?: string[];
  people?: { name: string }[];
  collectionIds?: string[];
  ownerId: string;
  createdAt: number;
  role?: Role;
  thumbnailUrl?: string;
  error?: string;
  deletedAt?: number;
  processing?: { status?: string; stages: Record<string, string>; error?: string };
  analysisSummary?: { title?: string; summary?: string; topics?: string[]; speakers?: string[]; language?: string; genre?: string; mood?: string; tags?: string[] };
  grants?: Grant[] | null;
  analysisLanguage?: string;
}

export interface Grant { principal: string; role: Role; expiresAt?: number; grantedBy?: string }
export interface Collection { collectionId: string; name: string; ownerId: string; role: Role; createdAt: number }
export interface Group { groupId: string; name: string; member?: boolean }
export interface TenantUser { sub: string; email?: string; name?: string; tenantRole: 'admin' | 'member' }
export interface Me { sub: string; email: string; tenantId: string; tenantName?: string; tenantRole: 'admin' | 'member'; language: string; languages: Record<string, string>; groups: Group[]; features: { generation: boolean } }

export interface Segment { startSec: number; endSec: number; modality: string; modalities?: string[]; score: number; text?: string; page?: number }
export interface SearchResult { asset: Asset; score: number; segments: Segment[] }

export interface Scene { start_sec: number; end_sec: number; summary?: string; on_screen_text?: string; shot_type?: string; highlight_score?: number; tags?: string; title?: string }
export interface Analysis {
  summary: Record<string, unknown> | null;
  scenes: Scene[] | null;
  chapters: Scene[] | null;
  transcript: { words: { s: number; e: number; w: string; spk?: string }[]; sentences: { s: number; e: number; text: string; spk?: string }[] } | null;
}
export interface AssetMedia {
  kind: AssetKind;
  durationSec?: number;
  width?: number;
  height?: number;
  fps?: number;
  proxyUrl?: string;
  scrubUrl?: string;
  audioUrl?: string;
  waveformUrl?: string;
  thumbnailUrl?: string;
  imageUrl?: string;
  pages?: { index: number; url: string; width: number; height: number }[];
  sprites?: { intervalSec: number; cols: number; rows: number; tileWidth: number; sheets: { url: string; firstFrame: number; frames: number }[] };
}
