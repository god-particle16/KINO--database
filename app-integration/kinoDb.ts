/**
 * KINO database client. Copy this file into your app as src/services/kinoDb.ts
 * (this is the replacement for the TMDB calls).
 *
 * Data is static JSON served from GitHub, refreshed daily by GitHub Actions.
 * Two mirrors are tried in order, so it still works if one is blocked on your network.
 */

const REPO = 'god-particle16/KINO--database';
// The database lives on the `data` branch (the `main` branch only holds code).
const BASES = [
  `https://raw.githubusercontent.com/${REPO}/data`,
  `https://cdn.jsdelivr.net/gh/${REPO}@data`,
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

// ---------------------------------------------------------------------------
// GLOBAL catalog: every movie and series on IMDb (100k+ titles), via bulk/.
// Items are sharded by IMDb number, search is sharded by first letter.
// ---------------------------------------------------------------------------
export type BulkType = 'movies' | 'series';
const SHARDS = 64;
const STOP = new Set(['the', 'a', 'an', 'of', 'and']);
const norm = (s: string) => s.toLowerCase().normalize('NFKD').replace(/[\u0300-\u036f]/g, '');
const words = (s: string) => norm(s).match(/[\p{L}\p{N}]+/gu) ?? [];

export interface BulkHit { id: string; imdbId: string; title: string; original?: string; year?: number | null; votes: number }

/** Look up one title by IMDb id (tt0111161). Poster/overview are filled in over time and may be missing. */
export async function getBulkItem(t: BulkType, imdbId: string): Promise<KinoItem | undefined> {
  const n = parseInt(imdbId.replace(/\D/g, ''), 10) % SHARDS;
  const shard = await getJson<Record<string, KinoItem>>(`bulk/${t}/s${String(n).padStart(2, '0')}.json`);
  return shard[imdbId];
}

/** Search the whole global catalog. Results are sorted by popularity (vote count). */
export async function searchBulk(t: BulkType, query: string, limit = 40): Promise<BulkHit[]> {
  const qw = words(query);
  if (!qw.length) return [];
  const first = qw.find(w => !STOP.has(w)) ?? qw[0];
  const c = first[0];
  const key = /[a-z0-9]/.test(c) ? c : '_';
  let rows: any[][];
  try { rows = await getJson<any[][]>(`bulk/${t}/search/${key}.json`); } catch { return []; }
  const out: BulkHit[] = [];
  for (const r of rows) {
    const hay = norm(`${r[1]} ${r[4] ?? ''}`);
    if (qw.every(w => hay.includes(w))) {
      out.push({ id: `imdb:${r[0]}`, imdbId: r[0], title: r[1], original: r[4], year: r[2], votes: r[3] });
      if (out.length >= limit) break;
    }
  }
  return out;
}

/** Curated global lists: 'top_rated' | 'most_voted' | 'recent' (500 titles each). */
export async function getBulkList(t: BulkType, name: 'top_rated' | 'most_voted' | 'recent', limit = 50) {
  const ids = (await getJson<string[]>(`bulk/${t}/${name}.json`)).slice(0, limit);
  const items = await Promise.all(ids.map(id => getBulkItem(t, id)));
  return items.filter(Boolean) as KinoItem[];
}

/** Search everything: live catalogs (anime/series/movies) + the global movie/series catalog. */
export async function searchGlobal(query: string) {
  const [live, movies, series] = await Promise.all([
    searchAll(query).catch(() => [] as KinoItem[]),
    searchBulk('movies', query, 20),
    searchBulk('series', query, 20),
  ]);
  return { live, movies, series };
}
