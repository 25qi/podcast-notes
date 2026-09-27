"""episode_status 的狀態轉換，抓內容與摘要任務共用。

每個函式自己開一個交易（呼叫前要先結束讀取用的隱含交易，原因見 fetch.sync_sources），
而且照 spec 由程式自己設 updated_at = now()（2026-09-27 決定不用 DB trigger）。
"""

from __future__ import annotations

import psycopg
from psycopg.rows import DictRow


def release_episode(conn: psycopg.Connection[DictRow], episode_id: int) -> None:
    """放回 pending，並把 claim 時加上去的那一次嘗試扣回來。

    用在「不是這一集的錯」的情況：Spotify 登入失效、逐字稿還沒產生、Gemini 當日額度用完。
    """
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
            (episode_id,),
        )


def mark_failed(conn: psycopg.Connection[DictRow], episode_id: int, message: str) -> None:
    """標 failed、記錄原因。spec：失敗不自動重試，等人工重置。"""
    with conn.transaction():
        conn.execute(
            """
            update episode_status
            set status = 'failed', last_error = %s, updated_at = now()
            where id = %s
            """,
            (message, episode_id),
        )
