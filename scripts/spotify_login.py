"""開一個看得到的瀏覽器視窗，讓人手動登入 Spotify，登入狀態存進設定檔（PN_SPOTIFY_PROFILE_DIR）。

什麼時候要跑：第一次設定、或抓逐字稿時出現 SpotifyLoginRequired（登入 cookie 過期）。
帳號密碼與真人驗證都由人在視窗裡自己完成，程式只負責開視窗、偵測登入成功後關掉。

用法：
    uv run --extra browser python scripts/spotify_login.py
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from playwright.sync_api import sync_playwright  # noqa: E402

from podcast_notes.config import get_spotify_settings  # noqa: E402
from podcast_notes.sources.spotify_transcript import (  # noqa: E402
    IGNORE_DEFAULT_ARGS,
    LAUNCH_ARGS,
)

_LOGIN_URL = "https://accounts.spotify.com/login?continue=https%3A%2F%2Fopen.spotify.com%2F"
# 登入後網頁播放器右上角的使用者頭像，出現就代表登入成功
_LOGGED_IN = '[data-testid="user-widget-link"]'
_MAX_WAIT_SECONDS = 600


def main() -> None:
    profile_dir = get_spotify_settings().spotify_profile_dir
    print(f"設定檔位置：{profile_dir.resolve()}")
    print("請在跳出的視窗裡登入 Spotify（最多等 10 分鐘）……")

    with sync_playwright() as p:
        context = p.chromium.launch_persistent_context(
            str(profile_dir),
            headless=False,
            args=LAUNCH_ARGS,
            ignore_default_args=IGNORE_DEFAULT_ARGS,
        )
        page = context.pages[0] if context.pages else context.new_page()
        page.goto(_LOGIN_URL)

        deadline = time.monotonic() + _MAX_WAIT_SECONDS
        while time.monotonic() < deadline:
            if "open.spotify.com" in page.url and page.locator(_LOGGED_IN).count():
                print("登入成功，登入狀態已存進設定檔。")
                context.close()
                return
            time.sleep(2)

        context.close()
        sys.exit("等太久沒偵測到登入成功，請重跑一次。")


if __name__ == "__main__":
    main()
