"""RSS ↔ Spotify 集數比對的試跑版：指定月份，每集列出比對到的 Spotify episode id，不寫資料庫。

目的是在接進 pipeline 之前，先用真實資料確認比對規則準不準——比不到的有幾集、有沒有配錯。
配錯的代價是拿別集的逐字稿寫筆記，所以正式上線前要人工看過一次這份報告。

只需要 .env 裡的 PN_SPOTIFY_CLIENT_ID / PN_SPOTIFY_CLIENT_SECRET，不需要 DB 設定。

用法：
    uv run python scripts/spotify_match_dry_run.py 2026-09   # → out/spotify-match-2026-09.md

節目的 Spotify show id 從 sql/004_seed_tracked_sources.sql 解析（這支腳本不連資料庫）。
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from fetch_dry_run import OUT_DIR, SEED_SQL, load_sources, month_range  # noqa: E402

from podcast_notes.config import get_spotify_settings  # noqa: E402
from podcast_notes.sources.podcast import fetch_feed, filter_by_published  # noqa: E402
from podcast_notes.sources.spotify_catalog import SpotifyCatalog, match_episodes  # noqa: E402

# 對應 seed 檔 update 段裡的 ('RSS 網址', 'Spotify show id')
_SHOW_ID_ROW = re.compile(r"\(\s*'([^']+)',\s*'([0-9A-Za-z]{22})'\s*\)")


def load_show_ids() -> dict[str, str]:
    """從 seed SQL 讀出 {feed 網址: Spotify show id}。"""
    return dict(_SHOW_ID_ROW.findall(SEED_SQL.read_text(encoding="utf-8")))


def _cell(text: str) -> str:
    """標題裡的 | 會被 markdown 當成表格欄位分隔（20VC、a16z 的標題常有），要跳脫。"""
    return text.replace("|", "\\|")


def main() -> None:
    if len(sys.argv) != 2:
        sys.exit("用法：uv run python scripts/spotify_match_dry_run.py 2026-09")
    month = sys.argv[1]
    since, until = month_range(month)

    settings = get_spotify_settings()

    show_ids = load_show_ids()
    lines = [f"# RSS ↔ Spotify 比對：{month}", ""]
    total = matched = 0

    with SpotifyCatalog(
        settings.spotify_client_id,
        settings.spotify_client_secret,
        market=settings.spotify_market,
    ) as catalog:
        for name, feed_url in load_sources():
            rss = filter_by_published(fetch_feed(feed_url).episodes, since=since, until=until)
            show_id = show_ids.get(feed_url)
            if not rss or not show_id:
                continue
            spotify = catalog.list_show_episodes(show_id)
            by_id = {sp.id: sp for sp in spotify}
            result = match_episodes(rss, spotify)

            total += len(rss)
            matched += len(result)
            print(f"{name}：{len(result)}/{len(rss)} 集比對成功（Spotify 上共 {len(spotify)} 集）")

            lines += [f"## {name}（{len(result)}/{len(rss)}）", ""]
            lines += ["| RSS 日期 | RSS 標題 | Spotify 日期 | Spotify 標題 |", "|---|---|---|---|"]
            for ep in rss:
                sp = by_id.get(result.get(ep.source_item_id, ""))
                sp_cols = f"{sp.release_date} | {_cell(sp.name)}" if sp else "— | **比不到**"
                lines.append(f"| {ep.published_at.date()} | {_cell(ep.title)} | {sp_cols} |")
            lines.append("")

    OUT_DIR.mkdir(exist_ok=True)
    out = OUT_DIR / f"spotify-match-{month}.md"
    out.write_text("\n".join(lines), encoding="utf-8")
    print(f"\n合計 {matched}/{total} 集比對成功，報告：{out}")


if __name__ == "__main__":
    main()
