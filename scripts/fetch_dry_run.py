"""抓取任務的試跑版（dry run）：把指定月份、所有追蹤節目的集數抓下來寫成報告，不寫資料庫。

目的是先單獨確認「RSS 解析這件事對不對」——抓得到嗎、時間對嗎、音檔網址有嗎、
有沒有逐字稿——確認完再接資料庫寫入。兩個問題分開驗，出錯時才知道是誰的錯。

結果寫成 markdown 檔（預設 out/<月份>.md），終端機只印各節目幾集的摘要。

用法：
    uv run python scripts/fetch_dry_run.py                   # 本月 → out/<本月>.md
    uv run python scripts/fetch_dry_run.py 2026-09           # → out/2026-09.md
    uv run python scripts/fetch_dry_run.py 2026-09 某路徑.md  # 自己指定檔名

節目清單直接從 sql/004_seed_tracked_sources.sql 解析，不另外維護一份，
避免兩邊不同步（這支腳本不連資料庫，所以不能從 tracked_sources 讀）。
"""

from __future__ import annotations

import re
import sys
from datetime import UTC, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from podcast_notes.sources.podcast import (  # noqa: E402
    FEED_TIMEOUT,
    fetch_feed,
    filter_by_published,
)

SEED_SQL = Path(__file__).resolve().parent.parent / "sql" / "004_seed_tracked_sources.sql"
TAIPEI = ZoneInfo("Asia/Taipei")
# 結果檔的預設位置。這是隨時可重跑重生的產物，不進版控。
OUT_DIR = Path(__file__).resolve().parent.parent / "out"

# 對應 seed 檔裡每一筆 ('podcast', '節目名', '主持人', 'RSS 網址')。
# (?:[^']|'')* 是為了吃掉 SQL 裡用兩個單引號跳脫的撇號，例如 'O''Shaughnessy'。
_ROW = re.compile(
    r"\(\s*'([^']*)',\s*'((?:[^']|'')*)',\s*'((?:[^']|'')*)',\s*'([^']*)'\s*\)", re.S
)


def load_sources() -> list[tuple[str, str]]:
    """從 seed SQL 讀出 (節目名稱, feed 網址)。"""
    sql = SEED_SQL.read_text(encoding="utf-8")
    return [(name.replace("''", "'"), url) for _type, name, _host, url in _ROW.findall(sql)]


def month_range(month: str) -> tuple[datetime, datetime]:
    """把 '2026-09' 轉成 [當月 1 號, 下個月 1 號) 的 UTC 區間。"""
    year, mon = (int(x) for x in month.split("-"))
    since = datetime(year, mon, 1, tzinfo=UTC)
    until = datetime(year + (mon == 12), mon % 12 + 1, 1, tzinfo=UTC)
    return since, until


def main() -> int:
    month = sys.argv[1] if len(sys.argv) > 1 else datetime.now(UTC).strftime("%Y-%m")
    out_path = Path(sys.argv[2]) if len(sys.argv) > 2 else OUT_DIR / f"{month}.md"
    since, until = month_range(month)
    sources = load_sources()

    lines: list[str] = [
        f"# Podcast 集數預覽 {month}",
        "",
        f"- 抓取區間：UTC {since:%Y-%m-%d} 起，不含 {until:%Y-%m-%d}",
        f"- 產生時間：{datetime.now(TAIPEI):%Y-%m-%d %H:%M} 台北",
        f"- 節目數：{len(sources)}",
        "",
    ]

    total = 0
    failures: list[tuple[str, str]] = []

    with httpx.Client(timeout=FEED_TIMEOUT, follow_redirects=True) as client:
        for name, feed_url in sources:
            try:
                result = fetch_feed(feed_url, client=client)
            except Exception as exc:  # 單一節目失敗不該中斷整批
                failures.append((name, f"{type(exc).__name__}: {exc}"))
                lines += [f"## {name}", "", f"**抓取失敗**：{type(exc).__name__}: {exc}", ""]
                print(f"{name}：抓取失敗（{type(exc).__name__}）")
                continue

            picked = filter_by_published(result.episodes, since=since, until=until)
            total += len(picked)

            lines += [
                f"## {name}",
                "",
                f"{month} 有 **{len(picked)}** 集（feed 全部 {len(result.episodes)} 集）",
                "",
            ]
            if result.permanent_redirect_to:
                lines += [f"> 永久轉址，feed_url 應更新為：{result.permanent_redirect_to}", ""]
            for ep in picked:
                local = ep.published_at.astimezone(TAIPEI)
                lines += [
                    f"### {local:%Y-%m-%d %H:%M}（台北）{ep.title}",
                    "",
                    f"- guid：`{ep.source_item_id}`",
                    f"- 音檔：{ep.audio_url or '（無）'}",
                    f"- 逐字稿：{ep.transcript_url or '（RSS 沒帶）'}",
                    "",
                ]
            print(f"{name}：{len(picked)} 集")

    lines += ["---", "", f"合計 {month} 共 **{total}** 集"]
    if failures:
        lines += ["", f"有 {len(failures)} 個節目抓取失敗："]
        lines += [f"- {name}：{err}" for name, err in failures]
    lines.append("")

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text("\n".join(lines), encoding="utf-8")

    print(f"\n合計 {total} 集，結果已寫入：{out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
