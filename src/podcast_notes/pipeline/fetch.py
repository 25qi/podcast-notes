"""抓取任務：把追蹤節目的新集數寫進 episode_status，再撿出這一輪要處理的集數。

對應 spec「排程／執行層」的任務 2。分兩段，可以分開呼叫、分開測：

1. sync_sources：每個啟用中的節目 → 抓 RSS → 新集數寫入（已存在的不動）
   → 還沒比對到 Spotify 的集數，用官方 Web API 比對出 spotify_episode_id
2. claim_episodes：撿出要處理的集數，標成 processing

交易切法：每個節目各自一個交易。某個節目 RSS 掛了或 Spotify API 出錯，
只有那個節目這輪沒更新，不會把其他節目已經寫好的資料一起復原。
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Protocol

import psycopg
from psycopg.rows import DictRow

from podcast_notes.sources.podcast import Episode, FeedResult, fetch_feed
from podcast_notes.sources.spotify_catalog import (
    SpotifyEpisode,
    SpotifyRateLimited,
    match_episodes,
)

log = logging.getLogger(__name__)

# 卡在 processing 超過這個時間就當作上一輪中途掛掉，重新撿起來（spec 暫定 2 小時）
STUCK_TIMEOUT = timedelta(hours=2)

# 嘗試次數上限（spec）。達到就不再自動撿，等人工確認後手動重置。
MAX_ATTEMPTS = 3

# 新集數剛發布時，Spotify 可能還沒上架、或還沒產生逐字稿（2026-09-27 Veky 決定等 24 小時）。
# 走 Spotify 的節目，發布未滿這個時間、又還沒比對到 Spotify 集數的，先不撿，
# 下一輪重新比對；超過時間還是比不到，才放行讓後面的步驟退回用音檔。
SPOTIFY_GRACE_PERIOD = timedelta(hours=24)


class ShowCatalog(Protocol):
    """sync_sources 需要的 Spotify 功能。正式跑用 SpotifyCatalog，測試可以換成假的。"""

    def list_show_episodes(
        self, show_id: str, *, stop_before: date | None = None
    ) -> list[SpotifyEpisode]: ...


@dataclass(frozen=True)
class Source:
    id: int
    name: str
    feed_url: str
    spotify_show_id: str | None


@dataclass
class SyncReport:
    """一輪同步的結果，給呼叫端記 log 或判斷要不要讓任務失敗。"""

    inserted: dict[str, int] = field(default_factory=dict)
    """每個節目新寫入幾集"""
    matched: dict[str, int] = field(default_factory=dict)
    """每個節目這輪新比對到幾集 Spotify"""
    errors: dict[str, str] = field(default_factory=dict)
    """同步失敗的節目與錯誤訊息。這些節目這輪沒更新，其他節目不受影響。"""


def load_active_sources(conn: psycopg.Connection[DictRow]) -> list[Source]:
    rows = conn.execute(
        """
        select id, name, feed_url, spotify_show_id
        from tracked_sources
        where is_active and source_type = 'podcast'
        order by id
        """
    ).fetchall()
    return [Source(**row) for row in rows]


def sync_sources(
    conn: psycopg.Connection[DictRow],
    *,
    catalog: ShowCatalog | None,
    since: datetime,
    feed_fetcher: Callable[[str], FeedResult] = fetch_feed,
) -> SyncReport:
    """所有啟用中的 podcast 節目：寫入新集數、比對 Spotify。

    since：只寫入這個時間之後發布的集數（回填起點，PN_BACKFILL_SINCE）。
    catalog：傳 None 就跳過 Spotify 比對（例如沒設定 Spotify 憑證時）。
    feed_fetcher：抓 RSS 的函式，測試時換成假的，不用真的連網。
    """
    report = SyncReport()
    sources = load_active_sources(conn)
    # 先結束「讀節目清單」那個自動開啟的交易。不然下面的 conn.transaction() 會變成它裡面的
    # savepoint，離開時不會真的 commit，全部要等最外層才一起存（psycopg 官方文件的行為），
    # 中途掛掉的話前面節目的資料就白寫了。
    conn.commit()
    rate_limited = False
    for source in sources:
        # RSS 寫入和 Spotify 比對分成兩個交易：
        # 比對失敗（例如 Spotify 限流）不該把已經寫好的新集數也復原
        try:
            with conn.transaction():
                report.inserted[source.name] = _sync_feed(conn, source, since, feed_fetcher)
        except Exception as e:  # noqa: BLE001  一個節目失敗不該拖垮其他節目
            log.exception("節目 %s 同步 RSS 失敗", source.name)
            report.errors[source.name] = f"{type(e).__name__}: {e}"
            continue

        if catalog is None or not source.spotify_show_id or rate_limited:
            continue
        try:
            with conn.transaction():
                report.matched[source.name] = _match_spotify(conn, source, catalog)
        except SpotifyRateLimited as e:
            # 被要求等很久：後面的節目也一定會被擋，這一輪就不再比對了。
            # 沒比對到的集數 spotify_checked_at 維持 null，
            # claim 不會把它們誤當成「Spotify 上沒有」。
            log.warning("Spotify 限流，這一輪停止比對：%s", e)
            report.errors[source.name] = f"Spotify 比對：{e}"
            rate_limited = True
        except Exception as e:  # noqa: BLE001
            log.exception("節目 %s 比對 Spotify 失敗", source.name)
            report.errors[source.name] = f"Spotify 比對：{type(e).__name__}: {e}"
    return report


def _sync_feed(
    conn: psycopg.Connection[DictRow],
    source: Source,
    since: datetime,
    feed_fetcher: Callable[[str], FeedResult],
) -> int:
    feed = feed_fetcher(source.feed_url)
    if feed.permanent_redirect_to:
        _update_feed_url(conn, source, feed.permanent_redirect_to)

    inserted = 0
    for ep in feed.episodes:
        if ep.published_at < since:
            continue
        if not ep.audio_url:
            # source_url 不能空（摘要退回音檔時要用），沒音檔的集數代表 feed 本身有問題，記下來跳過
            log.warning("%s 的集數沒有音檔網址，跳過：%s", source.name, ep.title)
            continue
        cur = conn.execute(
            """
            insert into episode_status
                (source_id, source_type, source_item_id, show_name, episode_title,
                 published_at, source_url)
            values (%s, 'podcast', %s, %s, %s, %s, %s)
            on conflict (source_type, source_item_id) do nothing
            """,
            (source.id, ep.source_item_id, source.name, ep.title, ep.published_at, ep.audio_url),
        )
        inserted += cur.rowcount
    return inserted


def _update_feed_url(conn: psycopg.Connection[DictRow], source: Source, new_url: str) -> None:
    """RSS 永久轉址：把新網址寫回 feed_url（spec「追蹤清單」）。

    新網址已被別的節目占用時（違反唯一約束）不硬寫，只記錄下來讓人判斷。
    用 savepoint（巢狀 transaction）包住：寫回失敗只復原這一句，不影響同一個交易裡的集數寫入。
    """
    try:
        with conn.transaction():
            conn.execute(
                "update tracked_sources set feed_url = %s where id = %s", (new_url, source.id)
            )
        log.info("%s 的 RSS 永久轉址，feed_url 已更新為 %s", source.name, new_url)
    except psycopg.errors.UniqueViolation:
        log.error("%s 的 RSS 轉址到 %s，但該網址已被其他節目使用，未寫回", source.name, new_url)


def _match_spotify(conn: psycopg.Connection[DictRow], source: Source, catalog: ShowCatalog) -> int:
    """這個節目還沒比對到 Spotify 的 pending 集數，拿去跟 Spotify 集數清單比對，比到就寫回。

    每輪都會重試還沒比對到的集數（新集數在 Spotify 上架可能比 RSS 晚），
    只看 pending：已經在處理或處理完的集數，比對結果已經用不到了。
    """
    rows = conn.execute(
        """
        select source_item_id, episode_title, published_at
        from episode_status
        where source_id = %s and status = 'pending' and spotify_episode_id is null
        """,
        (source.id,),
    ).fetchall()
    if not rows:
        return 0  # 都比對過了，就不用打 Spotify API

    # 只需要翻到比「最舊的待比對集數」再舊一點的地方（多留兩天緩衝，兩邊日期可能差一天）
    oldest = min(r["published_at"] for r in rows).date() - timedelta(days=2)

    rss = [
        Episode(
            source_item_id=r["source_item_id"],
            title=r["episode_title"],
            published_at=r["published_at"],
            audio_url=None,
            transcript_url=None,
        )
        for r in rows
    ]
    matches = match_episodes(
        rss, catalog.list_show_episodes(source.spotify_show_id, stop_before=oldest)
    )
    # 這批集數都「真的比對過」了（不管有沒有比到），記下時間。claim 靠它判斷能不能改用音檔。
    conn.execute(
        """
        update episode_status
        set spotify_checked_at = now(), updated_at = now()
        where source_id = %s and status = 'pending' and spotify_episode_id is null
        """,
        (source.id,),
    )

    written = 0
    for guid, spotify_id in matches.items():
        try:
            with conn.transaction():
                conn.execute(
                    """
                    update episode_status
                    set spotify_episode_id = %s, updated_at = now()
                    where source_type = 'podcast' and source_item_id = %s
                    """,
                    (spotify_id, guid),
                )
            written += 1
        except psycopg.errors.UniqueViolation:
            # 這個 Spotify 集數已經配給別的 RSS 集數了，代表兩邊有一邊配錯，不硬寫，讓這集退回音檔
            log.error("%s：Spotify 集數 %s 已被其他集數使用，guid %s 不寫入", source.name,
                      spotify_id, guid)
    return written


def claim_episodes(
    conn: psycopg.Connection[DictRow], *, limit: int, newest_first: bool
) -> list[dict]:
    """撿出這一輪要處理的集數，標成 processing，回傳這些集數的完整資料。

    撿的條件（spec）：
    - pending，或卡在 processing 超過 STUCK_TIMEOUT 的（上一輪中途掛掉）
    - attempt_count 還沒達到 MAX_ATTEMPTS
    - 走 Spotify 的節目：已經比對到 Spotify 集數，或「比對過確定找不到」且發布已超過
      SPOTIFY_GRACE_PERIOD。還沒比對過的（例如被 Spotify 限流）一律不撿，不然會被誤改用音檔

    attempt_count 在撿起來的這一刻就加 1，而不是失敗時才加：
    如果程式中途整個掛掉，根本沒機會執行「失敗時加 1」，那一集就會無限重試。

    limit、newest_first 沒有預設值：回填怎麼分批（每輪幾集、先新還是先舊）是 spec 的待定事項，
    由呼叫端（之後的 DAG）明確指定，這裡不偷偷決定。

    for update skip locked：萬一兩輪同時在跑，同一集不會被兩邊都撿走。
    """
    order = "desc" if newest_first else "asc"
    rows = conn.execute(
        f"""
        with picked as (
            select e.id
            from episode_status e
            join tracked_sources t on t.id = e.source_id
            where e.attempt_count < %(max_attempts)s
              and (
                  e.status = 'pending'
                  or (e.status = 'processing'
                      and e.processing_started_at < now() - %(stuck)s)
              )
              and (
                  t.spotify_show_id is null
                  or e.spotify_episode_id is not null
                  or (e.spotify_checked_at is not null
                      and e.published_at < now() - %(grace)s)
              )
            order by e.published_at {order}
            limit %(limit)s
            for update of e skip locked
        )
        update episode_status s
        set status = 'processing',
            processing_started_at = now(),
            attempt_count = s.attempt_count + 1,
            updated_at = now()
        from picked
        where s.id = picked.id
        returning s.*
        """,
        {
            "max_attempts": MAX_ATTEMPTS,
            "stuck": STUCK_TIMEOUT,
            "grace": SPOTIFY_GRACE_PERIOD,
            "limit": limit,
        },
    ).fetchall()
    conn.commit()
    # update ... returning 不保證順序，照發布時間排回來
    return sorted(rows, key=lambda r: r["published_at"], reverse=newest_first)
