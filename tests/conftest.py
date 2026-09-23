"""測試共用的資料庫來源。

同一組連線測試，可以用兩種「真的 Postgres」各跑一次：

  auto_docker  測試自己起一個暫時的 Docker Postgres，整個測試跑完自動關掉。
  manual_env   你先自己起好一個 Postgres，用環境變數 PN_TEST_DB_PORT 告訴測試連去哪。
               沒設 PN_TEST_DB_PORT 就自動跳過。
"""

from __future__ import annotations

import os
import shutil
import subprocess
import time
import uuid
from collections.abc import Iterator
from typing import Any

import psycopg
import pytest

from podcast_notes.config import Settings
from podcast_notes.db import connect

# 手動模式只允許連本機，避免不小心把測試（會建表、刪表）指到真的 RDS。
_LOCAL_HOSTS = ("127.0.0.1", "localhost")


def _make_settings(**overrides: Any) -> Settings:
    """組出測試用的 Settings。

    _env_file=None：測試絕對不讀開發者本機的 .env，避免不小心連到真的資料庫。
    """
    values: dict[str, Any] = {
        "db_host": "127.0.0.1",
        "db_user": "postgres",
        "db_password": "test",
        "db_name": "postgres",
        "db_sslmode": "disable",  # Docker 官方 Postgres image 預設沒開 SSL
        "gemini_api_key": "fake",
        "rds_instance_id": "fake",
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)


def _docker_available() -> bool:
    if shutil.which("docker") is None:
        return False
    return subprocess.run(["docker", "info"], capture_output=True).returncode == 0


def _wait_until_ready(settings: Settings, timeout: float = 40) -> None:
    """一直嘗試連線，直到連得上為止。資料庫剛開機要幾秒，沒等好就連會失敗。"""
    deadline = time.monotonic() + timeout
    while True:
        try:
            with connect(settings) as conn:
                conn.execute("select 1")
            return
        except psycopg.OperationalError:
            if time.monotonic() > deadline:
                raise
            time.sleep(1)


@pytest.fixture
def make_settings():
    """讓測試自己組 Settings（例如指定不同的密碼）。"""
    return _make_settings


@pytest.fixture(scope="session")
def auto_docker_settings() -> Iterator[Settings]:
    if not _docker_available():
        pytest.skip("Docker 沒有在跑，略過需要真資料庫的測試")

    name = f"pn-test-{uuid.uuid4().hex[:8]}"
    subprocess.run(
        [
            "docker", "run", "-d", "--rm", "--name", name,
            "-e", "POSTGRES_PASSWORD=test",
            # 127.0.0.1::5432 = 只開在本機、由 Docker 隨機挑一個沒被占用的連接埠
            "-p", "127.0.0.1::5432",
            "postgres:16-alpine",
        ],
        check=True,
        capture_output=True,
    )
    try:
        out = subprocess.run(
            ["docker", "port", name, "5432/tcp"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout
        port = int(out.strip().splitlines()[0].rsplit(":", 1)[1])
        settings = _make_settings(db_port=port)
        _wait_until_ready(settings)
        yield settings
    finally:
        subprocess.run(["docker", "stop", name], capture_output=True)


@pytest.fixture(scope="session")
def manual_env_settings() -> Settings:
    port = os.environ.get("PN_TEST_DB_PORT")
    if not port:
        pytest.skip("沒設定 PN_TEST_DB_PORT，略過手動模式（要你先自己起好 Postgres）")

    host = os.environ.get("PN_TEST_DB_HOST", "127.0.0.1")
    if host not in _LOCAL_HOSTS:
        raise RuntimeError("為了安全，手動模式只允許連本機的資料庫（127.0.0.1 或 localhost）")

    settings = _make_settings(
        db_host=host,
        db_port=int(port),
        db_user=os.environ.get("PN_TEST_DB_USER", "postgres"),
        db_password=os.environ.get("PN_TEST_DB_PASSWORD", "test"),
        db_name=os.environ.get("PN_TEST_DB_NAME", "postgres"),
    )
    _wait_until_ready(settings, timeout=5)
    return settings


@pytest.fixture(params=["auto_docker", "manual_env"])
def pg_settings(request: pytest.FixtureRequest) -> Settings:
    """同一個測試，換不同的資料庫來源各跑一次。"""
    return request.getfixturevalue(f"{request.param}_settings")


@pytest.fixture
def demo_table(pg_settings: Settings) -> Iterator[str]:
    """每個測試開始前建一張乾淨的小表，結束後刪掉，測試之間互不影響。"""
    with connect(pg_settings) as conn:
        conn.execute("drop table if exists pn_test_demo")
        conn.execute("create table pn_test_demo (id int primary key, name text not null)")
    yield "pn_test_demo"
    with connect(pg_settings) as conn:
        conn.execute("drop table if exists pn_test_demo")
