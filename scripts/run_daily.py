"""每天執行一次的主程式：抓取 → 抓內容 → 摘要＋寫入。

目前先在本機 Mac 執行（spec「內容取得層」），之後再換成 Airflow DAG；
三個步驟的邏輯都在 src/podcast_notes/pipeline/，這支只負責依序呼叫、記 log。

每次最多處理 PN_DAILY_EPISODE_LIMIT 集（預設 20，Gemini 免費方案一天 20 次），由新到舊，
所以每天新出的集數先處理，剩下的額度往回補舊集數。

兩種會提早停止的情況：
- Gemini 當日額度用完：剩下的集數放回去，明天再跑。這是回填期間的正常情況，結束代碼 0
- Spotify 登入失效：要人工執行 scripts/spotify_login.py 重新登入，結束代碼 1

用法（在專案根目錄執行，需要 Docker 的 Postgres 開著）：
    uv run --extra browser python scripts/run_daily.py
log 同時寫到 out/logs/<日期>.log。
"""

from __future__ import annotations

import logging
import sys
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from podcast_notes.config import get_settings, get_spotify_settings  # noqa: E402
from podcast_notes.db import connect  # noqa: E402
from podcast_notes.pipeline.fetch import claim_episodes, sync_sources  # noqa: E402
from podcast_notes.pipeline.fetch_content import fetch_contents  # noqa: E402
from podcast_notes.pipeline.summarize import summarize_episodes  # noqa: E402
from podcast_notes.sources.spotify_catalog import SpotifyCatalog  # noqa: E402
from podcast_notes.sources.spotify_transcript import (  # noqa: E402
    SpotifyLoginRequired,
    SpotifyTranscriptFetcher,
)
from podcast_notes.summarize.gemini import GeminiSummarizer, QuotaExhausted  # noqa: E402

LOG_DIR = Path(__file__).resolve().parent.parent / "out" / "logs"
log = logging.getLogger("run_daily")


def _setup_logging() -> Path:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    log_file = LOG_DIR / f"{datetime.now():%Y-%m-%d}.log"
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        handlers=[logging.StreamHandler(), logging.FileHandler(log_file, encoding="utf-8")],
    )
    # 第三方套件的 INFO 太吵（每個 HTTP 請求一行），只留警告以上
    for noisy in ("httpx", "google_genai"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    return log_file


def main() -> int:
    log_file = _setup_logging()
    settings = get_settings()
    spotify = get_spotify_settings()
    since = datetime.combine(settings.backfill_since, datetime.min.time(), tzinfo=UTC)

    log.info("=== 1/3 抓取：同步 RSS、比對 Spotify ===")
    with (
        SpotifyCatalog(
            spotify.spotify_client_id, spotify.spotify_client_secret,
            market=spotify.spotify_market,
        ) as catalog,
        connect() as conn,
    ):
        report = sync_sources(conn, catalog=catalog, since=since)
    log.info("新集數：%s", {k: v for k, v in report.inserted.items() if v})
    log.info("新比對到 Spotify：%s", {k: v for k, v in report.matched.items() if v})
    for name, error in report.errors.items():
        log.error("節目 %s 同步失敗：%s", name, error)

    with connect() as conn:
        claimed = claim_episodes(
            conn, limit=settings.daily_episode_limit, newest_first=True
        )
    log.info("這輪撿了 %d 集", len(claimed))

    try:
        log.info("=== 2/3 抓內容：Spotify 逐字稿 ===")
        with SpotifyTranscriptFetcher(spotify.spotify_profile_dir) as fetcher, connect() as conn:
            content = fetch_contents(conn, fetcher)
        log.info("逐字稿 %d、改用音檔 %d、放回 %d、失敗 %d",
                 content.transcripts, content.audio, content.released, len(content.failed))

        log.info("=== 3/3 摘要＋寫入：Gemini ===")
        summarizer = GeminiSummarizer(settings.gemini_api_key, settings.gemini_model)
        with connect() as conn:
            summary = summarize_episodes(conn, summarizer)
        log.info("完成 %d、失敗 %d", summary.completed, len(summary.failed))
    except SpotifyLoginRequired:
        log.error("Spotify 登入失效，請重新登入：scripts/spotify_login.py（用法見該檔開頭）")
        return 1
    except QuotaExhausted:
        log.warning("Gemini 今天的額度用完了，剩下的集數明天再跑（美西午夜重置）")

    with connect() as conn:
        counts = conn.execute(
            "select status, count(*) as n from episode_status group by status order by status"
        ).fetchall()
    log.info("目前進度：%s", {r["status"]: r["n"] for r in counts})
    log.info("log：%s", log_file)
    return 0


if __name__ == "__main__":
    sys.exit(main())
