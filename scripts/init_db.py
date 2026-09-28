"""依序執行 sql/ 底下還沒跑過的 SQL 檔，建表或補上新的變更。

用 schema_migrations 表記錄哪些檔案已經跑過，所以可以重複執行：
第一次會跑 001～最新，之後新增 006 時只會補跑 006。本機 Postgres 和之後的 RDS 都用這支。

規則：已經跑過的檔案不要再改內容（改了也不會重跑）；要改表結構就新增下一個編號的檔案。

用法（在專案根目錄執行，連線設定讀 .env 的 PN_DB_*）：
    uv run python scripts/init_db.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from podcast_notes.db import connect  # noqa: E402

SQL_DIR = Path(__file__).resolve().parent.parent / "sql"


def main() -> None:
    with connect() as conn:
        conn.execute(
            """
            create table if not exists schema_migrations (
                filename text primary key,
                applied_at timestamptz not null default now()
            )
            """
        )
        applied = {r["filename"] for r in conn.execute("select filename from schema_migrations")}
        conn.commit()

        pending = [p for p in sorted(SQL_DIR.glob("*.sql")) if p.name not in applied]
        if not pending:
            print("資料庫已是最新，沒有要跑的 SQL。")
            return
        for path in pending:
            # 一個檔案一個交易：檔案跑到一半出錯就整個復原，也不會被記成已執行
            with conn.transaction():
                conn.execute(path.read_text(encoding="utf-8"))
                conn.execute("insert into schema_migrations (filename) values (%s)", (path.name,))
            print(f"已執行 {path.name}")


if __name__ == "__main__":
    main()
