"""抓取任務對真的 Postgres 的行為。RSS 與 Spotify 都用假的，不連網。

資料庫用 conftest 的 db fixture：每個測試一個乾淨的 schema，裡面有 Show A、Show B 兩個節目。
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import pytest
from _shared import FEED_A, FEED_B

from podcast_notes.config import Settings
from podcast_notes.db import connect
from podcast_notes.pipeline.fetch import claim_episodes, sync_sources
from podcast_notes.sources.podcast import Episode, FeedResult
from podcast_notes.sources.spotify_catalog import SpotifyEpisode

SINCE = datetime(2025, 1, 1, tzinfo=UTC)
NOW = datetime.now(UTC)


def _ep(guid: str, title: str, published: datetime, audio: str | None = "https://x/a.mp3"):
    return Episode(
        source_item_id=guid, title=title, published_at=published,
        audio_url=audio, transcript_url=None,
    )


class FakeFeeds:
    """假的 RSS：feed 網址 → FeedResult，或 → 例外（模擬 RSS 掛掉）。"""

    def __init__(self, feeds: dict[str, FeedResult | Exception]) -> None:
        self.feeds = feeds

    def __call__(self, url: str) -> FeedResult:
        result = self.feeds[url]
        if isinstance(result, Exception):
            raise result
        return result


class FakeCatalog:
    def __init__(self, episodes: list[SpotifyEpisode]) -> None:
        self.episodes = episodes
        self.calls = 0

    def list_show_episodes(self, show_id: str) -> list[SpotifyEpisode]:
        self.calls += 1
        return self.episodes


def _feed(*episodes: Episode, redirect: str | None = None) -> FeedResult:
    return FeedResult(episodes=list(episodes), permanent_redirect_to=redirect)


def _rows(settings: Settings, sql: str = "select * from episode_status order by source_item_id"):
    with connect(settings) as conn:
        return conn.execute(sql).fetchall()


# --- sync_sources ---------------------------------------------------------------------------


def test_inserts_new_episodes_and_is_idempotent(db: Settings) -> None:
    feeds = FakeFeeds({
        FEED_A: _feed(
            _ep("a1", "New one", datetime(2026, 9, 1, tzinfo=UTC)),
            _ep("a0", "Before backfill start", datetime(2024, 12, 31, tzinfo=UTC)),
            _ep("a2", "No audio", datetime(2026, 9, 2, tzinfo=UTC), audio=None),
        ),
        FEED_B: _feed(_ep("b1", "B one", datetime(2026, 9, 3, tzinfo=UTC))),
    })

    with connect(db) as conn:
        report = sync_sources(conn, catalog=None, since=SINCE, feed_fetcher=feeds)
    assert report.inserted == {"Show A": 1, "Show B": 1}
    assert report.errors == {}
    assert [r["source_item_id"] for r in _rows(db)] == ["a1", "b1"]
    assert _rows(db)[0]["status"] == "pending"

    # 再跑一次：已存在的集數不重複寫入
    with connect(db) as conn:
        report = sync_sources(conn, catalog=None, since=SINCE, feed_fetcher=feeds)
    assert report.inserted == {"Show A": 0, "Show B": 0}
    assert len(_rows(db)) == 2


def test_matches_spotify_and_skips_api_when_nothing_to_match(db: Settings) -> None:
    feeds = FakeFeeds({
        FEED_A: _feed(_ep("a1", "Episode One", datetime(2026, 9, 1, tzinfo=UTC))),
        FEED_B: _feed(),
    })
    catalog = FakeCatalog([SpotifyEpisode(id="S" * 22, name="Episode One", release_date=None)])

    with connect(db) as conn:
        report = sync_sources(conn, catalog=catalog, since=SINCE, feed_fetcher=feeds)
    assert report.matched == {"Show A": 1}  # Show B 沒有 Spotify id，不比對
    assert _rows(db)[0]["spotify_episode_id"] == "S" * 22

    # 都比對過了，第二輪不該再打 Spotify API
    with connect(db) as conn:
        sync_sources(conn, catalog=catalog, since=SINCE, feed_fetcher=feeds)
    assert catalog.calls == 1


def test_unmatched_episode_is_retried_next_run(db: Settings) -> None:
    """RSS 先出、Spotify 晚上架：第一輪比不到，第二輪 Spotify 有了就補上。"""
    feeds = FakeFeeds({
        FEED_A: _feed(_ep("a1", "Fresh", datetime(2026, 9, 1, tzinfo=UTC))),
        FEED_B: _feed(),
    })
    catalog = FakeCatalog([])
    with connect(db) as conn:
        sync_sources(conn, catalog=catalog, since=SINCE, feed_fetcher=feeds)
    assert _rows(db)[0]["spotify_episode_id"] is None

    catalog.episodes = [SpotifyEpisode(id="S" * 22, name="Fresh", release_date=None)]
    with connect(db) as conn:
        sync_sources(conn, catalog=catalog, since=SINCE, feed_fetcher=feeds)
    assert _rows(db)[0]["spotify_episode_id"] == "S" * 22


def test_spotify_id_already_used_by_another_episode_is_not_written(db: Settings) -> None:
    """同一個 Spotify 集數配給第二集 RSS：資料庫 unique 擋下，這集留空（退回音檔），
    不讓整輪失敗。"""
    feeds = FakeFeeds({
        FEED_A: _feed(_ep("a1", "Same Title", datetime(2026, 9, 1, tzinfo=UTC))),
        FEED_B: _feed(),
    })
    catalog = FakeCatalog([SpotifyEpisode(id="S" * 22, name="Same Title", release_date=None)])
    with connect(db) as conn:
        sync_sources(conn, catalog=catalog, since=SINCE, feed_fetcher=feeds)

    # 之後 RSS 冒出一集同標題、不同 guid 的（例如節目重發），比對又會對到同一個 Spotify 集數
    feeds.feeds[FEED_A] = _feed(_ep("a2", "Same Title", datetime(2026, 9, 2, tzinfo=UTC)))
    with connect(db) as conn:
        report = sync_sources(conn, catalog=catalog, since=SINCE, feed_fetcher=feeds)
    assert report.errors == {}
    assert report.matched == {"Show A": 0}
    rows = {r["source_item_id"]: r["spotify_episode_id"] for r in _rows(db)}
    assert rows == {"a1": "S" * 22, "a2": None}


def test_one_failing_source_does_not_affect_others(db: Settings) -> None:
    feeds = FakeFeeds({
        FEED_A: ConnectionError("RSS host down"),
        FEED_B: _feed(_ep("b1", "B one", datetime(2026, 9, 3, tzinfo=UTC))),
    })
    with connect(db) as conn:
        report = sync_sources(conn, catalog=None, since=SINCE, feed_fetcher=feeds)
    assert "RSS host down" in report.errors["Show A"]
    assert report.inserted == {"Show B": 1}
    assert [r["source_item_id"] for r in _rows(db)] == ["b1"]


def test_each_source_is_committed_on_its_own(db: Settings) -> None:
    """節目各自 commit：同步途中程式整個掛掉，已處理完的節目資料仍然存在。"""
    feeds = FakeFeeds({
        FEED_A: _feed(_ep("a1", "A one", datetime(2026, 9, 1, tzinfo=UTC))),
        FEED_B: _feed(),
    })

    class Crash(BaseException):
        """BaseException 不會被 sync_sources 的 except Exception 接住，模擬程式整個中斷。"""

    def crash_on_b(url: str) -> FeedResult:
        if url == FEED_B:
            raise Crash
        return feeds(url)

    with pytest.raises(Crash):
        with connect(db) as conn:
            sync_sources(conn, catalog=None, since=SINCE, feed_fetcher=crash_on_b)
    assert [r["source_item_id"] for r in _rows(db)] == ["a1"]


def test_permanent_redirect_updates_feed_url(db: Settings) -> None:
    new_url = "https://example.com/a-moved.xml"
    feeds = FakeFeeds({FEED_A: _feed(redirect=new_url), FEED_B: _feed()})
    with connect(db) as conn:
        sync_sources(conn, catalog=None, since=SINCE, feed_fetcher=feeds)
    urls = [r["feed_url"] for r in _rows(db, "select feed_url from tracked_sources order by id")]
    assert urls == [new_url, FEED_B]


def test_redirect_to_url_used_by_another_source_is_not_written(db: Settings) -> None:
    """轉址到別的節目已在用的網址：不硬寫，但這個節目的集數照樣寫入。"""
    feeds = FakeFeeds({
        FEED_A: _feed(_ep("a1", "A one", datetime(2026, 9, 1, tzinfo=UTC)), redirect=FEED_B),
        FEED_B: _feed(),
    })
    with connect(db) as conn:
        report = sync_sources(conn, catalog=None, since=SINCE, feed_fetcher=feeds)
    assert report.errors == {}
    urls = [r["feed_url"] for r in _rows(db, "select feed_url from tracked_sources order by id")]
    assert urls == [FEED_A, FEED_B]
    assert [r["source_item_id"] for r in _rows(db)] == ["a1"]


# --- claim_episodes -------------------------------------------------------------------------


def _insert(settings: Settings, guid: str, *, show: str = "Show A", published: datetime,
            status: str = "pending", spotify_id: str | None = None, attempts: int = 0,
            started: datetime | None = None) -> None:
    with connect(settings) as conn:
        conn.execute(
            """
            insert into episode_status
                (source_id, source_type, source_item_id, show_name, episode_title, published_at,
                 source_url, status, spotify_episode_id, attempt_count, processing_started_at)
            select id, 'podcast', %s, name, %s, %s, 'https://x/a.mp3', %s, %s, %s, %s
            from tracked_sources where name = %s
            """,
            (guid, guid, published, status, spotify_id, attempts, started, show),
        )


def _claim(settings: Settings, limit: int = 100, newest_first: bool = True) -> list[str]:
    with connect(settings) as conn:
        return [r["source_item_id"] for r in claim_episodes(conn, limit=limit,
                                                           newest_first=newest_first)]


def test_claim_marks_processing_and_counts_attempt(db: Settings) -> None:
    _insert(db, "a1", published=datetime(2026, 9, 1, tzinfo=UTC), spotify_id="S" * 22)
    assert _claim(db) == ["a1"]
    row = _rows(db)[0]
    assert row["status"] == "processing"
    assert row["attempt_count"] == 1
    assert row["processing_started_at"] is not None
    assert row["updated_at"] > row["created_at"]
    # 已經在 processing（還沒卡住），不會被重複撿
    assert _claim(db) == []


def test_claim_rules(db: Settings) -> None:
    old = NOW - timedelta(days=10)
    _insert(db, "pending", published=old, spotify_id="1" * 22)
    _insert(db, "too-many-attempts", published=old, spotify_id="2" * 22, attempts=3)
    _insert(db, "stuck", published=old, spotify_id="3" * 22, status="processing",
            started=NOW - timedelta(hours=3))
    _insert(db, "still-running", published=old, spotify_id="4" * 22, status="processing",
            started=NOW - timedelta(minutes=30))
    _insert(db, "done", published=old, spotify_id="5" * 22, status="completed")
    # 走 Spotify 的節目、還沒比對到：未滿 24 小時先等，超過就放行（之後退回音檔）
    _insert(db, "fresh-unmatched", published=NOW - timedelta(hours=2))
    _insert(db, "old-unmatched", published=NOW - timedelta(hours=25))
    _insert(db, "fresh-matched", published=NOW - timedelta(hours=2), spotify_id="6" * 22)
    # 不走 Spotify 的節目，不用等
    _insert(db, "fresh-no-spotify", show="Show B", published=NOW - timedelta(hours=2))

    assert sorted(_claim(db)) == sorted(
        ["pending", "stuck", "old-unmatched", "fresh-matched", "fresh-no-spotify"]
    )


def test_claim_limit_and_order(db: Settings) -> None:
    for day in (1, 2, 3):
        published = datetime(2026, 9, day, tzinfo=UTC)
        _insert(db, f"d{day}", published=published, spotify_id=str(day) * 22)
    assert _claim(db, limit=2, newest_first=False) == ["d1", "d2"]
    assert _claim(db, limit=2, newest_first=False) == ["d3"]


def test_backfill_since_default_matches_config() -> None:
    """SINCE 常數跟設定檔的回填起點一致，避免測試跟實際行為對不上。"""
    assert Settings.model_fields["backfill_since"].default == date(2025, 1, 1)
