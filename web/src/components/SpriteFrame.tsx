import { CSSProperties } from 'react';
import type { AssetMedia } from '../api/types';

/** A frame of a video at time t from the ingest sprite sheets — shows instantly, no video download. */
export default function SpriteFrame({ m, t, style }: { m?: AssetMedia; t: number; style?: CSSProperties }) {
  const sp = m?.sprites;
  if (!sp || !sp.sheets?.length) return null;
  const idx = Math.max(0, Math.floor(t / sp.intervalSec));
  const sheet = sp.sheets.find((s) => idx >= s.firstFrame && idx < s.firstFrame + s.frames) ?? sp.sheets[sp.sheets.length - 1];
  const k = Math.min(sheet.frames - 1, Math.max(0, idx - sheet.firstFrame));
  const col = k % sp.cols, row = Math.floor(k / sp.cols);
  return (
    <div aria-hidden style={{
      backgroundImage: `url(${sheet.url})`, backgroundSize: `${sp.cols * 100}% ${sp.rows * 100}%`,
      backgroundPosition: `${sp.cols > 1 ? (col / (sp.cols - 1)) * 100 : 0}% ${sp.rows > 1 ? (row / (sp.rows - 1)) * 100 : 0}%`,
      aspectRatio: `${m?.width ?? 16} / ${m?.height ?? 9}`, ...style,
    }} />
  );
}
