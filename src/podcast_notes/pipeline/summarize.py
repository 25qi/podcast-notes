"""摘要＋寫入任務：已經抓好內容的集數 → Gemini 寫筆記 → 寫進 notes、標 completed。

spec 原本把「摘要」和「寫入」切成兩個任務，這裡合成一步：
摘要結果如果要跨任務傳遞，得先找地方暫存（Airflow XCom 或再開一張表），
而「寫 notes」和「標 completed」本來就該在同一個交易裡一起成功或一起失敗（見 db.py），
合在一起反而最單純。切開的好處是「可以單獨重跑」，但寫入這一步幾乎不會失敗，用不到。

每一集的結果：
- 成功：寫入 notes、標 completed（同一個交易）
- Gemini 當日額度用完（429）：這集和後面的全部放回 pending（不算嘗試次數），拋出例外，明天再跑
- 其他錯誤：標 failed、記 last_error（spec：不自動重試）
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Protocol

import psycopg
from psycopg.rows import DictRow
from psycopg.types.json import Jsonb

from podcast_notes.pipeline.status import mark_failed, release_episode
from podcast_notes.summarize.gemini import Note, QuotaExhausted

log = logging.getLogger(__name__)


class Summarizer(Protocol):
    """summarize_episodes 需要的功能。正式跑用 GeminiSummarizer，測試可以換成假的。"""

    def summarize_transcript(self, show: str, title: str, transcript: str) -> Note: ...

    def summarize_audio(self, show: str, title: str, audio_url: str) -> Note: ...


@dataclass
class SummaryReport:
    completed: int = 0
    released: int = 0
    failed: dict[str, str] = field(default_factory=dict)


def summarize_episodes(
    conn: psycopg.Connection[DictRow], summarizer: Summarizer
) -> SummaryReport:
    """處理所有「processing、已有內容、還沒有筆記」的集數，由新到舊。"""
    episodes = conn.execute(
        """
        select e.id, e.source_type, e.source_item_id, e.show_name, e.episode_title,
               e.published_at, e.source_url, c.content_source, c.transcript
        from episode_status e
        join episode_contents c using (source_type, source_item_id)
        where e.status = 'processing'
          and not exists (
              select 1 from notes n
              where n.source_type = e.source_type and n.source_item_id = e.source_item_id
          )
        order by e.published_at desc
        """
    ).fetchall()
    # 結束讀取用的隱含交易，下面每集才是獨立 commit 的交易（原因同 fetch.sync_sources）
    conn.commit()

    report = SummaryReport()
    for i, ep in enumerate(episodes):
        try:
            if ep["content_source"] == "spotify_transcript":
                note = summarizer.summarize_transcript(
                    ep["show_name"], ep["episode_title"], ep["transcript"]
                )
            else:
                note = summarizer.summarize_audio(
                    ep["show_name"], ep["episode_title"], ep["source_url"]
                )
        except QuotaExhausted:
            for rest in episodes[i:]:
                release_episode(conn, rest["id"])
            report.released += len(episodes) - i
            raise
        except Exception as e:  # noqa: BLE001  一集失敗不影響其他集
            log.exception("%s 摘要失敗：%s", ep["show_name"], ep["source_item_id"])
            message = f"摘要失敗：{type(e).__name__}: {e}"
            mark_failed(conn, ep["id"], message)
            report.failed[ep["source_item_id"]] = message
            continue

        _write_note(conn, ep, note)
        report.completed += 1
    return report


def _write_note(conn: psycopg.Connection[DictRow], ep: dict, note: Note) -> None:
    """寫入 notes 並標 completed。兩件事在同一個交易：要嘛都成功，要嘛都不算數。"""
    c = note.content
    with conn.transaction():
        conn.execute(
            """
            insert into notes
                (source_type, source_item_id, show_name, episode_title, published_at, source_url,
                 one_liner, guest_background, summary, key_points, people, companies, quotes,
                 follow_up_questions, content_source, model_name)
            values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                ep["source_type"], ep["source_item_id"], ep["show_name"], ep["episode_title"],
                ep["published_at"], ep["source_url"],
                c.one_liner, c.guest_background, c.summary,
                Jsonb(c.key_points),
                Jsonb([m.model_dump() for m in c.people]),
                Jsonb([m.model_dump() for m in c.companies]),
                Jsonb([q.model_dump() for q in c.quotes]),
                Jsonb(c.follow_up_questions),
                ep["content_source"], note.model_name,
            ),
        )
        conn.execute(
            """
            update episode_status
            set status = 'completed', last_error = null, updated_at = now()
            where id = %s
            """,
            (ep["id"],),
        )
