"""資料庫連線。

只提供一個 connect()，回傳設定好的 psycopg 連線，交給呼叫端用 `with` 管理：

    with connect() as conn:
        conn.execute("update ...")
        conn.execute("insert ...")

離開 with 區塊時：沒出錯就一次存檔（commit），出錯就整個復原（rollback），
兩種情況連線都會關閉（psycopg 3 官方文件的行為）。

「寫入」任務要「寫 notes」和「更新 episode_status」兩件事要嘛都成功、要嘛都不算數，
就是靠這個交易機制保證。
"""

from __future__ import annotations

import psycopg
from psycopg.rows import DictRow, dict_row

from podcast_notes.config import Settings, get_settings


def connect(settings: Settings | None = None) -> psycopg.Connection[DictRow]:
    """建立到 Postgres 的連線。不傳 settings 就用環境變數讀進來的設定。

    連線參數用關鍵字傳，不組成 postgresql://user:password@host 這種網址字串，
    因為密碼含 @ / : # 等符號時，網址字串要另外逸出，關鍵字傳法沒有這個問題。

    查詢結果每一列是 dict（例如 {"id": 1, "name": "20VC"}），不是 tuple，
    用欄位名稱取值，不用記第幾個位置。
    """
    s = settings or get_settings()
    return psycopg.connect(
        host=s.db_host,
        port=s.db_port,
        dbname=s.db_name,
        user=s.db_user,
        password=s.db_password,
        sslmode=s.db_sslmode,
        connect_timeout=s.db_connect_timeout,
        row_factory=dict_row,
    )
