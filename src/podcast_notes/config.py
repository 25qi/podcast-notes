"""集中管理所有設定值。

設計原則：程式碼裡任何地方都不直接讀 os.environ，一律透過這裡的 Settings。
好處是（1）型別錯誤在啟動時就炸，不是跑到一半才炸；
（2）之後要換成 AWS Secrets Manager 或 Airflow Variable，只要改這一個檔案。
"""

from __future__ import annotations

from datetime import date
from functools import lru_cache
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

_CONFIG = SettingsConfigDict(
    env_prefix="PN_",
    env_file=".env",
    env_file_encoding="utf-8",
    extra="ignore",
)


class Settings(BaseSettings):
    model_config = _CONFIG

    # --- 資料庫 ---
    db_host: str
    db_port: int = 5432
    db_name: str = "podcast_notes"
    db_user: str
    db_password: str
    # SSL 連線模式（libpq 的 sslmode）。預設 require：一定要加密，沒加密就拒絕連線。
    # 本機測試用的 Docker Postgres 沒開 SSL，要改成 disable。
    # require 只加密、不驗證對方身分；要驗證得改 verify-full 並提供 RDS 的 CA 憑證，之後再做。
    db_sslmode: Literal[
        "disable", "allow", "prefer", "require", "verify-ca", "verify-full"
    ] = "require"
    # 連線逾時秒數。不設的話，RDS 還沒開好時一次連線可能等很久；
    # 「等待 RDS 可連線」那個 Sensor 需要每次嘗試都快速失敗，才能反覆重試。
    db_connect_timeout: int = 10

    # --- Gemini ---
    gemini_api_key: str
    # 用 -latest 別名而不是寫死版本號，Google 汰換舊版時不用改程式碼。
    gemini_model: str = "gemini-flash-latest"

    # --- AWS ---
    aws_region: str = "ap-northeast-1"
    rds_instance_id: str

    # --- 監控 ---
    healthcheck_ping_url: str | None = None

    # --- 回填 ---
    backfill_since: date = Field(default=date(2025, 1, 1))


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """快取成單例。Airflow 每個 task 是獨立 process，各自建一次就好。"""
    return Settings()  # type: ignore[call-arg]


class SpotifySettings(BaseSettings):
    """Spotify 官方 Web API 的設定（把 RSS 集數對應到 Spotify episode id 用）。

    為什麼不放進 Settings：Settings 要求 DB、Gemini 等欄位一定要有，
    但比對試跑（scripts/spotify_match_dry_run.py）只需要 Spotify，不該因為還沒建 RDS 就跑不了。
    反過來，只跑 RSS 抓取的任務也不該因為沒填 Spotify 而啟動失敗。
    讀的是同一份 .env、同樣的 PN_ 前綴，只是分開驗證。
    """

    model_config = _CONFIG

    # developer.spotify.com/dashboard 建 app 取得
    spotify_client_id: str
    spotify_client_secret: str
    # 查詢集數時帶的國家代碼（官方文件：只回傳在該地區可用的內容）。
    # client credentials 沒有登入使用者，也就沒有「使用者所在國家」可推，所以明確指定一個。
    # 追蹤的都是美國節目，用 US。
    spotify_market: str = "US"


@lru_cache(maxsize=1)
def get_spotify_settings() -> SpotifySettings:
    return SpotifySettings()  # type: ignore[call-arg]
