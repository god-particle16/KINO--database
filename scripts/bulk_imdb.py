"""Bulk global catalog: every movie and series from the IMDb public datasets.

Source: https://datasets.imdbws.com  (free, no key, refreshed daily by IMDb;
        personal / non-commercial use only, see https://developer.imdb.com/non-commercial-datasets/)

IMDb gives titles, year, runtime, genres, rating and vote count, but no posters or
descriptions. Those are filled in gradually, a few thousand titles per day, most-voted
first:
  - series : TVMaze lookup by IMDb id (no key)
  - movies : TMDB /find by IMDb id (needs TMDB_API_KEY), also used as series fallback

Layout written under data/bulk/<movies|series>/
  s00.json .. s63.json   items, sharded by (imdb number % 64), so a client can find any title
  search/<char>.json     compact search index, one file per first letter of each word
  top_rated.json, most_voted.json, recent.json   lists of imdb ids (tt...)
  ../meta.json           counts, thresholds, enrichment progress

Tuning (environment variables / repo variables):
  MIN_VOTES_MOVIES (default 1000)   MIN_VOTES_SERIES (default 300)
  ENRICH_MOVIES (6000/day)          ENRICH_SERIES (3000/day)
  ENRICH_SECONDS (2400 per type)    SKIP_BULK=1 to disable
"""
from __future__ import annotations

import datetime as dt
import gzip
import json
import os
import re
import shutil
import tempfile
import time
import unicodedata
import urllib.request
from pathlib import Path

BASE = "https://datasets.imdbws.com"
SHARDS = 64
STOP = {"the", "a", "an", "of", "and"}
KIND_OF = {"movie": "movies", "tvSeries": "series", "tvMiniSeries": "series"}
RETRY_NOT_FOUND_DAYS = 60
LIST_SIZE = 500


def env_int(name: str, default: int) -> int:
    v = os.environ.get(name, "").strip()
    return int(v) if v.isdigit() else default


def log(msg: str) -> None:
    print(msg, flush=True)


# ---------------------------------------------------------------- text helpers
def norm(s: str) -> str:
    s = unicodedata.normalize("NFKD", s.lower())
    return "".join(c for c in s if not unicodedata.combining(c))


def words(s: str) -> list[str]:
    return re.findall(r"[^\W_]+", norm(s))


def shard_key(word: str) -> str:
    c = word[0]
    return c if ("a" <= c <= "z" or "0" <= c <= "9") else "_"


def index_keys(title: str, original: str | None) -> set[str]:
    ws = words(title) + (words(original) if original else [])
    keep = [w for w in ws if w not in STOP] or ws
    return {shard_key(w) for w in keep}


# -------------------------------------------------------------------- download
def download(url: str, dest: Path, retries: int = 3) -> None:
    last = None
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "KINO-database-updater/1.0"})
            with urllib.request.urlopen(req, timeout=120) as r, open(dest, "wb") as f:
                shutil.copyfileobj(r, f, 1 << 20)
            return
        except Exception as e:
            last = e
            time.sleep(5 * (attempt + 1))
    raise RuntimeError(f"download {url} failed: {last}")


# ------------------------------------------------------------------- IMDb data
def load_ratings(path: Path, min_votes: int) -> dict[str, tuple[float, int]]:
    out = {}
    with gzip.open(path, "rt", encoding="utf-8") as f:
        next(f)
        for line in f:
            tc, r, v = line.rstrip("\n").split("\t")
            v = int(v)
            if v >= min_votes:
                out[tc] = (float(r), v)
    return out


def build_items(path: Path, ratings: dict, thresholds: dict) -> dict[str, dict[str, dict]]:
    fresh: dict[str, dict[str, dict]] = {"movies": {}, "series": {}}
    with gzip.open(path, "rt", encoding="utf-8") as f:
        header = next(f).rstrip("\n").split("\t")
        ix = {n: i for i, n in enumerate(header)}
        for line in f:
            p = line.rstrip("\n").split("\t")
            kind = KIND_OF.get(p[ix["titleType"]])
            if not kind or p[ix["isAdult"]] == "1":
                continue
            tc = p[ix["tconst"]]
            rv = ratings.get(tc)
            if not rv or rv[1] < thresholds[kind]:
                continue

            def g(k):
                v = p[ix[k]]
                return None if v in ("\\N", "") else v

            item = {
                "id": f"imdb:{tc}",
                "imdb_id": tc,
                "type": "movie" if kind == "movies" else "series",
                "title": g("primaryTitle"),
                "year": int(g("startYear")) if g("startYear") else None,
                "runtime": int(g("runtimeMinutes")) if g("runtimeMinutes") else None,
                "genres": g("genres").split(",") if g("genres") else [],
                "rating": rv[0],
                "votes": rv[1],
            }
            if g("originalTitle") and g("originalTitle") != item["title"]:
                item["original_title"] = g("originalTitle")
            if kind == "series" and g("endYear"):
                item["end_year"] = int(g("endYear"))
            fresh[kind][tc] = {k: v for k, v in item.items() if v not in (None, [])}
    return fresh


# --------------------------------------------------------------------- storage
def shard_of(tc: str) -> int:
    return int(re.sub(r"\D", "", tc) or 0) % SHARDS


def load_existing(core, kind: str) -> dict[str, dict]:
    items: dict[str, dict] = {}
    d = core.ROOT / "bulk" / kind
    for p in sorted(d.glob("s[0-9][0-9].json")):
        items.update(core.read_json(p, {}))
    return items


def write_kind(core, kind: str, items: dict[str, dict]) -> None:
    d = core.ROOT / "bulk" / kind
    d.mkdir(parents=True, exist_ok=True)
    for old in d.glob("s[0-9][0-9].json"):
        old.unlink()
    shards: dict[int, dict] = {}
    for tc, it in items.items():
        shards.setdefault(shard_of(tc), {})[tc] = it
    for n, data in shards.items():
        core.write_json(d / f"s{n:02d}.json", data)

    # search index: one file per first letter of every (non-stop) word of the title
    sd = d / "search"
    if sd.exists():
        shutil.rmtree(sd)
    buckets: dict[str, list] = {}
    for tc, it in items.items():
        entry = [tc, it["title"], it.get("year"), it["votes"]]
        if it.get("original_title"):
            entry.append(it["original_title"])
        for key in index_keys(it["title"], it.get("original_title")):
            buckets.setdefault(key, []).append(entry)
    for key, entries in buckets.items():
        entries.sort(key=lambda e: -e[3])
        core.write_json(sd / f"{key}.json", entries)

    # curated lists (ids only)
    vals = list(items.values())
    avg = sum(i["rating"] for i in vals) / max(len(vals), 1)
    m = 25000 if kind == "movies" else 5000

    def weighted(i):
        return i["votes"] / (i["votes"] + m) * i["rating"] + m / (i["votes"] + m) * avg

    this_year = dt.date.today().year
    lists = {
        "top_rated": sorted(vals, key=weighted, reverse=True),
        "most_voted": sorted(vals, key=lambda i: -i["votes"]),
        "recent": sorted((i for i in vals if (i.get("year") or 0) >= this_year - 1),
                         key=lambda i: -i["votes"]),
    }
    for name, arr in lists.items():
        core.write_json(d / f"{name}.json", [i["imdb_id"] for i in arr[:LIST_SIZE]])


# ------------------------------------------------------------------ enrichment
def tmdb_enrich(core, key: str, tc: str, kind: str) -> dict | None:
    res = core.tmdb_get(f"/find/{tc}", key, external_source="imdb_id") or {}
    hits = res.get("movie_results" if kind == "movies" else "tv_results") or []
    if not hits:
        return None
    h = hits[0]
    out = {"tmdb_id": h.get("id"), "overview": h.get("overview")}
    if h.get("poster_path"):
        out["poster"] = f"{core.TMDB_IMG}/w500{h['poster_path']}"
    if h.get("backdrop_path"):
        out["backdrop"] = f"{core.TMDB_IMG}/w780{h['backdrop_path']}"
    return out


def tvmaze_enrich(core, tc: str) -> dict | None:
    show = core.http_json(f"{core.TVMAZE}/lookup/shows?imdb={tc}")
    if not show:
        return None
    img = show.get("image") or {}
    net = show.get("network") or show.get("webChannel") or {}
    out = {
        "tvmaze_id": show.get("id"),
        "overview": core.strip_html(show.get("summary")),
        "poster": img.get("original") or img.get("medium"),
        "network": net.get("name"),
        "status": show.get("status"),
    }
    return out


def enrich(core, kind: str, items: dict[str, dict]) -> int:
    key = os.environ.get("TMDB_API_KEY", "").strip()
    if kind == "movies" and not key:
        log("[bulk] movies: no TMDB_API_KEY, posters/overviews skipped")
        return 0
    cap = env_int("ENRICH_MOVIES" if kind == "movies" else "ENRICH_SERIES",
                  6000 if kind == "movies" else 3000)
    deadline = time.time() + env_int("ENRICH_SECONDS", 2400)
    today = dt.date.today()

    def due(it):
        if it.get("poster"):
            return False
        nf = it.get("nf")
        return not nf or (today - dt.date.fromisoformat(nf)).days >= RETRY_NOT_FOUND_DAYS

    todo = sorted((i for i in items.values() if due(i)), key=lambda i: -i["votes"])[:cap]
    done = fails = 0
    for it in todo:
        if time.time() > deadline:
            break
        tc = it["imdb_id"]
        try:
            if kind == "series":
                extra = tvmaze_enrich(core, tc)
                time.sleep(0.55)
                if (not extra or not extra.get("poster")) and key:
                    extra = {**(extra or {}), **(tmdb_enrich(core, key, tc, kind) or {})}
                    time.sleep(0.1)
            else:
                extra = tmdb_enrich(core, key, tc, kind)
                time.sleep(0.1)
            fails = 0
        except Exception as e:
            fails += 1
            log(f"[bulk] enrich {tc} failed: {e}")
            if fails >= 5:
                log("[bulk] too many consecutive failures, stopping enrichment")
                break
            continue
        extra = {k: v for k, v in (extra or {}).items() if v not in (None, "")}
        it.update(extra)
        if not it.get("poster"):
            it["nf"] = today.isoformat()
        else:
            it.pop("nf", None)
        done += 1
    log(f"[bulk] {kind}: enriched {done} titles")
    return done


# ------------------------------------------------------------------------ main
def update_bulk(core) -> int:
    if os.environ.get("SKIP_BULK") == "1":
        log("[bulk] SKIP_BULK=1, skipping")
        return 0
    tmp = Path(tempfile.mkdtemp())
    try:
        rp, bp = tmp / "ratings.tsv.gz", tmp / "basics.tsv.gz"
        download(f"{BASE}/title.ratings.tsv.gz", rp)
        download(f"{BASE}/title.basics.tsv.gz", bp)
        thresholds = {
            "movies": env_int("MIN_VOTES_MOVIES", 1000),
            "series": env_int("MIN_VOTES_SERIES", 300),
        }
        ratings = load_ratings(rp, min(thresholds.values()))
        fresh = build_items(bp, ratings, thresholds)
    except Exception as e:
        log(f"[bulk] failed, keeping previous data: {e}")
        return 0
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    meta = {"updated_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
            "source": "IMDb non-commercial datasets", "shards": SHARDS,
            "min_votes": thresholds, "counts": {}, "with_poster": {}}
    ok = 0
    for kind, new in fresh.items():
        if not new:
            log(f"[bulk] {kind}: nothing parsed, keeping previous data")
            continue
        old = load_existing(core, kind)
        merged = {tc: {**old.get(tc, {}), **base} for tc, base in new.items()}
        enrich(core, kind, merged)
        write_kind(core, kind, merged)
        meta["counts"][kind] = len(merged)
        meta["with_poster"][kind] = sum(1 for i in merged.values() if i.get("poster"))
        log(f"[bulk] {kind}: {len(merged)} titles ({meta['with_poster'][kind]} with posters)")
        ok += 1
    if ok:
        core.write_json(core.ROOT / "bulk" / "meta.json", meta)
    return ok
