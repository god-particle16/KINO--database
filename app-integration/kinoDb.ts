/**
 * KINO database client. Copy this file into your app as src/services/kinoDb.ts
 * (this is the replacement for the TMDB calls).
 *
 * Data is static JSON served from GitHub, refreshed daily by GitHub Actions.
 * Two mirrors are tried in order, so it still works if one is blocked on your network.
 */

const REPO = 'god-particle16/KINO--database';
const BASES = [
  `https://raw.githubusercontent.com/${REPO}/main/data`,
  `https://cdn.jsdelivr.net/gh/${REPO}@main/data`,
];

export type MediaType = 'anime' | 'series' | 'movies';

export interface KinoItem {
  id: string;               // e.g. "anilist:154587", "tvmaze:82", "tmdb:603"
  type: 'anime' | 'series' | 'movie';
  title: string;
  original_title?: string;
  overview?: string;
  year?: number;
  release_date?: string;
  genres?: string[];
  rating?: number;          // 0-10
  popularity?: number;
  poster?: string;
  backdrop?: string;
  status?: string;
  episodes?: number;
  runtime?: number;
  network?: string;
  next_episode?: number;
  next_episode_at?: string; // ISO date (anime)
}

export interface SearchEntry { id: string; t: string; o?: string; y?: number; p?: string }
export interface ScheduleEntry {
  show_id: string; date: string; time: string | null;
  season: number | null; episode: number | null; name: string | null;
}

const cache = new Map<string, Promise<any>>();

async function getJson<T>(path: string): Promise<T> {
  if (!cache.has(path)) {
    cache.set(path, (async () => {
      let lastErr: unknown;
      for (const base of BASES) {
        try {
          const res = await fetch(`${base}/${path}`);
          if (res.ok) return res.json();
          lastErr = new Error(`${res.status} ${path}`);
        } catch (e) { lastErr = e; }
      }
      cache.delete(path);
      throw lastErr;
    })());
  }
  return cache.get(path)!;
}

/** Full catalog for one media type: { [id]: KinoItem } */
export const getCatalog = (t: MediaType) =>
  getJson<Record<string, KinoItem>>(`catalog/${t}.json`);

/**
 * Ordered list of items. Lists:
 *   anime : trending, popular, airing, upcoming
 *   series: airing_today, popular
 *   movies: trending, popular, now_playing, upcoming, top_rated, tamil, telugu, hindi, malayalam
 */
export async function getList(t: MediaType, name: string): Promise<KinoItem[]> {
  const [ids, catalog] = await Promise.all([
    getJson<string[]>(`lists/${t}_${name}.json`),
    getCatalog(t),
  ]);
  return ids.map(id => catalog[id]).filter(Boolean);
}

/** Search (runs locally on the compact index, no server needed). */
export async function search(t: MediaType, query: string, limit = 30): Promise<KinoItem[]> {
  const q = query.trim().toLowerCase();
  if (!q) return [];
  const [index, catalog] = await Promise.all([
    getJson<SearchEntry[]>(`search/${t}.json`),
    getCatalog(t),
  ]);
  return index
    .filter(e => e.t?.toLowerCase().includes(q) || e.o?.toLowerCase().includes(q))
    .slice(0, limit)
    .map(e => catalog[e.id])
    .filter(Boolean);
}

export async function searchAll(query: string): Promise<KinoItem[]> {
  const parts = await Promise.all((['anime', 'series', 'movies'] as MediaType[]).map(t =>
    search(t, query, 15).catch(() => [] as KinoItem[])));
  return parts.flat();
}

/** Episodes airing in the next 14 days (series). */
export const getSchedule = () => getJson<ScheduleEntry[]>('series_schedule.json');

/** When the database was last refreshed, plus item counts. */
export const getMeta = () =>
  getJson<{ updated_at: string; counts: Record<MediaType, number> }>('index.json');
