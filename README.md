# podcast-notes

追蹤指定 YouTube 頻道與國外科技類 Podcast，新集數出現時自動抓內容、用 Gemini 生成摘要、寫入 Postgres。

## 架構

```
來源層 (YouTube / Podcast RSS)
    ↓
排程執行層 (Apache Airflow，自架於 Oracle Cloud Always Free)
    ↓ 抓取 → 摘要 → 寫入（三個獨立 task）
狀態追蹤層 (episode_status)  ←→  輸出層 (notes)
    （同一個 AWS RDS Postgres）
    ↓
監控層 (Healthchecks.io dead-man's switch)
```

## 目錄結構

| 路徑 | 用途 |
|---|---|
| `src/podcast_notes/config.py` | 設定集中管理（環境變數 → 型別驗證） |
| `src/podcast_notes/sources/` | 各來源怎麼取得「可餵給 AI 的內容」 |
| `src/podcast_notes/summarize/` | Gemini 摘要 |
| `src/podcast_notes/pipeline/` | 抓取 / 摘要 / 寫入三段邏輯（與 Airflow 解耦，可單獨跑） |
| `dags/` | Airflow DAG 定義，只負責串接與排程 |
| `sql/` | 資料庫 schema |
| `docs/implementation-spec.md` | 完整技術規格與所有技術選型決定 |

## 開發環境

```bash
uv sync --extra dev
cp .env.example .env   # 填入實際值，實際值存在 Bitwarden
```

## 狀態

建置中。詳見 `docs/implementation-spec.md`。
