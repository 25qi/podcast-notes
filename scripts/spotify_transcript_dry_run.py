"""Spotify 逐字稿的試跑版：指定月份，RSS → 比對 Spotify id → 抓逐字稿，存成檔案，不寫資料庫。

目的是在接進 pipeline 之前，用真實資料確認三件事：
- 覆蓋率：每個節目是不是每一集都有逐字稿（探勘時每節目只測了 1～2 集）
- 穩定性：連續抓幾十集會不會被擋、會不會中途登入失效
- 品質：逐字稿內容是不是完整（字數對照節目時長）

需要 .env 的 PN_SPOTIFY_*，以及已登入的瀏覽器設定檔（scripts/spotify_login.py）。

用法：
    uv run --extra browser python scripts/spotify_transcript_dry_run.py 2026-09
    → 逐字稿：out/transcripts-2026-09/<節目>/<日期>-<episode id>.txt
    → 報告：  out/transcripts-2026-09.md
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from fetch_dry_run import OUT_DIR, load_sources, month_range  # noqa: E402
from spotify_match_dry_run import load_show_ids  # noqa: E402

from podcast_notes.config import get_spotify_settings  # noqa: E402
from podcast_notes.sources.podcast import fetch_feed, filter_by_published  # noqa: E402
from podcast_notes.sources.spotify_catalog import SpotifyCatalog, match_episodes  # noqa: E402
from podcast_notes.sources.spotify_transcript import (  # noqa: E402
    SpotifyLoginRequired,
    SpotifyTranscriptFetcher,
)


def _slug(name: str) -> str:
    return re.sub(r"[^\w]+", "-", name).strip("-").lower()


def main() -> None:
    if len(sys.argv) != 2:
        sys.exit("用法：spotify_transcript_dry_run.py 2026-09（見檔案開頭說明）")
    month = sys.argv[1]
    since, until = month_range(month)
    settings = get_spotify_settings()
    show_ids = load_show_ids()
    out_dir = OUT_DIR / f"transcripts-{month}"

    lines = [f"# Spotify 逐字稿試跑：{month}", ""]
    lines += ["| 節目 | 日期 | 標題 | 結果 | 句數 | 字數 |", "|---|---|---|---|---|---|"]
    counts = {"ok": 0, "none": 0, "unmatched": 0, "error": 0}

    with (
        SpotifyCatalog(
            settings.spotify_client_id, settings.spotify_client_secret,
            market=settings.spotify_market,
        ) as catalog,
        SpotifyTranscriptFetcher(settings.spotify_profile_dir) as fetcher,
    ):
        for name, feed_url in load_sources():
            rss = filter_by_published(fetch_feed(feed_url).episodes, since=since, until=until)
            if not rss or feed_url not in show_ids:
                continue
            matched = match_episodes(rss, catalog.list_show_episodes(show_ids[feed_url]))

            for ep in rss:
                title = ep.title.replace("|", "\\|")
                row = f"| {name} | {ep.published_at.date()} | {title[:60]} |"
                episode_id = matched.get(ep.source_item_id)
                if episode_id is None:
                    counts["unmatched"] += 1
                    lines.append(f"{row} 比對不到 Spotify 集數 | | |")
                    continue
                try:
                    transcript = fetcher.fetch(episode_id)
                except SpotifyLoginRequired:
                    raise  # 登入失效後面每集都會失敗，直接停
                except Exception as e:  # noqa: BLE001  試跑要看到每一集的結果，不因一集失敗就停
                    counts["error"] += 1
                    lines.append(f"{row} ❌ {type(e).__name__}: {str(e)[:80]} | | |")
                    print(f"  ❌ {name} {ep.published_at.date()}：{e}")
                    continue
                if transcript is None:
                    counts["none"] += 1
                    lines.append(f"{row} 沒有逐字稿 | | |")
                    continue

                counts["ok"] += 1
                path = out_dir / _slug(name) / f"{ep.published_at.date()}-{episode_id}.txt"
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(f"# {ep.title}\n\n{transcript.text}\n", encoding="utf-8")
                lines.append(
                    f"{row} ✅ | {transcript.sentence_count} | {transcript.word_count:,} |"
                )
                print(f"  ✅ {name} {ep.published_at.date()}：{transcript.word_count:,} 字")

    summary = (
        f"成功 {counts['ok']}、沒有逐字稿 {counts['none']}、"
        f"比對不到 {counts['unmatched']}、失敗 {counts['error']}"
    )
    lines[1:1] = ["", summary]
    report = OUT_DIR / f"transcripts-{month}.md"
    report.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"\n{summary}\n報告：{report}\n逐字稿：{out_dir}")


if __name__ == "__main__":
    main()
