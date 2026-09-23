"""選項 B：不連真的資料庫，用假的 psycopg.connect 檢查「我們傳了什麼參數」。"""

from __future__ import annotations

import psycopg
import pytest
from psycopg.rows import dict_row

from podcast_notes.db import connect


def test_connect_passes_settings_to_psycopg(monkeypatch, make_settings):
    captured: dict = {}

    def fake_connect(**kwargs):
        captured.update(kwargs)
        return "fake-connection"

    monkeypatch.setattr(psycopg, "connect", fake_connect)

    settings = make_settings(
        db_host="h",
        db_port=6543,
        db_name="d",
        db_user="u",
        db_password="p@ss/w:rd",
        db_sslmode="require",
        db_connect_timeout=7,
    )

    assert connect(settings) == "fake-connection"
    assert captured == {
        "host": "h",
        "port": 6543,
        "dbname": "d",
        "user": "u",
        "password": "p@ss/w:rd",
        "sslmode": "require",
        "connect_timeout": 7,
        "row_factory": dict_row,
    }


def test_unreachable_database_raises_operational_error(make_settings):
    """連不上（這裡用一個沒人監聽的連接埠）要丟出 OperationalError，
    「等待 RDS 可連線」的 Sensor 靠捕捉這個錯誤來決定要不要重試。"""
    settings = make_settings(db_port=1, db_connect_timeout=2)
    with pytest.raises(psycopg.OperationalError):
        connect(settings)
