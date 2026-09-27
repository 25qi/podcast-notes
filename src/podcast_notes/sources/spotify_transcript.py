"""Spotify 逐字稿：用已登入的瀏覽器開集數頁，攔截頁面自己發出的逐字稿請求。

做法與理由（探勘證據見 docs/podcast-sources.md 第四輪）：
- 逐字稿不在官方 Web API 裡，只有網頁播放器登入後才拿得到。
- 不自己去要 token 呼叫逐字稿 API（無頭模式自己要 token 會被擋，回 401），
  而是讓頁面照常運作、我們點 Transcript 分頁，再攔截頁面發出的
  spclient.wg.spotify.com/transcript-read-along/v2/episode/<id> 回應。

這是非公開 API，Spotify 改版就可能壞，所以：
- 分清楚「這集沒有逐字稿」（回 None，呼叫端退回音檔）和「程式壞了 / 登入過期」（拋例外），
  兩者處理方式不同，混在一起會讓壞掉的情況被默默當成沒逐字稿。
- JSON 解析（parse_transcript）跟瀏覽器操作分開，格式變了只要改解析那段，而且可以不開瀏覽器單獨測。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from playwright.sync_api import Page, Playwright, sync_playwright
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

_EPISODE_URL = "https://open.spotify.com/episode/{episode_id}"
_TRANSCRIPT_API = "spclient.wg.spotify.com/transcript-read-along/v2/episode/"

# 頁面上用來判斷狀態的元素（2026-09-27 實測）
_TRANSCRIPT_TAB = "#transcript-tab"  # 有逐字稿時才出現的分頁
_DESCRIPTION_TAB = "#description-tab"  # 登入後集數頁一定有的分頁，出現代表頁面載入完成
_LOGIN_BUTTON = '[data-testid="login-button"]'  # 未登入時右上角的登入按鈕

# 等頁面載入的上限。Spotify 網頁播放器很肥，網路慢時 10 秒不夠。
_PAGE_TIMEOUT_MS = 30_000
# 點下 Transcript 之後等逐字稿回應的上限
_TRANSCRIPT_TIMEOUT_MS = 20_000

# 規避網站偵測「這是自動化瀏覽器」的旗標。不加的話登入時比較容易被要求真人驗證。
LAUNCH_ARGS = ["--disable-blink-features=AutomationControlled"]
IGNORE_DEFAULT_ARGS = ["--enable-automation"]


class SpotifyLoginRequired(Exception):
    """瀏覽器設定檔沒有登入（或登入過期）。要人工執行 scripts/spotify_login.py 重新登入。"""


@dataclass(frozen=True)
class Transcript:
    text: str
    """整理後的逐字稿：段落標題（講者或章節）獨立一行，下面接內文，段落間空一行。"""

    language: str | None
    sentence_count: int
    word_count: int


class SpotifyTranscriptFetcher:
    """開一個瀏覽器，連續抓多集逐字稿，用完關掉。

    一個 fetcher 共用同一個瀏覽器，不是每集重開一次：開瀏覽器要好幾秒，回填幾百集時差很多。
    同一個設定檔不能同時被兩個瀏覽器使用（Playwright 官方文件），所以不要平行開多個 fetcher。
    """

    def __init__(self, profile_dir: Path, *, headless: bool = True) -> None:
        self._profile_dir = profile_dir
        self._headless = headless
        self._playwright: Playwright | None = None

    def __enter__(self) -> SpotifyTranscriptFetcher:
        if not self._profile_dir.exists():
            # 不讓 Playwright 自動建一個空的設定檔：那樣會變成「未登入」，錯誤訊息不直觀
            raise SpotifyLoginRequired(
                f"找不到瀏覽器設定檔 {self._profile_dir}，先執行 scripts/spotify_login.py 登入"
            )
        self._playwright = sync_playwright().start()
        self._context = self._playwright.chromium.launch_persistent_context(
            str(self._profile_dir),
            headless=self._headless,
            args=LAUNCH_ARGS,
            ignore_default_args=IGNORE_DEFAULT_ARGS,
        )
        self._page = self._context.new_page()
        return self

    def __exit__(self, *exc: object) -> None:
        self._context.close()
        if self._playwright:
            self._playwright.stop()

    def fetch(self, episode_id: str) -> Transcript | None:
        """抓一集的逐字稿。這集沒有逐字稿回 None；未登入拋 SpotifyLoginRequired。

        其他狀況（頁面載入逾時、點了 Transcript 卻沒等到回應）讓 Playwright 的例外直接往上拋，
        由呼叫端記成這集失敗、之後重試——這些可能是網路問題，也可能是 Spotify 改版，
        不能當成沒逐字稿。
        """
        page = self._page
        page.goto(_EPISODE_URL.format(episode_id=episode_id), wait_until="domcontentloaded")
        _wait_until_loaded(page)

        if page.locator(_LOGIN_BUTTON).count():
            raise SpotifyLoginRequired("Spotify 登入已失效，執行 scripts/spotify_login.py 重新登入")
        if not page.locator(_TRANSCRIPT_TAB).count():
            return None

        with page.expect_response(
            lambda r: _TRANSCRIPT_API + episode_id in r.url, timeout=_TRANSCRIPT_TIMEOUT_MS
        ) as response_info:
            page.locator(_TRANSCRIPT_TAB).click()
        response = response_info.value
        if not response.ok:
            raise RuntimeError(f"逐字稿請求失敗：HTTP {response.status}（episode {episode_id}）")
        return parse_transcript(response.json())


def _wait_until_loaded(page: Page) -> None:
    """等到「已登入的集數頁」或「未登入的登入按鈕」其中一個出現。

    不能只等 Transcript 分頁：沒逐字稿的集數永遠等不到它，會變成每集都卡滿逾時才判定沒有。
    Transcript 和 Description 分頁是一起出現的，所以等到 Description 出現時，
    Transcript 沒出現就代表這集真的沒有。
    """
    ready = page.locator(_DESCRIPTION_TAB).or_(page.locator(_LOGIN_BUTTON))
    try:
        ready.first.wait_for(timeout=_PAGE_TIMEOUT_MS)
    except PlaywrightTimeoutError as e:
        seconds = _PAGE_TIMEOUT_MS // 1000
        raise PlaywrightTimeoutError(f"集數頁 {seconds} 秒內沒載入完成：{page.url}") from e


def parse_transcript(payload: dict) -> Transcript:
    """把逐字稿 API 的 JSON 整理成純文字。

    JSON 的 section 是一串依時間排序的區塊，兩種：
    - {"title": {"title": "..."}}：段落標題。實測有兩種內容——講者（"Speaker 1"），
      或 Spotify 自動切的章節標題（"How Misleading AI Language Echoes..."）；
      title 是空物件時代表換段落、沒有新標題。
    - {"text": {"sentence": {"text": "..."}}}：一句話

    輸出格式：標題獨立一行、下面接內文，段落間空一行。
    標題不寫成「標題: 內文」，因為章節標題會被誤讀成講者名字。
    保留標題，Gemini 寫摘要時才分得出是誰在講、在講哪個主題。
    """
    paragraphs: list[str] = []
    heading: str | None = None
    current: list[str] = []
    sentence_count = word_count = 0

    def flush() -> None:
        nonlocal heading
        if current:
            body = " ".join(current)
            paragraphs.append(f"{heading}\n{body}" if heading else body)
            current.clear()
        heading = None

    for section in payload.get("section", []):
        if "title" in section:
            flush()
            heading = section["title"].get("title") or None
        elif sentence := section.get("text", {}).get("sentence", {}).get("text"):
            current.append(sentence.strip())
            sentence_count += 1
            word_count += len(sentence.split())  # 只算講話內容，不含標題
    flush()

    text = "\n\n".join(paragraphs)
    return Transcript(
        text=text,
        language=payload.get("language"),
        sentence_count=sentence_count,
        word_count=word_count,
    )
