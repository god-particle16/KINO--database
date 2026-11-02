# KINO database

Auto-updating media database for the KINO app (anime, series, movies).
A GitHub Action runs every day at 06:00 IST, fetches fresh data and commits it to `data/`.
No server and no manual work. The app just reads the JSON files.

| Type | Source | Key needed |
|------|--------|-----------|
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

## Data layout

```
data/index.json            last update time + counts
data/catalog/{anime,series,movies}.json   every item ever seen, keyed by id (grows daily)
data/lists/<type>_<name>.json             ordered id lists (trending, popular, airing ...)
data/search/<type>.json                   compact search index
data/series_schedule.json                 episodes airing in the next 14 days
```

Every item has the same shape: `id, type, title, overview, year, release_date, genres, rating (0-10), poster, backdrop, status, episodes, runtime ...`

## Using it in the app

Copy `app-integration/kinoDb.ts` into the app as `src/services/kinoDb.ts`, then:

```ts
import { getList, search, getSchedule } from './services/kinoDb';

const trending = await getList('anime', 'trending');
const results  = await search('series', 'breaking');
```

## Local test

```
python3 tests/test_update_db.py     # offline, mocked APIs
python3 scripts/update_db.py        # real run, writes to data/
```
