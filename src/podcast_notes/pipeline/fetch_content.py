"""抓內容任務：替已撿起來（processing）的集數決定摘要要用什麼內容，寫進 episode_contents。

對應 spec「排程／執行層」任務 3。每一集的結果只會是下面其中一種：

- 逐字稿：抓到 Spotify 逐字稿 → 存全文（content_source = spotify_transcript）
- 音檔：節目沒有 Spotify id、或這集沒比對到 Spotify、或 Spotify 上這集沒有逐字稿
  → 記一筆 audio，摘要任務改餵音檔
- 放回去：不是這集本身的問題，放回 pending、嘗試次數不算，下一輪再試
    - 有比對到 Spotify，但逐字稿還沒產生、發布未滿 24 小時（跟 fetch 的等待期同一個決定）
    - Spotify 登入失效：這一批剩下的全部放回去，然後拋出例外讓任務失敗、提醒人重新登入
- 失敗：其他錯誤（網路逾時、Spotify 改版……）→ 標 failed、記 last_error，等人工處理（spec）
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Protocol

import psycopg
from psycopg.rows import DictRow

from podcast_notes.pipeline.fetch import SPOTIFY_GRACE_PERIOD
from podcast_notes.sources.spotify_transcript import SpotifyLoginRequired, Transcript

log = logging.getLogger(__name__)


class TranscriptSource(Protocol):
    """fetch_contents 需要的逐字稿功能。正式跑用 SpotifyTranscriptFetcher，測試可以換成假的。"""

    def fetch(self, episode_id: str) -> Transcript | None: ...


@dataclass
class ContentReport:
    transcripts: int = 0
    audio: int = 0
    released: int = 0
    """放回 pending 的集數（逐字稿還沒產生、或登入失效）"""
    failed: dict[str, str] = field(default_factory=dict)
    """失敗的集數：source_item_id → 錯誤訊息"""


def fetch_contents(
    conn: psycopg.Connection[DictRow], transcripts: TranscriptSource
) -> ContentReport:
    """處理所有「processing 但還沒有內容」的集數。由新到舊處理（spec 回填方式）。

    每一集各自一個交易：處理到一半程式掛掉，已經處理完的集數不會白做。
    """
    episodes = conn.execute(
        """
        select e.id, e.source_type, e.source_item_id, e.show_name, e.published_at,
               e.spotify_episode_id, t.spotify_show_id
        from episode_status e
        join tracked_sources t on t.id = e.source_id
        where e.status = 'processing'
          and not exists (
              select 1 from episode_contents c
              where c.source_type = e.source_type and c.source_item_id = e.source_item_id
          )
        order by e.published_at desc
        """
    ).fetchall()
    # 結束讀取用的隱含交易，下面每集的 conn.transaction() 才會是真正獨立 commit 的交易
    # （原因同 fetch.sync_sources）
    conn.commit()

    report = ContentReport()
    for i, ep in enumerate(episodes):
        if not ep["spotify_show_id"] or not ep["spotify_episode_id"]:
            # 沒走 Spotify 的節目，或 fetch 階段 24 小時內都沒比對到（claim 才會放行到這裡）
            _save_audio(conn, ep)
            report.audio += 1
            continue

        try:
            transcript = transcripts.fetch(ep["spotify_episode_id"])
        except SpotifyLoginRequired:
            # 登入失效不是這一集的錯，後面每一集也都會失敗：全部放回去，拋出去讓人重新登入
            for rest in episodes[i:]:
                _release(conn, rest)
            report.released += len(episodes) - i
            raise
        except Exception as e:  # noqa: BLE001  一集失敗不影響其他集
            log.exception("%s 抓內容失敗：%s", ep["show_name"], ep["source_item_id"])
            message = f"抓內容失敗：{type(e).__name__}: {e}"
            _mark_failed(conn, ep, message)
            report.failed[ep["source_item_id"]] = message
            continue

        if transcript is not None:
            _save_transcript(conn, ep, transcript)
            report.transcripts += 1
        elif datetime.now(UTC) - ep["published_at"] < SPOTIFY_GRACE_PERIOD:
            # Spotify 有這集但逐字稿還沒產生，發布未滿 24 小時，等下一輪
            _release(conn, ep)
            report.released += 1
        else:
            _save_audio(conn, ep)
            report.audio += 1
    return report


def _save_transcript(conn: psycopg.Connection[DictRow], ep: dict, t: Transcript) -> None:
    with conn.transaction():
        conn.execute(
            """
            insert into episode_contents
                (source_type, source_item_id, content_source, transcript,
                 transcript_language, transcript_word_count)
            values (%s, %s, 'spotify_transcript', %s, %s, %s)
            """,
            (ep["source_type"], ep["source_item_id"], t.text, t.language, t.word_count),
        )


def _save_audio(conn: psycopg.Connection[DictRow], ep: dict) -> None:
    with conn.transaction():
        conn.execute(
            """
            insert into episode_contents (source_type, source_item_id, content_source)
            values (%s, %s, 'audio')
            """,
            (ep["source_type"], ep["source_item_id"]),
        )


def _release(conn: psycopg.Connection[DictRow], ep: dict) -> None:
    """放回 pending，並把 claim 時加上去的那一次嘗試扣回來（這次不算這一集的錯）。"""
    with conn.transaction():
        conn.execute(
            """
            update episode_status
            set status = 'pending',
                processing_started_at = null,
                attempt_count = attempt_count - 1,
                updated_at = now()
            where id = %s
            """,
            (ep["id"],),
        )


def _mark_failed(conn: psycopg.Connection[DictRow], ep: dict, message: str) -> None:
    with conn.transaction():
        conn.execute(
            """
            update episode_status
            set status = 'failed', last_error = %s, updated_at = now()
            where id = %s
            """,
            (message, ep["id"]),
        )
