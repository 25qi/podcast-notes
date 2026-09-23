"""連線程式對真的 Postgres 的行為。

每個測試會用 pg_settings 跑兩次：一次連自動起的 Docker（auto_docker），
一次連你手動先起好的 Postgres（manual_env，沒設定就跳過）。
"""

from __future__ import annotations

import psycopg
import pytest
from psycopg import sql

from podcast_notes.db import connect


def test_can_connect_and_query(pg_settings):
    with connect(pg_settings) as conn:
        row = conn.execute("select 1 as one").fetchone()
    # 每一列是 dict，用欄位名稱取值
    assert row == {"one": 1}


def test_connection_is_closed_after_with_block(pg_settings):
    with connect(pg_settings) as conn:
        pass
    assert conn.closed


def test_data_is_committed_when_block_succeeds(pg_settings, demo_table):
    with connect(pg_settings) as conn:
        conn.execute("insert into pn_test_demo values (1, 'a')")

    # 另開一條新連線來讀，證明資料真的存進資料庫，不只是同一條連線自己看得到
    with connect(pg_settings) as conn:
        rows = conn.execute("select id, name from pn_test_demo").fetchall()
    assert rows == [{"id": 1, "name": "a"}]


def test_data_is_rolled_back_when_block_fails(pg_settings, demo_table):
    with pytest.raises(RuntimeError):
        with connect(pg_settings) as conn:
            conn.execute("insert into pn_test_demo values (1, 'a')")
            raise RuntimeError("模擬寫到一半出錯")

    with connect(pg_settings) as conn:
        row = conn.execute("select count(*) as n from pn_test_demo").fetchone()
    assert row["n"] == 0


def test_sql_typo_is_caught_by_real_database(pg_settings, demo_table):
    with pytest.raises(psycopg.errors.UndefinedColumn):
        with connect(pg_settings) as conn:
            conn.execute("select namee from pn_test_demo")


def test_password_with_special_characters(pg_settings):
    """密碼含 @ / : # 空白等符號也要連得上（這是不用網址字串、改用關鍵字傳參數的原因）。"""
    weird = "p@ss/w:rd#1 ?%&="

    with connect(pg_settings) as conn:
        conn.execute("drop role if exists pn_weird")
        conn.execute(
            sql.SQL("create role pn_weird login password {}").format(sql.Literal(weird))
        )
    try:
        weird_settings = pg_settings.model_copy(
            update={"db_user": "pn_weird", "db_password": weird}
        )
        with connect(weird_settings) as conn:
            row = conn.execute("select current_user as u").fetchone()
        assert row == {"u": "pn_weird"}
    finally:
        with connect(pg_settings) as conn:
            conn.execute("drop role if exists pn_weird")
