"""測試 config.py 的 Settings 是否照我們預期的方式運作。"""

from datetime import date

from podcast_notes.config import Settings


def test_backfill_since_default_is_2025_01_01(monkeypatch: object) -> None:
    """驗證：如果沒特別指定，回填起點的預設值應該是 2025-01-01。"""
    # 先把 Settings 一定要有的欄位假造出來，不然它會因為缺欄位而報錯
    monkeypatch.setenv("PN_DB_HOST", "fake-host")
    monkeypatch.setenv("PN_DB_USER", "fake-user")
    monkeypatch.setenv("PN_DB_PASSWORD", "fake-password")
    monkeypatch.setenv("PN_GEMINI_API_KEY", "fake-key")
    monkeypatch.setenv("PN_RDS_INSTANCE_ID", "fake-instance")

    settings = Settings()

    assert settings.backfill_since == date(2025, 1, 1)
