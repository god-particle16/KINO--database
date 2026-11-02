"""Offline test: mocks every HTTP call, runs the whole pipeline, checks the output."""
import datetime as dt
import json
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
tmp = tempfile.mkdtemp()
os.environ["KINO_DATA_DIR"] = tmp
os.environ["TMDB_API_KEY"] = "fakekey"

import update_db as u  # noqa: E402

import gzip  # noqa: E402
import bulk_imdb as b  # noqa: E402

u.time.sleep = lambda *_: None

BASICS = [
    "tconst\ttitleType\tprimaryTitle\toriginalTitle\tisAdult\tstartYear\tendYear\truntimeMinutes\tgenres",
    "tt0000002\tmovie\tThe Big Movie\tLa Grande\t0\t2020\t\\N\t120\tDrama,Action",
    "tt0000066\tmovie\tTiny Movie\tTiny Movie\t0\t2010\t\\N\t90\tComedy",       # shard 2 as well
    "tt0000003\ttvSeries\tSome Show\tSome Show\t0\t2015\t2019\t45\tDrama",
    "tt0000004\ttvMiniSeries\tMini Thing\tMini Thing\t0\t2024\t\\N\t\\N\t\\N",
    "tt0000005\tmovie\tAdult One\tAdult One\t1\t2000\t\\N\t90\tX",
    "tt0000006\tshort\tA Short\tA Short\t0\t2000\t\\N\t5\tX",
    "tt0000007\tmovie\tUnpopular\tUnpopular\t0\t2000\t\\N\t90\tX",
    "tt0000008\ttvEpisode\tEpisode\tEpisode\t0\t2000\t\\N\t30\tX",
]
RATINGS = ["tconst\taverageRating\tnumVotes",
           "tt0000002\t8.1\t50000", "tt0000066\t6.0\t2000", "tt0000003\t7.5\t900",
           "tt0000004\t7.0\t400", "tt0000005\t5.0\t99999", "tt0000006\t5.0\t99999",
           "tt0000007\t5.0\t10", "tt0000008\t5.0\t99999"]


def fake_download(url, dest, retries=3):
    rows = RATINGS if "ratings" in url else BASICS
    with gzip.open(dest, "wt", encoding="utf-8") as f:
        f.write("\n".join(rows) + "\n")


b.download = fake_download
today = dt.date.today().isoformat()


def fake(url, *, data=None, headers=None, **_):
    if "tvmaze.com/lookup/shows" in url:
        return {"id": 55, "summary": "<p>Lookup</p>", "image": {"original": "http://i/lookup.jpg"},
                "network": {"name": "BBC"}, "status": "Ended"}
    if "/find/" in url:
        if "tt0000066" in url:
            return {"movie_results": []}          # not found on TMDB
        return {"movie_results": [{"id": 77, "overview": "Big", "poster_path": "/big.jpg",
                                   "backdrop_path": "/bd.jpg"}],
                "tv_results": []}
    if "anilist" in url:
        media = [{
            "id": 100 + i, "idMal": i, "title": {"romaji": f"Anime {i}", "english": None, "native": "x"},
            "description": "Desc<br>line", "format": "TV", "status": "RELEASING", "episodes": 12,
            "duration": 24, "season": "FALL", "seasonYear": 2026,
            "startDate": {"year": 2026, "month": 10, "day": 3}, "genres": ["Action"],
            "averageScore": 83, "popularity": 5, "coverImage": {"extraLarge": "http://img/a.jpg"},
            "bannerImage": None, "nextAiringEpisode": {"airingAt": 1793700000, "episode": 5},
        } for i in range(50 if json.loads(data)["variables"]["page"] == 1 else 3)]
        return {"data": {"Page": {"media": media}}}
    if "tvmaze.com/schedule" in url:
        show = {"id": 7, "name": "Show", "summary": "<p>Hi</p>", "genres": ["Drama"], "weight": 90,
                "rating": {"average": 8.1}, "premiered": "2020-01-01", "image": {"original": "http://i/s.jpg"},
                "network": {"name": "HBO"}, "externals": {"imdb": "tt1"}}
        ep = {"airdate": today, "airtime": "21:00", "season": 1, "number": 2, "name": "E"}
        if "/web" in url:
            return [{**ep, "_embedded": {"show": show}}]
        return [{**ep, "show": show}]
    if "updates/shows" in url:
        return {"7": 1}
    if "tvmaze.com/shows/" in url:
        return {"id": 7, "name": "Show", "genres": []}
    if "genre/movie/list" in url:
        return {"genres": [{"id": 1, "name": "Drama"}]}
    if "themoviedb" in url:
        return {"results": [{"id": 9, "title": "Movie", "original_title": "Movie", "genre_ids": [1],
                             "release_date": "2026-10-01", "vote_average": 7.5, "vote_count": 10,
                             "poster_path": "/p.jpg", "popularity": 3}]}
    raise AssertionError(url)


u.http_json = fake
assert u.main() == 0

d = Path(tmp)
idx = json.loads((d / "index.json").read_text())
assert idx["sources_ok"] == {"anime": 4, "series": 1, "movies": 9, "bulk": 2}, idx
assert idx["counts"]["anime"] == 50 and idx["counts"]["series"] == 1 and idx["counts"]["movies"] == 1, idx
a = json.loads((d / "catalog/anime.json").read_text())["anilist:100"]
assert a["title"] == "Anime 0" and a["rating"] == 8.3 and a["next_episode"] == 5 and a["overview"] == "Desc\nline"
s = json.loads((d / "catalog/series.json").read_text())["tvmaze:7"]
assert s["overview"] == "Hi" and s["network"] == "HBO" and s["imdb_id"] == "tt1"
sched = json.loads((d / "series_schedule.json").read_text())
assert len(sched) == SCHED if (SCHED := len(sched)) else False
assert len({(e["show_id"], e["date"]) for e in sched}) == len(sched)  # deduped
m = json.loads((d / "catalog/movies.json").read_text())["tmdb:9"]
assert m["genres"] == ["Drama"] and m["poster"].endswith("/w500/p.jpg")
assert json.loads((d / "search/anime.json").read_text())[0]["id"].startswith("anilist:")
assert (d / "lists/anime_trending.json").exists() and (d / "lists/movies_tamil.json").exists()

# ---- bulk IMDb catalog
bulk = d / "bulk"
meta = json.loads((bulk / "meta.json").read_text())
assert meta["counts"] == {"movies": 2, "series": 2}, meta        # adult/short/episode/unpopular excluded
mv = json.loads((bulk / "movies/s02.json").read_text())           # 2 % 64 and 66 % 64 -> same shard
assert set(mv) == {"tt0000002", "tt0000066"}, mv
big = mv["tt0000002"]
assert big["poster"].endswith("/w500/big.jpg") and big["tmdb_id"] == 77 and big["genres"] == ["Drama", "Action"]
assert big["original_title"] == "La Grande" and big["rating"] == 8.1 and big["votes"] == 50000
assert mv["tt0000066"]["nf"] == today and "poster" not in mv["tt0000066"]   # lookup miss remembered
ser = json.loads((bulk / "series/s03.json").read_text())["tt0000003"]
assert ser["poster"] == "http://i/lookup.jpg" and ser["network"] == "BBC" and ser["end_year"] == 2019
assert "tt0000004" in json.loads((bulk / "series/s04.json").read_text())
# search index: "The" is a stop word, so the title is indexed under b(ig), m(ovie) and l/g (original), never t
assert json.loads((bulk / "movies/search/b.json").read_text())[0][:2] == ["tt0000002", "The Big Movie"]
assert not (bulk / "movies/search/t.json").exists() or \
    all(e[0] != "tt0000002" for e in json.loads((bulk / "movies/search/t.json").read_text()))
assert json.loads((bulk / "movies/most_voted.json").read_text()) == ["tt0000002", "tt0000066"]
assert json.loads((bulk / "movies/top_rated.json").read_text())[0] == "tt0000002"
assert "bulk/movies/s02.json" not in idx["files"] and idx["bulk"]["counts"]["series"] == 2
assert idx["sources_ok"]["bulk"] == 2

# second run must merge, not duplicate
assert u.main() == 0
assert json.loads((d / "index.json").read_text())["counts"]["anime"] == 50

# failure of every source -> non-zero exit
u.http_json = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("down"))
b.download = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("down"))
os.environ.pop("TMDB_API_KEY")
assert u.main() == 1
print("ALL TESTS PASSED")
