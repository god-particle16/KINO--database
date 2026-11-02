#!/usr/bin/env python3
"""KINO database updater.

Runs daily from GitHub Actions. Pulls fresh data and writes static JSON into data/.

Sources
  - Anime  : AniList GraphQL   (no key)
  - Series : TVMaze            (no key)
  - Movies : TMDB              (optional: set TMDB_API_KEY as a GitHub secret;
                                it is fetched from GitHub's servers, so it does
                                not matter if TMDB is blocked on your network)

Output (all under data/)
  index.json                 meta: last update time, counts, file list
  catalog/anime.json         every anime ever seen, keyed by id (grows over time)
  catalog/series.json        same for series
  catalog/movies.json        same for movies
  lists/anime_*.json         ordered lists of ids (trending, popular, airing, upcoming)
  lists/series_*.json
  lists/movies_*.json
  series_schedule.json       upcoming episodes for the next 14 days
  search/<type>.json         compact search index (id, title, year, poster)

Stdlib only, so the workflow needs no pip install.
"""
from __future__ import annotations

import datetime as dt
import html
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(os.environ.get("KINO_DATA_DIR", Path(__file__).resolve().parent.parent / "data"))
UA = "KINO-database-updater/1.0 (+https://github.com/god-particle16/KINO--database)"

# Hard limits so the repo never balloons.
MAX_CATALOG_ITEMS = 20000
SCHEDULE_DAYS = 14
TVMAZE_UPDATE_CAP = 120


# --------------------------------------------------------------------------- #
# HTTP helpers
# --------------------------------------------------------------------------- #
def http_json(url: str, *, data: bytes | None = None, headers: dict | None = None,
              retries: int = 4, timeout: int = 30):
    hdrs = {"User-Agent": UA, "Accept": "application/json"}
    if headers:
        hdrs.update(headers)
    last = None
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, data=data, headers=hdrs)
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            last = e
            if e.code == 404:
                return None
            wait = 2 ** attempt * 2
            if e.code == 429:
                wait = max(wait, int(e.headers.get("Retry-After", "10")))
            elif e.code < 500:
                break
            time.sleep(wait)
        except Exception as e:  # network hiccup, bad JSON, timeout
            last = e
            time.sleep(2 ** attempt * 2)
    raise RuntimeError(f"GET {url} failed: {last}")


def log(msg: str) -> None:
    print(msg, flush=True)


# --------------------------------------------------------------------------- #
# File helpers
# --------------------------------------------------------------------------- #
def read_json(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def write_json(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False, separators=(",", ":"), sort_keys=True),
                   encoding="utf-8")
    tmp.replace(path)


def strip_html(s: str | None) -> str:
    if not s:
        return ""
    s = re.sub(r"<br\s*/?>", "\n", s, flags=re.I)
    s = re.sub(r"<[^>]+>", "", s)
    return html.unescape(s).strip()


def year_of(date_str: str | None) -> int | None:
    if date_str and re.match(r"^\d{4}", date_str):
        return int(date_str[:4])
    return None


# --------------------------------------------------------------------------- #
# Catalog (persistent store of every item ever seen)
# --------------------------------------------------------------------------- #
class Catalog:
    def __init__(self, kind: str):
        self.kind = kind
        self.path = ROOT / "catalog" / f"{kind}.json"
        self.items: dict[str, dict] = read_json(self.path, {})

    def upsert(self, item: dict) -> str:
        old = self.items.get(item["id"], {})
        merged = {**old, **{k: v for k, v in item.items() if v not in (None, "", [])}}
        self.items[item["id"]] = merged
        return item["id"]

    def save(self) -> None:
        if len(self.items) > MAX_CATALOG_ITEMS:
            # drop the oldest-updated entries
            keep = sorted(self.items.values(), key=lambda x: x.get("seen", ""), reverse=True)
            self.items = {x["id"]: x for x in keep[:MAX_CATALOG_ITEMS]}
        write_json(self.path, self.items)
        search = [
            {"id": i["id"], "t": i.get("title"), "o": i.get("original_title"),
             "y": i.get("year"), "p": i.get("poster")}
            for i in self.items.values()
        ]
        search.sort(key=lambda x: (x["t"] or "").lower())
        write_json(ROOT / "search" / f"{self.kind}.json", search)


TODAY = dt.date.today().isoformat()


# --------------------------------------------------------------------------- #
# Anime: AniList
# --------------------------------------------------------------------------- #
ANILIST_URL = "https://graphql.anilist.co"
ANILIST_QUERY = """
query ($page: Int, $perPage: Int, $sort: [MediaSort], $status: MediaStatus) {
  Page(page: $page, perPage: $perPage) {
    media(type: ANIME, sort: $sort, status: $status, isAdult: false) {
      id idMal
      title { romaji english native }
      description(asHtml: false)
      format status episodes duration season seasonYear
      startDate { year month day }
      genres averageScore popularity
      coverImage { extraLarge large }
      bannerImage
      nextAiringEpisode { airingAt episode }
    }
  }
}
"""


def anilist_item(m: dict) -> dict:
    sd = m.get("startDate") or {}
    date = None
    if sd.get("year"):
        date = f"{sd['year']:04d}-{(sd.get('month') or 1):02d}-{(sd.get('day') or 1):02d}"
    nxt = m.get("nextAiringEpisode") or {}
    title = m["title"]
    cover = m.get("coverImage") or {}
    return {
        "id": f"anilist:{m['id']}",
        "type": "anime",
        "mal_id": m.get("idMal"),
        "title": title.get("english") or title.get("romaji") or title.get("native"),
        "original_title": title.get("romaji") or title.get("native"),
        "overview": strip_html(m.get("description")),
        "format": m.get("format"),
        "status": m.get("status"),
        "episodes": m.get("episodes"),
        "runtime": m.get("duration"),
        "season": m.get("season"),
        "release_date": date,
        "year": m.get("seasonYear") or year_of(date),
        "genres": m.get("genres") or [],
        "rating": round(m["averageScore"] / 10, 1) if m.get("averageScore") else None,
        "popularity": m.get("popularity"),
        "poster": cover.get("extraLarge") or cover.get("large"),
        "backdrop": m.get("bannerImage"),
        "next_episode": nxt.get("episode"),
        "next_episode_at": (
            dt.datetime.fromtimestamp(nxt["airingAt"], dt.timezone.utc).isoformat()
            if nxt.get("airingAt") else None),
        "seen": TODAY,
    }


def anilist_list(sort: str, status: str | None, pages: int) -> list[dict]:
    out = []
    for page in range(1, pages + 1):
        variables = {"page": page, "perPage": 50, "sort": [sort]}
        if status:
            variables["status"] = status
        body = json.dumps({"query": ANILIST_QUERY, "variables": variables}).encode()
        res = http_json(ANILIST_URL, data=body, headers={"Content-Type": "application/json"})
        media = (((res or {}).get("data") or {}).get("Page") or {}).get("media") or []
        out.extend(media)
        time.sleep(1.0)  # AniList: 90 req/min
        if len(media) < 50:
            break
    return out


def update_anime() -> int:
    cat = Catalog("anime")
    plan = {
        "trending": ("TRENDING_DESC", None, 2),
        "popular": ("POPULARITY_DESC", None, 3),
        "airing": ("POPULARITY_DESC", "RELEASING", 3),
        "upcoming": ("POPULARITY_DESC", "NOT_YET_RELEASED", 2),
    }
    ok = 0
    for name, (sort, status, pages) in plan.items():
        try:
            media = anilist_list(sort, status, pages)
        except Exception as e:
            log(f"[anime] {name} failed: {e}")
            continue
        if not media:
            log(f"[anime] {name}: empty response, keeping previous list")
            continue
        ids = [cat.upsert(anilist_item(m)) for m in media]
        write_json(ROOT / "lists" / f"anime_{name}.json", ids)
        log(f"[anime] {name}: {len(ids)}")
        ok += 1
    if ok:
        cat.save()
    return ok


# --------------------------------------------------------------------------- #
# Series: TVMaze
# --------------------------------------------------------------------------- #
TVMAZE = "https://api.tvmaze.com"


def tvmaze_item(s: dict) -> dict:
    img = s.get("image") or {}
    net = s.get("network") or s.get("webChannel") or {}
    return {
        "id": f"tvmaze:{s['id']}",
        "type": "series",
        "imdb_id": (s.get("externals") or {}).get("imdb"),
        "title": s.get("name"),
        "overview": strip_html(s.get("summary")),
        "status": s.get("status"),
        "format": s.get("type"),
        "language": s.get("language"),
        "network": net.get("name"),
        "runtime": s.get("runtime") or s.get("averageRuntime"),
        "release_date": s.get("premiered"),
        "year": year_of(s.get("premiered")),
        "genres": s.get("genres") or [],
        "rating": (s.get("rating") or {}).get("average"),
        "popularity": s.get("weight"),
        "poster": img.get("original") or img.get("medium"),
        "seen": TODAY,
    }


def update_series() -> int:
    cat = Catalog("series")
    schedule: list[dict] = []
    show_ids: dict[str, int] = {}
    today = dt.date.today()

    for d in range(SCHEDULE_DAYS):
        day = (today + dt.timedelta(days=d)).isoformat()
        for url in (f"{TVMAZE}/schedule/web?date={day}",
                    f"{TVMAZE}/schedule?country=US&date={day}",
                    f"{TVMAZE}/schedule?country=GB&date={day}"):
            try:
                eps = http_json(url) or []
            except Exception as e:
                log(f"[series] schedule {url} failed: {e}")
                continue
            for ep in eps:
                # /schedule/web embeds the show under _embedded.show; others use `show`
                show = ((ep.get("_embedded") or {}).get("show")) or ep.get("show")
                if not show or not show.get("id"):
                    continue
                sid = cat.upsert(tvmaze_item(show))
                show_ids[sid] = show.get("weight") or 0
                schedule.append({
                    "show_id": sid,
                    "date": ep.get("airdate") or day,
                    "time": ep.get("airtime") or None,
                    "season": ep.get("season"),
                    "episode": ep.get("number"),
                    "name": ep.get("name"),
                })
            time.sleep(0.6)  # TVMaze: 20 req / 10 s

    if not schedule:
        log("[series] no schedule data fetched, keeping previous files")
        return 0

    # dedupe schedule
    seen, uniq = set(), []
    for e in schedule:
        key = (e["show_id"], e["date"], e["season"], e["episode"])
        if key not in seen:
            seen.add(key)
            uniq.append(e)
    uniq.sort(key=lambda e: (e["date"], e["time"] or "", e["show_id"]))
    write_json(ROOT / "series_schedule.json", uniq)

    # Refresh shows TVMaze says changed in the last day
    try:
        ups = http_json(f"{TVMAZE}/updates/shows?since=day") or {}
        ids = sorted(ups, key=lambda k: ups[k], reverse=True)[:TVMAZE_UPDATE_CAP]
        for sid in ids:
            show = http_json(f"{TVMAZE}/shows/{sid}")
            if show:
                cat.upsert(tvmaze_item(show))
            time.sleep(0.6)
        log(f"[series] refreshed {len(ids)} updated shows")
    except Exception as e:
        log(f"[series] updates failed: {e}")

    airing_today = [e["show_id"] for e in uniq if e["date"] == TODAY]
    write_json(ROOT / "lists" / "series_airing_today.json", list(dict.fromkeys(airing_today)))
    popular = sorted(show_ids, key=lambda k: show_ids[k], reverse=True)[:100]
    write_json(ROOT / "lists" / "series_popular.json", popular)
    cat.save()
    log(f"[series] schedule episodes: {len(uniq)}, shows in catalog: {len(cat.items)}")
    return 1


# --------------------------------------------------------------------------- #
# Movies: TMDB (optional, needs TMDB_API_KEY)
# --------------------------------------------------------------------------- #
TMDB = "https://api.themoviedb.org/3"
TMDB_IMG = "https://image.tmdb.org/t/p"
TMDB_GENRES: dict[int, str] = {}


def tmdb_get(path: str, key: str, **params):
    q = urllib.parse.urlencode({"language": "en-US", **params})
    if len(key) > 40:  # v4 read-access token
        return http_json(f"{TMDB}{path}?{q}", headers={"Authorization": f"Bearer {key}"})
    return http_json(f"{TMDB}{path}?{q}&api_key={key}")


def tmdb_item(m: dict) -> dict:
    return {
        "id": f"tmdb:{m['id']}",
        "type": "movie",
        "title": m.get("title"),
        "original_title": m.get("original_title"),
        "original_language": m.get("original_language"),
        "overview": m.get("overview"),
        "release_date": m.get("release_date") or None,
        "year": year_of(m.get("release_date")),
        "genres": [TMDB_GENRES[g] for g in m.get("genre_ids", []) if g in TMDB_GENRES],
        "rating": round(m["vote_average"], 1) if m.get("vote_count") else None,
        "popularity": m.get("popularity"),
        "poster": f"{TMDB_IMG}/w500{m['poster_path']}" if m.get("poster_path") else None,
        "backdrop": f"{TMDB_IMG}/w780{m['backdrop_path']}" if m.get("backdrop_path") else None,
        "seen": TODAY,
    }


def update_movies() -> int:
    key = os.environ.get("TMDB_API_KEY", "").strip()
    if not key:
        log("[movies] TMDB_API_KEY not set, skipping movies (see README)")
        return 0
    cat = Catalog("movies")
    try:
        g = tmdb_get("/genre/movie/list", key) or {}
        TMDB_GENRES.update({x["id"]: x["name"] for x in g.get("genres", [])})
    except Exception as e:
        log(f"[movies] genre list failed: {e}")
    plan = {
        "trending": ("/trending/movie/week", {}),
        "popular": ("/movie/popular", {"region": "IN"}),
        "now_playing": ("/movie/now_playing", {"region": "IN"}),
        "upcoming": ("/movie/upcoming", {"region": "IN"}),
        "top_rated": ("/movie/top_rated", {}),
        # Indian regional cinema (Tamil, Telugu, Hindi, Malayalam)
        "tamil": ("/discover/movie", {"with_original_language": "ta", "sort_by": "popularity.desc"}),
        "telugu": ("/discover/movie", {"with_original_language": "te", "sort_by": "popularity.desc"}),
        "hindi": ("/discover/movie", {"with_original_language": "hi", "sort_by": "popularity.desc"}),
        "malayalam": ("/discover/movie", {"with_original_language": "ml", "sort_by": "popularity.desc"}),
    }
    ok = 0
    for name, (path, params) in plan.items():
        results = []
        try:
            for page in (1, 2, 3):
                res = tmdb_get(path, key, page=page, **params) or {}
                results.extend(res.get("results", []))
                time.sleep(0.3)
        except Exception as e:
            log(f"[movies] {name} failed: {e}")
            if not results:
                continue
        if not results:
            continue
        ids = [cat.upsert(tmdb_item(m)) for m in results]
        write_json(ROOT / "lists" / f"movies_{name}.json", ids)
        log(f"[movies] {name}: {len(ids)}")
        ok += 1
    if ok:
        cat.save()
    return ok


# --------------------------------------------------------------------------- #
# Index / meta
# --------------------------------------------------------------------------- #
def write_index(results: dict[str, int]) -> None:
    files = sorted(str(p.relative_to(ROOT)) for p in ROOT.rglob("*.json") if p.name != "index.json")
    counts = {}
    for kind in ("anime", "series", "movies"):
        counts[kind] = len(read_json(ROOT / "catalog" / f"{kind}.json", {}))
    write_json(ROOT / "index.json", {
        "updated_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "counts": counts,
        "sources_ok": results,
        "files": files,
    })


def main() -> int:
    ROOT.mkdir(parents=True, exist_ok=True)
    results = {"anime": 0, "series": 0, "movies": 0}
    for name, fn in (("anime", update_anime), ("series", update_series), ("movies", update_movies)):
        try:
            results[name] = fn()
        except Exception as e:  # one source must never break the others
            log(f"[{name}] crashed: {e}")
    write_index(results)
    log(f"done: {results}")
    # Fail the workflow only if every source that was attempted produced nothing.
    attempted = [k for k in results if k != "movies" or os.environ.get("TMDB_API_KEY")]
    if all(results[k] == 0 for k in attempted):
        log("ERROR: no source produced data")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
