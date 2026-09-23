"""Podcast RSS：從一個 feed 網址取得集數清單。

這一層只負責「讀 RSS，吐出結構化的集數資料」，不碰資料庫、不管要處理哪幾集，
那些是 pipeline 層的事。分開的好處是這個函式可以單獨跑、單獨測，不用有 DB。

抓取用 httpx 而不是讓 feedparser 自己抓網址，因為我們需要控制兩件 feedparser 管不到的事：
逾時，以及轉址的處理（見 fetch_feed 的說明）。
"""

from __future__ import annotations

import calendar
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import UTC, datetime

import feedparser
import httpx

# 抓一個 feed 的逾時秒數。RSS 通常幾百 KB，10 秒很夠；
# 設上限是避免某個節目的主機卡住時，整批抓取跟著卡死。
FEED_TIMEOUT = 10.0

# HTTP 永久轉址。拿到這兩種狀態碼代表「這個網址以後都改去新的地方」，
# 依規格（docs/implementation-spec.md 追蹤清單段）要把新網址寫回 tracked_sources.feed_url。
# 302 / 307 是暫時轉址，只跟隨、不寫回。
_PERMANENT_REDIRECTS = (301, 308)

# Podcasting 2.0 的命名空間。逐字稿標籤 <podcast:transcript> 屬於這裡，
# feedparser 不支援這個命名空間（實測它連解析都不解析，entry 裡完全沒有這個 key），
# 所以逐字稿要自己用 ElementTree 從原始 XML 撈，見 _transcript_urls_by_guid。
_PODCAST_NS = "https://podcastindex.org/namespace/1.0"

# 逐字稿格式的偏好順序：純文字類優先，HTML 最後。
# 理由：srt/vtt 拿掉時間軸就是乾淨的文字，最適合餵給 Gemini；HTML 還要再剝標籤。
_TRANSCRIPT_TYPE_PREFERENCE = (
    "text/vtt",
    "application/x-subrip",
    "application/srt",
    "text/plain",
    "application/json",
)


@dataclass(frozen=True)
class Episode:
    """RSS 裡的一集。欄位對應 episode_status 表，方便之後直接寫入。"""

    source_item_id: str
    """該集在這個來源的唯一識別碼，podcast 用 RSS 的 <guid>。"""

    title: str
    published_at: datetime
    """發布時間，一律轉成 UTC 的 aware datetime（帶時區資訊），不用本地時間。"""

    audio_url: str | None
    """音檔網址（RSS 的 <enclosure>）。理論上每集都該有，沒有就是這個 feed 有問題。"""

    transcript_url: str | None
    """逐字稿網址（RSS 的 <podcast:transcript>）。大部分節目沒有，所以可空。"""


@dataclass(frozen=True)
class FeedResult:
    """抓一個 feed 的結果：集數本身，加上抓取過程中發現、呼叫端需要知道的事。"""

    episodes: list[Episode]
    permanent_redirect_to: str | None
    """遇到永久轉址時的新網址，呼叫端應該寫回 feed_url；沒有轉址就是 None。"""


def fetch_feed(feed_url: str, *, client: httpx.Client | None = None) -> FeedResult:
    """抓取並解析一個 podcast RSS，回傳裡面所有集數（不過濾時間）。

    follow_redirects=True：RSS 網址是託管平台給的，節目換平台就會轉址，不跟隨會直接抓不到。
    跟隨之後再檢查轉址是不是永久的——只有整條轉址鏈都是 301/308 才算永久，
    中間只要出現一次暫時轉址（例如平台在做維護導流），最終網址就不該被當成新家寫回去。
    """
    owns_client = client is None
    client = client or httpx.Client(timeout=FEED_TIMEOUT, follow_redirects=True)
    try:
        response = client.get(feed_url)
        response.raise_for_status()
    finally:
        if owns_client:
            client.close()

    redirect_to = None
    if response.history and all(r.status_code in _PERMANENT_REDIRECTS for r in response.history):
        redirect_to = str(response.url)

    # 傳 bytes 而不是網址給 feedparser，它就只做解析、不自己連網。
    parsed = feedparser.parse(response.content)
    transcripts = _transcript_urls_by_guid(response.content)
    episodes = [
        ep for entry in parsed.entries if (ep := _to_episode(entry, transcripts)) is not None
    ]
    return FeedResult(episodes=episodes, permanent_redirect_to=redirect_to)


def _transcript_urls_by_guid(xml_bytes: bytes) -> dict[str, str]:
    """從原始 XML 撈出每一集的逐字稿網址，回傳 {guid: 逐字稿網址}。

    為什麼要自己解析：feedparser 不認得 Podcasting 2.0 命名空間，
    <podcast:transcript> 在它解析出來的 entry 裡根本不存在，不自己撈就會誤判成「這節目沒逐字稿」。

    XML 格式壞掉時回空 dict 而不是拋例外——逐字稿是加分項，
    不該因為它讓整個 feed 的集數都抓不到（feedparser 對壞格式的容忍度比 ElementTree 高）。
    """
    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError:
        return {}

    result: dict[str, str] = {}
    for item in root.iter("item"):
        guid = item.findtext("guid")
        if not guid:
            continue
        candidates = [
            (t.get("type", ""), t.get("url", ""))
            for t in item.findall(f"{{{_PODCAST_NS}}}transcript")
            if t.get("url")
        ]
        if not candidates:
            continue
        # 依偏好排序，沒在偏好清單裡的排最後
        candidates.sort(key=lambda c: _transcript_rank(c[0]))
        result[guid.strip()] = candidates[0][1]
    return result


def _transcript_rank(mime_type: str) -> int:
    try:
        return _TRANSCRIPT_TYPE_PREFERENCE.index(mime_type)
    except ValueError:
        return len(_TRANSCRIPT_TYPE_PREFERENCE)


def _to_episode(entry, transcripts: dict[str, str]) -> Episode | None:  # noqa: ANN001
    """把 feedparser 的一筆 entry 轉成 Episode。缺關鍵欄位就回 None，由呼叫端決定怎麼處理。

    缺 guid 或發布時間的集數一律跳過：沒有 guid 就無法去重複（會變成每次都當新的重抓），
    沒有發布時間就無法判斷屬不屬於要處理的區間。
    """
    guid = entry.get("id") or entry.get("guid")
    published = entry.get("published_parsed")
    if not guid or not published:
        return None

    # published_parsed 是 feedparser 已經轉成 UTC 的 struct_time，但它不帶時區資訊。
    # calendar.timegm 明確以 UTC 解讀它，再補上 UTC 時區，避免被當成本地時間。
    published_at = datetime.fromtimestamp(calendar.timegm(published), tz=UTC)

    audio_url = next(
        (
            link.get("href")
            for link in entry.get("links", [])
            if link.get("rel") == "enclosure" and link.get("href")
        ),
        None,
    )

    # 逐字稿在 fetch_feed 就先從原始 XML 撈好，這裡用 guid 對回去。
    transcript_url = transcripts.get(str(guid).strip())

    return Episode(
        source_item_id=str(guid),
        title=entry.get("title", "").strip(),
        published_at=published_at,
        audio_url=audio_url,
        transcript_url=transcript_url,
    )


def filter_by_published(
    episodes: list[Episode], *, since: datetime, until: datetime | None = None
) -> list[Episode]:
    """挑出發布時間落在 [since, until) 的集數，由新到舊排序。

    until 用「不含」的半開區間，這樣月份可以直接用下個月 1 號當界線，不用煩惱月底是 30 還 31 號。
    """
    selected = [
        ep
        for ep in episodes
        if ep.published_at >= since and (until is None or ep.published_at < until)
    ]
    return sorted(selected, key=lambda ep: ep.published_at, reverse=True)
