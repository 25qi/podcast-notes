"""測試 spotify_catalog 的比對邏輯與 API 分頁處理。不連網、不需要 Spotify 帳號。"""

from datetime import UTC, date, datetime

import httpx

from podcast_notes.sources.podcast import Episode
from podcast_notes.sources.spotify_catalog import SpotifyCatalog, SpotifyEpisode, match_episodes


def _rss(guid: str, title: str, published: datetime) -> Episode:
    return Episode(
        source_item_id=guid, title=title, published_at=published,
        audio_url=None, transcript_url=None,
    )


def _sp(sid: str, name: str, release: date | None) -> SpotifyEpisode:
    return SpotifyEpisode(id=sid, name=name, release_date=release)


def test_exact_title_matches_even_if_dates_differ() -> None:
    """標題完全一樣就配對，不看日期（兩邊日期常差一天）。"""
    rss = [_rss("g1", "Josh Elman on Apple, AI Agents", datetime(2026, 9, 24, 2, tzinfo=UTC))]
    sp = [_sp("A" * 22, "Josh Elman on Apple, AI Agents", date(2026, 9, 23))]
    assert match_episodes(rss, sp) == {"g1": "A" * 22}


def test_title_normalization_ignores_case_quotes_and_punctuation() -> None:
    """彎引號 vs 直引號、大小寫、標點不同，都算同一個標題。"""
    rss = [_rss("g1", "Lenny’s Podcast: What’s Next?", datetime(2026, 9, 20, tzinfo=UTC))]
    sp = [_sp("A" * 22, "lenny's podcast - what's next", None)]
    assert match_episodes(rss, sp) == {"g1": "A" * 22}


def test_similar_title_within_one_day_matches() -> None:
    """標題有小差異（例如多了節目名後綴）但日期相近，第二輪會配對。"""
    rss = [_rss("g1", "Gabe Stengel - Building Investing Superintelligence",
                datetime(2026, 9, 22, 10, tzinfo=UTC))]
    sp = [_sp("A" * 22, "Gabe Stengel - Building Investing Superintelligence [ILTB]",
              date(2026, 9, 22))]
    assert match_episodes(rss, sp) == {"g1": "A" * 22}


def test_similar_title_but_date_too_far_is_not_matched() -> None:
    """標題相近但日期差太多（可能是重播或續集），寧可不配。"""
    rss = [_rss("g1", "State of AI Part 1", datetime(2026, 9, 22, tzinfo=UTC))]
    sp = [_sp("A" * 22, "State of AI Part 2", date(2026, 8, 1))]
    assert match_episodes(rss, sp) == {}


def test_different_titles_on_same_day_are_not_matched() -> None:
    """同一天、標題完全不同 → 不是同一集。"""
    rss = [_rss("g1", "Interview with Alice", datetime(2026, 9, 22, tzinfo=UTC))]
    sp = [_sp("A" * 22, "Market Update: Rates and IPOs", date(2026, 9, 22))]
    assert match_episodes(rss, sp) == {}


def test_ambiguous_candidates_are_not_guessed() -> None:
    """同一天有兩集標題都很像，無法確定是哪一集 → 放棄，不猜。"""
    rss = [_rss("g1", "Weekly Roundup Episode", datetime(2026, 9, 22, tzinfo=UTC))]
    sp = [
        _sp("A" * 22, "Weekly Roundup Episode A", date(2026, 9, 22)),
        _sp("B" * 22, "Weekly Roundup Episode B", date(2026, 9, 22)),
    ]
    assert match_episodes(rss, sp) == {}


def test_each_spotify_episode_is_used_at_most_once() -> None:
    """兩集 RSS 不能對到同一個 Spotify 集數（資料庫也有 unique 約束擋）。"""
    rss = [
        _rss("g1", "Big Interview", datetime(2026, 9, 22, tzinfo=UTC)),
        _rss("g2", "Big Interview (Rebroadcast)", datetime(2026, 9, 22, tzinfo=UTC)),
    ]
    sp = [_sp("A" * 22, "Big Interview", date(2026, 9, 22))]
    assert match_episodes(rss, sp) == {"g1": "A" * 22}


def test_list_show_episodes_follows_pagination_and_skips_null_items() -> None:
    """跟著 next 翻頁、跳過 null 集數、只有精確到日的日期才保留、token 只拿一次。"""
    token_calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal token_calls
        if request.url.host == "accounts.spotify.com":
            token_calls += 1
            return httpx.Response(200, json={"access_token": "t", "expires_in": 3600})
        assert request.headers["Authorization"] == "Bearer t"
        if request.url.params.get("offset") == "50":
            return httpx.Response(200, json={"items": [
                {"id": "B" * 22, "name": "Old", "release_date": "2019",
                 "release_date_precision": "year"},
            ], "next": None})
        assert request.url.params["market"] == "US"
        return httpx.Response(200, json={"items": [
            {"id": "A" * 22, "name": "New", "release_date": "2026-09-22",
             "release_date_precision": "day"},
            None,
        ], "next": "https://api.spotify.com/v1/shows/x/episodes?offset=50&limit=50&market=US"})

    http = httpx.Client(transport=httpx.MockTransport(handler))
    with SpotifyCatalog("id", "secret", client=http) as catalog:
        episodes = catalog.list_show_episodes("x")

    assert episodes == [
        SpotifyEpisode(id="A" * 22, name="New", release_date=date(2026, 9, 22)),
        SpotifyEpisode(id="B" * 22, name="Old", release_date=None),
    ]
    assert token_calls == 1
