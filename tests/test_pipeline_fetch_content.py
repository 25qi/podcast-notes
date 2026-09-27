"""抓內容任務對真的 Postgres 的行為。Spotify 逐字稿用假的，不開瀏覽器。

資料庫用 conftest 的 db fixture：Show A 走 Spotify，Show B 沒有 Spotify id。
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import psycopg
import pytest

from podcast_notes.config import Settings
from podcast_notes.db import connect
from podcast_notes.pipeline.fetch_content import fetch_contents
from podcast_notes.sources.spotify_transcript import SpotifyLoginRequired, Transcript

NOW = datetime.now(UTC)
OLD = NOW - timedelta(days=10)
TRANSCRIPT = Transcript(text="Speaker 1\nHello.", language="en-us", sentence_count=1,
                        word_count=1)


class FakeTranscripts:
    """假的逐字稿來源：Spotify episode id → Transcript / None（沒逐字稿）/ 例外。"""

    def __init__(self, results: dict[str, Transcript | None | BaseException]) -> None:
        self.results = results
        self.calls: list[str] = []

    def fetch(self, episode_id: str) -> Transcript | None:
        self.calls.append(episode_id)
        result = self.results[episode_id]
        if isinstance(result, BaseException):
            raise result
        return result


def _insert(settings: Settings, guid: str, *, show: str = "Show A", published: datetime = OLD,
            status: str = "processing", spotify_id: str | None = None, attempts: int = 1) -> None:
    with connect(settings) as conn:
        conn.execute(
            """
            insert into episode_status
                (source_id, source_type, source_item_id, show_name, episode_title, published_at,
                 source_url, status, spotify_episode_id, attempt_count, processing_started_at)
            select id, 'podcast', %s, name, %s, %s, 'https://x/a.mp3', %s, %s, %s, now()
            from tracked_sources where name = %s
            """,
            (guid, guid, published, status, spotify_id, attempts, show),
        )


def _status(settings: Settings) -> dict[str, tuple]:
    with connect(settings) as conn:
        rows = conn.execute(
            "select source_item_id, status, attempt_count, last_error from episode_status"
        ).fetchall()
    return {r["source_item_id"]: (r["status"], r["attempt_count"], r["last_error"]) for r in rows}


def _contents(settings: Settings) -> dict[str, tuple]:
    with connect(settings) as conn:
        rows = conn.execute(
            "select source_item_id, content_source, transcript from episode_contents"
        ).fetchall()
    return {r["source_item_id"]: (r["content_source"], r["transcript"]) for r in rows}


def _run(settings: Settings, fake: FakeTranscripts):
    with connect(settings) as conn:
        return fetch_contents(conn, fake)


def test_transcript_audio_and_pending_are_handled(db: Settings) -> None:
    _insert(db, "has-transcript", spotify_id="1" * 22)
    _insert(db, "no-transcript-old", spotify_id="2" * 22)
    _insert(db, "unmatched", spotify_id=None)  # fetch 階段 24 小時都沒比對到
    _insert(db, "no-spotify-show", show="Show B")
    _insert(db, "not-claimed", spotify_id="3" * 22, status="pending")  # 沒被撿的不處理
    fake = FakeTranscripts({"1" * 22: TRANSCRIPT, "2" * 22: None})

    report = _run(db, fake)

    assert (report.transcripts, report.audio, report.released) == (1, 3, 0)
    assert _contents(db) == {
        "has-transcript": ("spotify_transcript", "Speaker 1\nHello."),
        "no-transcript-old": ("audio", None),
        "unmatched": ("audio", None),
        "no-spotify-show": ("audio", None),
    }
    # 有內容了，但狀態仍是 processing：之後由摘要任務接手
    assert _status(db)["has-transcript"][0] == "processing"
    # 沒比對到 / 沒 Spotify 的集數，根本不該開瀏覽器
    assert sorted(fake.calls) == ["1" * 22, "2" * 22]


def test_already_fetched_episode_is_not_fetched_again(db: Settings) -> None:
    """逐字稿先存起來的目的：摘要失敗重跑時，不用再抓一次。"""
    _insert(db, "e1", spotify_id="1" * 22)
    fake = FakeTranscripts({"1" * 22: TRANSCRIPT})
    _run(db, fake)
    _run(db, fake)
    assert fake.calls == ["1" * 22]


def test_fresh_episode_without_transcript_is_released(db: Settings) -> None:
    """有比對到但逐字稿還沒產生、發布未滿 24 小時：放回 pending，嘗試次數扣回來。"""
    _insert(db, "fresh", spotify_id="1" * 22, published=NOW - timedelta(hours=3), attempts=1)
    report = _run(db, FakeTranscripts({"1" * 22: None}))
    assert report.released == 1
    assert _contents(db) == {}
    assert _status(db)["fresh"] == ("pending", 0, None)


def test_error_marks_episode_failed_and_continues(db: Settings) -> None:
    _insert(db, "broken", spotify_id="1" * 22, published=OLD + timedelta(days=1))
    _insert(db, "fine", spotify_id="2" * 22)
    fake = FakeTranscripts({"1" * 22: TimeoutError("page did not load"), "2" * 22: TRANSCRIPT})

    report = _run(db, fake)

    status, attempts, error = _status(db)["broken"]
    assert status == "failed"
    assert "page did not load" in error
    assert list(report.failed) == ["broken"]
    assert _contents(db)["fine"][0] == "spotify_transcript"


def test_login_required_releases_rest_and_raises(db: Settings) -> None:
    """登入失效：已處理的保留，當下這集和後面的全部放回 pending（不算嘗試次數），然後報錯。"""
    _insert(db, "newest", spotify_id="1" * 22, published=OLD + timedelta(days=2))
    _insert(db, "middle", spotify_id="2" * 22, published=OLD + timedelta(days=1))
    _insert(db, "oldest", spotify_id="3" * 22, published=OLD)
    fake = FakeTranscripts({
        "1" * 22: TRANSCRIPT,
        "2" * 22: SpotifyLoginRequired("expired"),
        "3" * 22: TRANSCRIPT,
    })

    with pytest.raises(SpotifyLoginRequired):
        _run(db, fake)

    assert _contents(db) == {"newest": ("spotify_transcript", "Speaker 1\nHello.")}
    status = _status(db)
    assert status["middle"] == ("pending", 0, None)
    assert status["oldest"] == ("pending", 0, None)
    assert "3" * 22 not in fake.calls  # 登入失效後不再嘗試


def test_contents_table_rejects_inconsistent_rows(db: Settings) -> None:
    """資料庫層的保護：標成逐字稿卻沒有全文、或標成音檔卻有全文，都寫不進去。"""
    _insert(db, "e1")
    for source, text in (("spotify_transcript", None), ("audio", "some text")):
        with pytest.raises(psycopg.errors.CheckViolation):
            with connect(db) as conn:
                conn.execute(
                    """
                    insert into episode_contents (source_type, source_item_id, content_source,
                                                  transcript)
                    values ('podcast', 'e1', %s, %s)
                    """,
                    (source, text),
                )
