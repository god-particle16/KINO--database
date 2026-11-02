# KINO database

Auto-updating media database for the KINO app (anime, series, movies).
A GitHub Action runs every day at 06:00 IST, fetches fresh data and publishes it to the
**`data` branch** (reset to one commit each run, so the repo never grows). `main` holds only the code.
No server and no manual work. The app just reads the JSON files.

| Type | Source | Key needed |
|------|--------|-----------|
| **All movies + series worldwide** (100k+ titles: title, year, genres, runtime, IMDb rating) | IMDb public datasets | no |
| Posters + descriptions for those | TVMaze (series), TMDB (movies and fallback), a few thousand titles per day, most popular first | TMDB only |
| Anime | AniList | no |
| Series (+ 14-day episode schedule) | TVMaze | no |
| Movies (incl. Tamil, Telugu, Hindi, Malayalam lists) | TMDB, fetched on GitHub's servers | yes, optional |

## One-time setup

1. Push this repo (already done) and make sure it is **Public**, so the app can read the files without a token.
2. **Settings → Actions → General → Workflow permissions** → select *Read and write permissions*.
3. *(Movies only)* **Settings → Secrets and variables → Actions → New repository secret**:
   name `TMDB_API_KEY`, value = your TMDB API key (v3 key or v4 read token).
   Without it, anime and series still update; movies are skipped.
4. **Actions** tab → *Update KINO database* → **Run workflow** to fill the data for the first time.
   After that it runs by itself every day.

## Data layout (on the `data` branch)

```
index.json                       last update time + counts
catalog/{anime,series,movies}.json   live catalogs: trending/airing/new titles with posters (grows daily)
lists/<type>_<name>.json         ordered id lists (trending, popular, airing ...)
search/<type>.json               compact search index for the live catalogs
series_schedule.json             episodes airing in the next 14 days
bulk/{movies,series}/s00..s63.json   GLOBAL catalog, sharded by IMDb number
bulk/{movies,series}/search/<letter>.json   global search index
bulk/{movies,series}/{top_rated,most_voted,recent}.json   500-title lists
bulk/meta.json                   counts and poster-fill progress
```

### How many titles?
By default titles with fewer than **1000 votes (movies)** / **300 votes (series)** are left out, which removes
unreleased and obscure entries and keeps the files small. To include more, set repository **variables**
`MIN_VOTES_MOVIES` / `MIN_VOTES_SERIES` (Settings → Secrets and variables → Actions → Variables), e.g. `200` / `100`.

### Posters
IMDb has no images. Posters and descriptions are filled in gradually (series ~3000/day, movies ~6000/day,
most popular first). Movies need the `TMDB_API_KEY` secret for this. Titles with no poster yet still show up
in search and lists, so the app should use a placeholder image.

IMDb data is licensed for personal, non-commercial use.

Every item has the same shape: `id, type, title, overview, year, release_date, genres, rating (0-10), poster, backdrop, status, episodes, runtime ...`

## Using it in the app

Copy `app-integration/kinoDb.ts` into the app as `src/services/kinoDb.ts`, then:

```ts
import { getList, searchGlobal, getBulkList, getSchedule } from './services/kinoDb';

const trending = await getList('anime', 'trending');          // live lists
const { live, movies, series } = await searchGlobal('breaking'); // whole world
const top = await getBulkList('movies', 'top_rated', 50);        // global top rated
```

## Local test

```
python3 tests/test_update_db.py     # offline, mocked APIs
python3 scripts/update_db.py        # real run, writes to data/
```
