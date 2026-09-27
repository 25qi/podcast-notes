# AI 筆記工具技術規格書（YouTube + 國外科技類 Podcast）

## 背景與目的

這是一個 AI 自動筆記工具：追蹤指定的 YouTube 頻道和國外科技類 podcast，每次有新集數就自動抓取內容、用 AI 生成摘要，存進資料庫。同時是兩件事：自己平常想用的工具，也是求職轉職 Data Engineer 用的作品集項目。

查證過類似專案（dimthink/awesome-ai-podcast-notes、arcmira/mcp 等）已經有人在做，所以這個專案的價值不在「點子沒人做過」，在於實際使用真正的雲端資料工程元件（雲端資料庫、排程系統、狀態追蹤、去重複邏輯）——這點是查到的競品都沒有的。規格內容以這個定位為準。

## 整體架構

```
來源層 (YouTube / Podcast)，追蹤清單存於 tracked_sources 表
    ↓
排程執行層 (Airflow, 自架在 Oracle Cloud)
    ↓ 抓取 → 摘要 → 寫入（三個獨立任務）
狀態追蹤層 (episode_status 表)  ←→  輸出層 (notes 表)
    （都在同一個 AWS RDS Postgres）
    ↓
監控層 (Healthchecks.io)
```

## 資料來源層

- 兩種來源：YouTube 影片、國外科技類 podcast
- 兩者共用摘要/存檔邏輯，各自處理自己怎麼取得文字內容：
  - YouTube：直接把影片 URL 餵給 Gemini API（preview 功能），不用另外做語音轉文字
  - Podcast：優先選有現成逐字稿的節目
- 同一節目同時有 YouTube 和 Podcast 版本的去重複處理：暫緩，目前主要來源不是 YouTube，不會遇到這個情境，之後真的需要再回來處理

**Podcast 追蹤清單**（新創/VC/AI 圈公認權威等級，非大眾熱門節目；詳細定位說明和來源網址見 `insider-authority-podcast-list.md`）：下列 10 個候選節目已全部寫進 `tracked_sources` 表（RSS 網址見 `sql/004_seed_tracked_sources.sql`），預設全部啟用。之後要調整追蹤範圍，改 `is_active` 即可，不用刪資料。

| 節目 | 主持人 |
|---|---|
| 20VC | Harry Stebbings |
| a16z Podcast | a16z 編輯團隊 |
| No Priors | Sarah Guo、Elad Gil |
| Invest Like the Best | Patrick O'Shaughnessy |
| Dwarkesh | Dwarkesh Patel |
| BG2 Pod | Bill Gurley、Brad Gerstner |
| Lenny's Podcast | Lenny Rachitsky |
| Lightcone | Y Combinator 合夥人 |
| Newcomer | Eric Newcomer |
| Dialectic | Jackson Dahl |

2026-09-21 實際下載各節目 RSS 核對的結果：

- 10 個 RSS 都能正常讀取、都有音檔連結
- Lightcone 最新一集是 2025-12-03、BG2 Pod 是 2026-06-11，RSS 上看起來久未更新（原因未確認）
- 只有 Newcomer 的 RSS 帶逐字稿標籤，其餘 9 個沒有（RSS 沒標籤不代表節目沒有逐字稿，可能放在官網）

## 追蹤清單

**`tracked_sources` 表**（定案，DDL 見 `sql/001_tracked_sources.sql`）：

| 欄位 | 型別 | 說明 |
|---|---|---|
| id | bigint identity | 主鍵 |
| source_type | text | `youtube` 或 `podcast`，CHECK 限制 |
| name | text | 節目或頻道名稱 |
| host_name | text（可空） | 主持人，純顯示用 |
| feed_url | text | 抓取任務用來找新集數的網址（podcast 為 RSS 網址），不可重複 |
| spotify_show_id | text（可空） | Spotify 節目 id，用來比對 RSS 集數與 Spotify 集數；22 碼英數 CHECK、不可重複。只記事實，要不要走 Spotify 逐字稿另外決定 |
| is_active | boolean | 是否啟用追蹤，預設 true；抓取任務只處理 true 的 |
| created_at | timestamptz | |

- 唯一約束：`(feed_url)`、`(spotify_show_id)`、`(id, source_type)`（最後一個供 `episode_status` 的複合外鍵使用）
- **feed_url 永久轉址處理（定案）**：10 個節目的 RSS 網址分屬 6 種託管平台（Libsyn、Simplecast、Megaphone、Anchor、Substack、Dwarkesh 自架的 Cloudflare Worker），網址由平台決定，節目換平台就會變。所以抓取時要允許並跟隨 redirect；若拿到永久轉址（HTTP 301 / 308），把轉址後的網址寫回 `feed_url`，避免之後每次抓都多跳一次，也避免舊網址哪天停止轉址就再也抓不到。暫時性轉址（302 / 307）只跟隨，不寫回。寫回時若新網址已被別的節目占用（違反 `(feed_url)` 唯一約束），不要硬寫，記錄下來讓人工判斷。

## 狀態追蹤層

用獨立的狀態表記錄每一集的處理進度，跟最終筆記內容的表分開——這樣摘要生成失敗時（例如 Gemini API 逾時、逐字稿抓取失敗）能清楚知道是「還沒處理」還是「處理過但失敗」，方便重跑，不會污染正式輸出內容。

**`episode_status` 表**（定案，DDL 見 `sql/002_episode_status.sql`）：

| 欄位 | 型別 | 說明 |
|---|---|---|
| id | bigint identity | 主鍵 |
| source_id | bigint | 這一集屬於 `tracked_sources` 的哪個節目或頻道 |
| source_type | text | `youtube` 或 `podcast`，CHECK 限制 |
| source_item_id | text | 該來源自己的集數識別碼（YouTube video ID / podcast episode GUID） |
| show_name | text | 節目名稱 |
| episode_title | text | 集數標題 |
| published_at | timestamptz | 原始發布時間 |
| source_url | text | 原始連結（podcast 為音檔網址），抓取階段就存，摘要任務直接讀 |
| spotify_episode_id | text（可空） | 比對到的 Spotify 集數 id，比對失敗留空（退回音檔）；22 碼英數 CHECK、不可重複 |
| status | text | `pending` / `processing` / `completed` / `failed`，預設 `pending`，CHECK 限制 |
| processing_started_at | timestamptz（可空） | 標成 `processing` 的時間，用來回收卡死的集數 |
| attempt_count | int | 重試次數，預設 0 |
| last_error | text（可空） | 最近一次失敗原因 |
| created_at | timestamptz | |
| updated_at | timestamptz | |

- 唯一約束：`(source_type, source_item_id)`、`(spotify_episode_id)`（後者防止比對配錯，兩集 RSS 對到同一個 Spotify 集數）
- 外鍵：`(source_id, source_type)` 對應 `tracked_sources(id, source_type)`，確保每一集屬於清單裡真實存在的節目，且類型一致
- 卡住回收：`processing` 超過時限（暫定 2 小時）的集數，重置回 `pending`，避免 Airflow 中途掛掉時永遠卡住
- 重試上限：`attempt_count` 達 3 次就不再自動撿，等人工重置

## 輸出層

同一個 AWS RDS Postgres，新增 `notes` 表存放處理完成的筆記內容。最終呈現形式（Obsidian 同步、網頁、或之後考慮的 MCP 查詢介面）不在這次規格範圍內，先只寫進資料庫。

**`notes` 表**（定案，DDL 見 `sql/003_notes.sql`）：

| 欄位 | 型別 | 說明 |
|---|---|---|
| id | bigint identity | 主鍵 |
| source_type | text | `youtube` 或 `podcast` |
| source_item_id | text | 同 episode_status，用來關聯 |
| show_name | text | |
| episode_title | text | |
| published_at | timestamptz | |
| source_url | text | 原始連結 |
| summary | text | Gemini 生成的摘要內容 |
| key_points | jsonb | 重點條列，CHECK 限制必須是陣列 |
| model_name | text | 產生這篇摘要的模型名稱（設定用的是會自動指向新版的別名，記下來才能追查品質變化） |
| created_at | timestamptz | |

- 唯一約束：`(source_type, source_item_id)`
- 外鍵：`(source_type, source_item_id)` 對應 `episode_status`，筆記必須對得上真實存在的集數，且已有筆記的集數不能被刪除

## 排程／執行層

自架 Apache Airflow，架在 Oracle Cloud Always Free 的免費主機上（不用 Amazon MWAA，因為 MWAA Serverless 不支援 PythonOperator/BashOperator，跑不了自訂邏輯；也不用 GitHub Actions 排程，因為 GitHub 官方文件本身標註 schedule trigger 是 best-effort，不保證準時觸發）。部署用 Docker Compose（Airflow 官方主流做法），但只包這一層；`src/podcast_notes/` 的業務邏輯本身不依賴 Docker，可在任何裝了 Python + uv 的機器上直接跑。

**DAG 任務切法：抓取、摘要、寫入切成三個獨立任務**（不合併成一個任務），原因：初期回填階段可能需要跑好幾輪抓取，切開方便針對單一步驟重跑或除錯，不用每次失敗就整條重來。

任務順序：

1. **等待 RDS 可連線**（Airflow Sensor，持續 poll 直到 RDS 啟動完成，不用 Lambda 15 分鐘執行上限的做法）
2. **抓取**：讀 `tracked_sources` 裡啟用的節目，找出新集數寫進 episode_status；再查 episode_status 有哪些集數要處理（`pending`，加上卡住超時的 `processing`，`attempt_count` 已達 3 次的不撿），撿到的標成 `processing` 並記下 `processing_started_at`
3. **摘要**：呼叫 Gemini Flash API 生成摘要與重點
4. **寫入**：寫進 `notes` 表，並把 episode_status 更新為 `completed`（失敗則更新為 `failed`，記錄 `last_error`）
5. **關閉 RDS**（不論前面成功或失敗都要執行，避免多花錢）

RDS 啟動失敗或逾時的重試/超時策略：先不決定，之後再回來處理。

## 摘要層

Google Gemini API，Flash 版本（Pro 已從免費方案下架）。不用小型專門摘要模型（上下文長度不夠放完整逐字稿），也不在 Oracle 主機上自架本地模型（運算資源不足、會跟 Airflow 本身搶資源，且 Gemini Flash 免費額度已經夠用，自架沒有省到錢）。

## 歷史回填

從 2025/1/1 開始回填，跟每次新集數處理一起跑，避免單次跑量爆掉。具體怎麼分批（用固定筆數、用日期區間、由新到舊或由舊到新）還沒定案，先不寫死，見文件最後的待定事項。

## 監控層

Healthchecks.io 的 dead-man's-switch 模式：pipeline 跑完就 ping 一個指定網址，沒收到 ping 才發信通知。這一個機制統一涵蓋所有失敗情境（Oracle 主機被閒置回收、RDS 啟動逾時、pipeline 邏輯出錯），因為它們最終都會變成「該收到的 ping 沒收到」。免費方案：20 個 check，不用信用卡。

## 成本安全機制

- AWS 帳號目前是新帳號的免費試用制，試用期 6 個月，不是舊帳號那種 12 個月、750 小時的免費層，要留意這個時間窗口
- AWS Free Tier 用量達 85% 時發告警
- 設定一個接近 0 美元門檻的 AWS Budget 告警
- pipeline 程式碼層級：不論成功或失敗都要執行「關閉 RDS」的步驟
- AWS Budget Actions 作為最後一道防線，超出預算門檻時自動關閉 RDS 執行個體

## 產品分層

- 開源：podcast/YouTube 整合 + 筆記生成引擎（本規格涵蓋的部分）
- 私有：把筆記自動轉發到 IG/Threads 帳號的發文層，憑證和發文邏輯分開管理，不放進開源專案

## 機密憑證管理

用 Bitwarden（免費方案 + 開源用戶端）統一存放 AWS key、Gemini API key、DB 密碼、SSH 金鑰，靠內建的跨裝置同步在 Mac 和 Windows 之間共用。

## 目前明確待定、不要自行決定的事項

- 同一節目 YouTube/Podcast 雙版本的去重複處理
- 回填的具體分批方式（固定筆數／日期區間／由新到舊或由舊到新）
- RDS 啟動失敗/逾時的重試與超時策略
- Podcast 內容怎麼取得（2026-09-23 更新）：實測 10 個節目的 RSS，只有 Newcomer 帶 `podcast:transcript` 標籤，且它最新一集有逐字稿的是 2026-05-29，6 月之後也沒有了——等於現在沒有任何節目能直接從 RSS 拿到逐字稿。方向定為「爬官網逐字稿為主、餵音檔給 Gemini 為備援」，但具體做法待探勘結果出來再定。
- 逐字稿不在集數頁的情況（先記錄，之後再處理）：通用抽取假設「逐字稿在 RSS `<link>` 指向的那一頁」，但實際上可能放在別處——獨立的 /transcript 子頁、PDF、Apple Podcasts 或 Spotify 站內、第三方逐字稿服務、電子報的另一篇文章、或只有 YouTube 字幕。探勘時順便記錄遇到幾種，再決定值不值得處理。
- YouTube 頻道怎麼追蹤新影片（`tracked_sources.feed_url` 對 YouTube 要放什麼）
- `episode_status.updated_at` 怎麼維持正確（目前只有新增時會自動填，之後更新不會自動變，要用 trigger 或由程式每次更新時設定）

## 實作協作原則

Claude Code 實作時，每個決定點（尤其是上面列為「提案」或「待定」的部分）要跟 Veky 解釋清楚在做什麼、為什麼這樣做，確認後才繼續，不要自己悶著頭跑完整個實作。這個專案的重點是 Veky 自己要能完全講得出來、用得上，不只是換一個能動的成品。
