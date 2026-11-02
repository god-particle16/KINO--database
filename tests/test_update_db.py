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

u.time.sleep = lambda *_: None
today = dt.date.today().isoformat()


def fake(url, *, data=None, headers=None, **_):
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
assert idx["sources_ok"] == {"anime": 4, "series": 1, "movies": 9}, idx
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

# second run must merge, not duplicate
assert u.main() == 0
assert json.loads((d / "index.json").read_text())["counts"]["anime"] == 50

# failure of every source -> non-zero exit
u.http_json = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("down"))
os.environ.pop("TMDB_API_KEY")
assert u.main() == 1
print("ALL TESTS PASSED")
