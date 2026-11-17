import Box from '@cloudscape-design/components/box';
import Button from '@cloudscape-design/components/button';
import SpaceBetween from '@cloudscape-design/components/space-between';
import Toggle from '@cloudscape-design/components/toggle';
import type { CaptionItem, ClipItem, Item, TextItem, Timeline } from '@shorts/timeline';
import { sampleKeyframes, timelineDuration } from '@shorts/timeline';
import { CSSProperties, useCallback, useEffect, useMemo, useRef, useState } from 'react';
import type { AssetMedia } from '../api/types';
import { fmtTime } from '../util';

/**
 * Interim live preview (DOM compositor) driven by the timeline JSON.
 * Main track sequencing, reframe crop path, B-roll overlay, text, word-timed captions, music.
 * Phase 4 replaces this with a WebGL compositor that matches the server renderer frame-for-frame.
 */
interface Props {
  timeline: Timeline;
  media: Record<string, AssetMedia>;
  time: number;
  onTime: (t: number) => void;
  selectedId?: string;
}

type ReframeValue = { mode?: string; layout?: string; path?: { t: number; x: number; y: number; scale?: number }[] };

function activeAt(items: Item[], t: number): Item | undefined {
  return items.find((i) => t >= i.start && t < i.start + i.duration);
}

function srcTime(c: ClipItem, t: number): number {
  return c.srcIn + (t - c.start) * (c.speed ?? 1);
}

function focusAt(c: ClipItem, st: number): { x: number; y: number; scale: number; layout: string } {
  const r = c.reframe as ReframeValue | undefined;
  const path = r?.path ?? [];
  if (!path.length) return { x: c.transform?.x ?? 0.5, y: c.transform?.y ?? 0.5, scale: c.transform?.scale ?? 1, layout: r?.layout ?? 'crop' };
  let p = path[0];
  for (const q of path) if (q.t <= st) p = q;
  return { x: p.x, y: p.y, scale: p.scale ?? 1, layout: r?.layout ?? 'crop' };
}

function mediaStyle(c: ClipItem, st: number, fit: 'cover' | 'contain'): CSSProperties {
  const f = focusAt(c, st);
  const rel = 0;
  const opacity = sampleKeyframes(c.keyframes?.opacity, rel, c.opacity ?? 1);
  const contain = f.layout === 'fit_blur' || f.layout === 'letterbox' || f.layout === 'split' || fit === 'contain';
  return {
    position: 'absolute', inset: 0, width: '100%', height: '100%',
    objectFit: contain ? 'contain' : 'cover',
    objectPosition: `${(f.x * 100).toFixed(1)}% ${(f.y * 100).toFixed(1)}%`,
    transform: `scale(${f.scale})`, transformOrigin: `${f.x * 100}% ${f.y * 100}%`, opacity,
  };
}

export default function Preview({ timeline, media, time, onTime, selectedId }: Props) {
  const stageRef = useRef<HTMLDivElement>(null);
  const mainRef = useRef<HTMLVideoElement>(null);
  const overlayRef = useRef<HTMLVideoElement>(null);
  const musicRef = useRef<HTMLAudioElement>(null);
  const [playing, setPlaying] = useState(false);
  const [safe, setSafe] = useState(true);
  const [stageW, setStageW] = useState(360);
  const clock = useRef<{ t0: number; wall: number } | null>(null);
  const duration = useMemo(() => timelineDuration(timeline), [timeline]);
  const { width, height } = timeline.settings;
  const tracks = timeline.tracks;
  const main = tracks.find((t) => t.kind === 'video');
  const overlay = tracks.find((t) => t.kind === 'overlay');
  const texts = tracks.filter((t) => t.kind === 'text' && !t.hidden).flatMap((t) => t.items) as TextItem[];
  const captions = tracks.filter((t) => t.kind === 'caption' && !t.hidden).flatMap((t) => t.items) as CaptionItem[];
  const music = tracks.find((t) => t.kind === 'audio' && t.audioRole === 'music');

  const mainItem = main && activeAt(main.items, time) as ClipItem | undefined;
  const overlayItem = overlay && activeAt(overlay.items, time) as ClipItem | undefined;
  const musicItem = music && activeAt(music.items, time) as ClipItem | undefined;

  // Fit the stage into the available box (max 70vh tall).
  useEffect(() => {
    const el = stageRef.current?.parentElement;
    if (!el) return;
    const ro = new ResizeObserver(() => {
      const maxH = Math.max(240, window.innerHeight - 380);
      setStageW(Math.min(el.clientWidth, (maxH * width) / height));
    });
    ro.observe(el);
    return () => ro.disconnect();
  }, [width, height]);

  // Keep media elements on the right source/time.
  const syncEl = useCallback((el: HTMLMediaElement | null, c: ClipItem | undefined, t: number, url?: string) => {
    if (!el) return;
    if (!c || !url) { if (!el.paused) el.pause(); return; }
    const want = srcTime(c, t);
    if (el.dataset.src !== url) {
      el.dataset.src = url; el.src = url;
      // Seeking before metadata is ignored by browsers: re-apply once it is known.
      el.addEventListener('loadedmetadata', () => { el.currentTime = Number(el.dataset.want ?? want); }, { once: true });
    }
    el.dataset.want = String(want);
    if (el.readyState === 0 && el.networkState !== el.NETWORK_LOADING) el.load();
    if (Math.abs(el.currentTime - want) > (playing ? 0.35 : 0.04)) el.currentTime = want;
    el.playbackRate = c.speed ?? 1;
    el.volume = Math.min(1, Math.max(0, c.volume ?? 1));
    if (playing && el.paused) el.play().catch(() => undefined);
    if (!playing && !el.paused) el.pause();
  }, [playing]);

  useEffect(() => {
    syncEl(mainRef.current, mainItem?.mediaKind === 'video' ? mainItem : undefined, time, mainItem && media[mainItem.assetId]?.proxyUrl);
    syncEl(overlayRef.current, overlayItem?.mediaKind === 'video' ? overlayItem : undefined, time, overlayItem && media[overlayItem.assetId]?.proxyUrl);
    const mu = musicItem && (media[musicItem.assetId]?.audioUrl ?? media[musicItem.assetId]?.proxyUrl);
    syncEl(musicRef.current, musicItem, time, mu);
    if (musicRef.current) musicRef.current.volume = Math.min(1, (music?.volume ?? 0.6) * (musicItem?.volume ?? 1));
  }, [time, mainItem, overlayItem, musicItem, media, syncEl, music?.volume]);

  // Playback clock (wall-clock based; media elements follow).
  useEffect(() => {
    if (!playing) { clock.current = null; return; }
    clock.current = { t0: time, wall: performance.now() };
    let raf = 0;
    const tick = () => {
      const c = clock.current!;
      const t = c.t0 + (performance.now() - c.wall) / 1000;
      if (t >= duration) { setPlaying(false); onTime(0); return; }
      onTime(t);
      raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [playing, duration]);

  const scale = stageW / width;
  const imageEl = (c: ClipItem) => {
    const m = media[c.assetId];
    const url = c.page != null && m?.pages ? m.pages.find((p) => p.index === c.page)?.url : (m?.imageUrl ?? m?.thumbnailUrl);
    const u = Math.min(1, Math.max(0, (time - c.start) / Math.max(0.01, c.duration)));
    const kb = c.kenBurns;
    const s = kb ? kb.from.scale + (kb.to.scale - kb.from.scale) * u : 1;
    const x = kb ? kb.from.x + (kb.to.x - kb.from.x) * u : 0.5;
    const y = kb ? kb.from.y + (kb.to.y - kb.from.y) * u : 0.5;
    return url ? <img src={url} alt="" style={{ position: 'absolute', inset: 0, width: '100%', height: '100%', objectFit: 'cover', transform: `scale(${s})`, transformOrigin: `${x * 100}% ${y * 100}%` }} /> : null;
  };
  const blurBg = (c?: ClipItem) => {
    if (!c) return null;
    const f = c.mediaKind === 'video' ? focusAt(c, srcTime(c, time)) : null;
    if (!f || (f.layout !== 'fit_blur' && f.layout !== 'split')) return null;
    const poster = media[c.assetId]?.thumbnailUrl;
    return poster ? <img src={poster} alt="" style={{ position: 'absolute', inset: '-10%', width: '120%', height: '120%', objectFit: 'cover', filter: 'blur(28px) brightness(0.6)' }} /> : null;
  };

  return (
    <SpaceBetween size="s">
      <div style={{ display: 'flex', justifyContent: 'center' }}>
        <div ref={stageRef} style={{ position: 'relative', width: stageW, height: (stageW * height) / width, background: timeline.settings.background, overflow: 'hidden', borderRadius: 4 }}>
          {blurBg(mainItem)}
          <video ref={mainRef} muted={main?.muted} playsInline preload="auto"
            style={mainItem?.mediaKind === 'video' ? mediaStyle(mainItem, srcTime(mainItem, time), 'cover') : { display: 'none' }} />
          {mainItem?.mediaKind === 'image' && imageEl(mainItem)}
          <video ref={overlayRef} muted playsInline preload="auto"
            style={overlayItem?.mediaKind === 'video' ? mediaStyle(overlayItem, srcTime(overlayItem, time), 'cover') : { display: 'none' }} />
          {overlayItem?.mediaKind === 'image' && imageEl(overlayItem)}
          <audio ref={musicRef} preload="auto" />
          {texts.filter((x) => time >= x.start && time < x.start + x.duration).map((x) => (
            <div key={x.id} style={{
              position: 'absolute', left: `${(x.position?.x ?? 0.5) * 100}%`, top: `${(x.position?.y ?? 0.2) * 100}%`, transform: 'translate(-50%, -50%)',
              width: `${(x.position?.maxWidth ?? 0.86) * 100}%`, textAlign: x.style.align ?? 'center', color: x.style.color ?? '#fff',
              fontSize: (Number(x.style.size) || 0.05) * height * scale, fontWeight: x.style.weight ?? 800, lineHeight: 1.15, whiteSpace: 'pre-line',
              fontFamily: `${x.style.font}, Pretendard, 'Apple SD Gothic Neo', 'Noto Sans KR', sans-serif`,
              background: x.style.background, padding: x.style.background ? '0.15em 0.4em' : 0, borderRadius: 3,
              WebkitTextStroke: x.style.stroke ? `${x.style.stroke.width * height * scale}px ${x.style.stroke.color}` : undefined,
              paintOrder: 'stroke fill', textTransform: x.style.uppercase ? 'uppercase' : 'none',
              outline: x.id === selectedId ? '2px dashed #22d3ee' : undefined,
            }}>{x.text}</div>
          ))}
          {captions.filter((c) => time >= c.start && time < c.start + c.duration).map((c) => (
            <div key={c.id} style={{
              position: 'absolute', left: '7%', right: '7%', top: `${c.position.y * 100}%`, transform: 'translateY(-50%)', textAlign: 'center',
              fontSize: (Number(c.style.size) || 0.045) * height * scale, fontWeight: c.style.weight ?? 800, color: c.style.color, lineHeight: 1.2,
              fontFamily: `${c.style.font}, Pretendard, 'Apple SD Gothic Neo', 'Noto Sans KR', sans-serif`,
              textTransform: c.style.uppercase ? 'uppercase' : 'none',
              WebkitTextStroke: c.style.stroke ? `${c.style.stroke.width * height * scale}px ${c.style.stroke.color}` : undefined, paintOrder: 'stroke fill',
              textShadow: c.style.shadow ? `${c.style.shadow.x}px ${c.style.shadow.y}px ${c.style.shadow.blur}px ${c.style.shadow.color}` : undefined,
            }}>
              <span style={{ background: c.style.background, padding: c.style.background ? '0.1em 0.35em' : 0, borderRadius: 6 }}>
                {c.words.map((w, i) => (
                  <span key={i} style={{ color: c.style.highlightColor && time >= w.s && time < w.e ? c.style.highlightColor : undefined }}>{w.w} </span>
                ))}
              </span>
            </div>
          ))}
          {safe && timeline.settings.safeZone && timeline.settings.safeZone !== 'none' && (
            <div style={{ position: 'absolute', left: '6%', right: '18%', top: '12%', bottom: '20%', border: '1px dashed rgba(255,255,255,0.35)', pointerEvents: 'none' }} />
          )}
          {!mainItem && duration === 0 && <Box textAlign="center" color="inherit" padding="xxl"><span style={{ color: '#9ca3af' }}>—</span></Box>}
        </div>
      </div>
      <div style={{ display: 'flex', justifyContent: 'center' }}><SpaceBetween direction="horizontal" size="xs" alignItems="center">
        <Button iconName={playing ? 'pause' : 'caret-right-filled'} variant="icon" ariaLabel="play" onClick={() => setPlaying(!playing)} disabled={!duration} />
        <input type="range" min={0} max={Math.max(0.01, duration)} step={0.01} value={Math.min(time, duration)} style={{ width: `min(${Math.max(240, stageW)}px, 40vw)` }}
          onChange={(e) => { setPlaying(false); onTime(Number(e.target.value)); }} />
        <Box variant="small">{fmtTime(time)} / {fmtTime(duration)}</Box>
        <Toggle checked={safe} onChange={(e) => setSafe(e.detail.checked)}>safe zone</Toggle>
      </SpaceBetween></div>
    </SpaceBetween>
  );
}
