"""摘要＋寫入任務對真的 Postgres 的行為。Gemini 用假的，不連網、不耗額度。

資料庫用 conftest 的 db fixture：Show A、Show B 兩個節目。
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from podcast_notes.config import Settings
from podcast_notes.db import connect
from podcast_notes.pipeline.summarize import summarize_episodes
from podcast_notes.summarize.gemini import Note, NoteContent, QuotaExhausted

OLD = datetime.now(UTC) - timedelta(days=10)

CONTENT = NoteContent.model_validate({
    "one_liner": "一句話",
    "guest_background": None,
    "summary": "摘要",
    "key_points": ["重點一", "重點二"],
    "people": [{"name": "Rene Haas", "context": "Arm CEO"}],
    "companies": [{"name": "Arm", "context": "晶片架構公司"}],
    "quotes": [{"original": "Hello.", "translation": "你好。"}],
    "follow_up_questions": ["問題？"],
})


class FakeSummarizer:
    """假的 Gemini：依標題決定回傳筆記或拋例外，並記錄被怎麼呼叫。"""

    def __init__(self, errors: dict[str, BaseException] | None = None) -> None:
        self.errors = errors or {}
        self.calls: list[tuple[str, str]] = []

    def _answer(self, kind: str, title: str) -> Note:
        self.calls.append((kind, title))
        if title in self.errors:
            raise self.errors[title]
        return Note(content=CONTENT, model_name="fake-flash")

    def summarize_transcript(self, show: str, title: str, transcript: str) -> Note:
        return self._answer("transcript", title)

    def summarize_audio(self, show: str, title: str, audio_url: str) -> Note:
        return self._answer("audio", title)


def _insert(settings: Settings, guid: str, *, published: datetime = OLD,
            content: str | None = "spotify_transcript", status: str = "processing") -> None:
    """放一集 processing 的集數；content 給 None 代表還沒抓內容。"""
    with connect(settings) as conn:
        conn.execute(
            """
            insert into episode_status
                (source_id, source_type, source_item_id, show_name, episode_title, published_at,
                 source_url, status, attempt_count, processing_started_at)
            select id, 'podcast', %s, name, %s, %s, 'https://x/a.mp3', %s, 1, now()
            from tracked_sources where name = 'Show A'
            """,
            (guid, guid, published, status),
        )
        if content:
            conn.execute(
                """
                insert into episode_contents (source_type, source_item_id, content_source,
                                              transcript)
                values ('podcast', %s, %s, %s)
                """,
                (guid, content, "Speaker 1\nHi." if content == "spotify_transcript" else None),
            )


def _status(settings: Settings) -> dict[str, tuple]:
    with connect(settings) as conn:
        rows = conn.execute(
            "select source_item_id, status, attempt_count, last_error from episode_status"
        ).fetchall()
    return {r["source_item_id"]: (r["status"], r["attempt_count"], r["last_error"]) for r in rows}


def _notes(settings: Settings) -> list[dict]:
    with connect(settings) as conn:
        return conn.execute("select * from notes order by source_item_id").fetchall()


def _run(settings: Settings, fake: FakeSummarizer):
    with connect(settings) as conn:
        return summarize_episodes(conn, fake)


def test_writes_note_and_completes(db: Settings) -> None:
    _insert(db, "t1")
    _insert(db, "a1", content="audio")
    _insert(db, "no-content-yet", content=None)  # 還沒抓內容的不處理
    fake = FakeSummarizer()

    report = _run(db, fake)

    assert report.completed == 2
    assert sorted(fake.calls) == [("audio", "a1"), ("transcript", "t1")]
    notes = {n["source_item_id"]: n for n in _notes(db)}
    assert set(notes) == {"a1", "t1"}
    t1 = notes["t1"]
    assert t1["one_liner"] == "一句話"
    assert t1["key_points"] == ["重點一", "重點二"]
    assert t1["people"] == [{"name": "Rene Haas", "context": "Arm CEO"}]
    assert t1["quotes"] == [{"original": "Hello.", "translation": "你好。"}]
    assert t1["content_source"] == "spotify_transcript"
    assert notes["a1"]["content_source"] == "audio"
    assert t1["model_name"] == "fake-flash"
    status = _status(db)
    assert status["t1"][0] == status["a1"][0] == "completed"
    assert status["no-content-yet"][0] == "processing"


def test_completed_episode_is_not_summarized_again(db: Settings) -> None:
    _insert(db, "t1")
    fake = FakeSummarizer()
    _run(db, fake)
    _run(db, fake)
    assert len(fake.calls) == 1


def test_error_marks_failed_and_continues(db: Settings) -> None:
    _insert(db, "bad", published=OLD + timedelta(days=1))
    _insert(db, "good")
    report = _run(db, FakeSummarizer({"bad": ValueError("invalid JSON from model")}))

    assert report.completed == 1
    status, _, error = _status(db)["bad"]
    assert status == "failed"
    assert "invalid JSON from model" in error
    assert [n["source_item_id"] for n in _notes(db)] == ["good"]


def test_quota_exhausted_releases_rest_and_raises(db: Settings) -> None:
    """當日額度用完：已完成的保留，這集和後面的放回 pending（不算嘗試次數），然後報錯。"""
    _insert(db, "newest", published=OLD + timedelta(days=2))
    _insert(db, "middle", published=OLD + timedelta(days=1))
    _insert(db, "oldest", published=OLD)
    fake = FakeSummarizer({"middle": QuotaExhausted("429")})

    with pytest.raises(QuotaExhausted):
        _run(db, fake)

    status = _status(db)
    assert status["newest"][0] == "completed"
    assert status["middle"] == ("pending", 0, None)
    assert status["oldest"] == ("pending", 0, None)
    assert ("transcript", "oldest") not in fake.calls
    # 內容保留著：明天重跑時不用重抓逐字稿
    with connect(db) as conn:
        n = conn.execute("select count(*) as n from episode_contents").fetchone()["n"]
    assert n == 3
