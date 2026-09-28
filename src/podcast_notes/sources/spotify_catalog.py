"""Spotify 官方 Web API：列出一個節目的集數，並把 RSS 的集數對應到 Spotify episode id。

為什麼需要這一層：Spotify 逐字稿要用 Spotify 的 episode id 去抓（open.spotify.com/episode/<id>），
但我們的集數清單來自 RSS，只有 guid，兩邊沒有共同的識別碼，只能靠標題和日期比對。

這裡只用官方公開 API（client credentials 流程，不需要登入使用者），
跟抓逐字稿那段（非公開 API + 登入的瀏覽器）分開：
比對這步壞了跟逐字稿壞了是兩回事，分開才查得出是誰壞。

跟 podcast.py 一樣，這一層不碰資料庫；比對結果交給 pipeline 層寫進 episode_status。
"""

from __future__ import annotations

import re
import time
import unicodedata
from dataclasses import dataclass
from datetime import date, timedelta
from difflib import SequenceMatcher

import httpx

from podcast_notes.sources.podcast import Episode

_TOKEN_URL = "https://accounts.spotify.com/api/token"
_API_BASE = "https://api.spotify.com/v1"

API_TIMEOUT = 10.0

# /shows/{id}/episodes 每頁上限是 50（官方文件），用上限可以少打幾次 API。
_PAGE_SIZE = 50

# 被限流（HTTP 429）時最多重試幾次。Spotify 會在 Retry-After 標頭告訴你要等幾秒。
_MAX_RATE_LIMIT_RETRIES = 3

# Retry-After 超過這個秒數就不等了，直接拋 SpotifyRateLimited。
# 2026-09-28 實測：第一次回填連續翻了幾十頁，Spotify 回 Retry-After: 15078（約 4 小時），
# 程式照著睡 4 小時，看起來就像卡死。等這麼久不如這輪先跳過比對，下一輪再來。
_MAX_RETRY_AFTER_SECONDS = 60

# 翻頁之間停一下，放慢打 API 的速度。開發模式的限流額度官方沒公布，保守一點。
_PAGE_INTERVAL_SECONDS = 1.0

# 日期不同但標題相近時，相似度要達到多少才算同一集。
# 0.8 是保守值：同一集常見的差異只是多了節目名前綴或標點不同，相似度通常 > 0.9；
# 同一天發的兩集標題完全不同，相似度通常 < 0.5。寧可比不到（退回音檔），也不要配錯。
_TITLE_SIMILARITY_THRESHOLD = 0.8

# 發布日期容許差幾天。RSS 是精確到秒的 UTC 時間，Spotify 只有日期而且沒說是哪個時區，
# 美國晚上發的節目，UTC 已經是隔天，所以要容許前後各差一天。
_DATE_TOLERANCE = timedelta(days=1)


@dataclass(frozen=True)
class SpotifyEpisode:
    """Spotify 上的一集，只留比對需要的欄位。"""

    id: str
    name: str
    release_date: date | None
    """精確到「日」才有值；Spotify 對老集數有時只給到年或月，那種就當沒日期。"""


class SpotifyRateLimited(Exception):
    """Spotify 要求等太久（Retry-After 超過 _MAX_RETRY_AFTER_SECONDS）。這一輪先放棄比對。"""

    def __init__(self, retry_after: int) -> None:
        super().__init__(f"Spotify 限流，要求等 {retry_after} 秒")
        self.retry_after = retry_after


class SpotifyCatalog:
    """呼叫 Spotify 官方 Web API 的客戶端。

    token 有效期 3600 秒（官方文件），這裡快取起來，快過期才重拿，
    避免每列一個節目就要一次 token。
    """

    def __init__(
        self,
        client_id: str,
        client_secret: str,
        *,
        market: str = "US",
        client: httpx.Client | None = None,
    ) -> None:
        self._client_id = client_id
        self._client_secret = client_secret
        self._market = market
        self._http = client or httpx.Client(timeout=API_TIMEOUT)
        self._token: str | None = None
        self._token_expires_at = 0.0

    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> SpotifyCatalog:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def list_show_episodes(
        self, show_id: str, *, stop_before: date | None = None
    ) -> list[SpotifyEpisode]:
        """列出一個節目在 Spotify 上的集數（實測回傳順序：由新到舊）。

        stop_before：翻到某一頁的最後一集比這個日期還舊，就不再往下翻。
        平常每天只需要比對最新幾集，不用每次把 1,500 集的節目整個翻完（20VC 要 31 頁）；
        不給就全部列出（第一次回填時）。
        """
        episodes: list[SpotifyEpisode] = []
        url: str | None = f"{_API_BASE}/shows/{show_id}/episodes"
        params: dict[str, str | int] | None = {"limit": _PAGE_SIZE, "market": self._market}
        while url:
            data = self._get(url, params)
            # 官方文件說 items 不會是 null，但實務上下架的集數會以 null 出現在清單裡，
            # 跳過就好，不要讓整個節目因為一集壞資料而失敗。
            page = [_to_spotify_episode(item) for item in data["items"] if item]
            episodes.extend(page)
            # next 是完整網址（已經帶好 offset、limit、market），直接跟著走，參數不用再帶。
            url = data.get("next")
            params = None
            oldest = next((ep.release_date for ep in reversed(page) if ep.release_date), None)
            if stop_before and oldest and oldest < stop_before:
                break
            if url:
                time.sleep(_PAGE_INTERVAL_SECONDS)
        return episodes

    def _get(self, url: str, params: dict[str, str | int] | None) -> dict:
        for _ in range(_MAX_RATE_LIMIT_RETRIES + 1):
            response = self._http.get(
                url, params=params, headers={"Authorization": f"Bearer {self._access_token()}"}
            )
            if response.status_code != 429:
                response.raise_for_status()
                return response.json()
            retry_after = int(response.headers.get("Retry-After", "1"))
            if retry_after > _MAX_RETRY_AFTER_SECONDS:
                raise SpotifyRateLimited(retry_after)
            time.sleep(retry_after)
        response.raise_for_status()
        raise AssertionError("unreachable")  # raise_for_status 對 429 一定會拋例外

    def _access_token(self) -> str:
        # 提早 60 秒換新，避免拿到一個「送出去的途中剛好過期」的 token。
        if self._token and time.monotonic() < self._token_expires_at - 60:
            return self._token
        response = self._http.post(
            _TOKEN_URL,
            data={"grant_type": "client_credentials"},
            auth=(self._client_id, self._client_secret),  # httpx 會轉成 Basic base64(id:secret)
        )
        response.raise_for_status()
        payload = response.json()
        self._token = payload["access_token"]
        self._token_expires_at = time.monotonic() + payload["expires_in"]
        return self._token


def _to_spotify_episode(item: dict) -> SpotifyEpisode:
    release_date = None
    if item.get("release_date_precision") == "day":
        release_date = date.fromisoformat(item["release_date"])
    return SpotifyEpisode(id=item["id"], name=item["name"], release_date=release_date)


def match_episodes(
    rss_episodes: list[Episode], spotify_episodes: list[SpotifyEpisode]
) -> dict[str, str]:
    """把 RSS 集數對應到 Spotify 集數，回傳 {RSS guid: Spotify episode id}。

    比不到的不會出現在結果裡，呼叫端就把那集的 spotify_episode_id 留空、退回用音檔。

    兩輪比對，先嚴後寬：
    1. 正規化後標題完全相同 → 直接配對。大部分節目兩邊標題一模一樣，這輪就解決。
    2. 剩下的：日期差一天以內、而且標題相似度 >= 門檻的候選，只有一個才配對。
       有兩個以上就放棄——同一天發兩集、標題又很像的情況，猜錯的代價是拿別集逐字稿寫筆記，
       比「比不到、退回音檔」嚴重得多。

    每個 Spotify 集數最多只配給一個 RSS 集數（對應資料庫的 unique 約束）。
    """
    result: dict[str, str] = {}
    used: set[str] = set()

    # 第一輪：標題完全相同。同一個正規化標題對到多集時（例如重播）不在這輪處理，丟給第二輪看日期。
    by_title: dict[str, list[SpotifyEpisode]] = {}
    for sp in spotify_episodes:
        by_title.setdefault(_normalize_title(sp.name), []).append(sp)
    for ep in rss_episodes:
        candidates = by_title.get(_normalize_title(ep.title), [])
        if len(candidates) == 1 and candidates[0].id not in used:
            result[ep.source_item_id] = candidates[0].id
            used.add(candidates[0].id)

    # 第二輪：日期相近 + 標題相似
    for ep in rss_episodes:
        if ep.source_item_id in result:
            continue
        rss_date = ep.published_at.date()
        rss_title = _normalize_title(ep.title)
        candidates = [
            sp
            for sp in spotify_episodes
            if sp.id not in used
            and sp.release_date is not None
            and abs(sp.release_date - rss_date) <= _DATE_TOLERANCE
            and _similarity(rss_title, _normalize_title(sp.name)) >= _TITLE_SIMILARITY_THRESHOLD
        ]
        if len(candidates) == 1:
            result[ep.source_item_id] = candidates[0].id
            used.add(candidates[0].id)

    return result


def _normalize_title(title: str) -> str:
    """把兩邊標題常見的無意義差異抹平：大小寫、全形半形、彎引號與直引號、標點、多餘空白。"""
    text = unicodedata.normalize("NFKC", title).casefold()
    text = re.sub(r"[^\w\s]", " ", text)
    return " ".join(text.split())


def _similarity(a: str, b: str) -> float:
    return SequenceMatcher(None, a, b).ratio()
