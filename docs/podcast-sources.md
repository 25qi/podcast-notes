# Podcast 來源總覽

最後更新：2026-09-27

這 10 個節目各是什麼狀況、每一集的內容要從哪裡取得。下面一張表就是全部結論。

每一集的原始資料（標題、guid、音檔網址）由 `scripts/fetch_dry_run.py` 產生在 `out/<月份>.md`，
那是可重跑重生的資料檔，不進版控。

## 總表

集數與時長為 2026-09。來源欄位：✅ 拿得到、⚠️ 有但拿不到、❌ 沒有。

| 節目 | 集數 | 時長 | 平均 | RSS | 官網 | 第三方站 | **Spotify** | 決定 | 說明 |
|---|---|---|---|---|---|---|---|---|---|
| a16z Podcast | 22 | 16.9h | 46 分 | ❌ | ❌ | ❌ | ✅ 8,454 字 | **Spotify** | 官網只有 show notes |
| 20VC | 9 | 10.9h | 73 分 | ❌ | ❌ | ❌ | ✅ 11,327 字 | **Spotify** | 官網只導向 Spotify |
| Invest Like the Best | 4 | 4.6h | 69 分 | ❌ | ⚠️ | ✅ | ✅ 14,327 字 | **Spotify** | colossus.com 要帳號；happyscribe 也有完整版 |
| No Priors | 3 | 2.0h | 40 分 | ❌ | ❌ | ❌ | ✅ 7,067 字 | **Spotify** | 官網擋爬蟲 |
| Dwarkesh | 3 | 5.3h | 106 分 | ❌ | ✅ | ❌ | ✅ 16,485 字 | **官網**（或 Spotify） | 官網靜態 HTML 就有，不用登入，最省事 |
| Lenny's Podcast | 3 | 4.3h | 86 分 | ❌ | ⚠️ | ⚠️ | ❌ 沒有按鈕 | **音檔** | Substack 付費牆；Spotify 也沒逐字稿 |
| Newcomer | 2 | 1.8h | 54 分 | ⚠️ | ❌ | ❌ | ✅ 11,418 字 | **Spotify** | RSS 曾有，2026-06 後停了 |
| Dialectic | 2 | 2.4h | 72 分 | ❌ | ❌ | ❌ | ✅ 5,188 字 | **Spotify** | 官網是 Notion，瀏覽器也抓不到 |
| BG2 Pod | 0 | — | — | ❌ | ❌ | ❌ | ✅ 15,058 字 | **Spotify** | RSS 停在 2026-06-11，目前沒新集數 |
| Lightcone | 0 | — | — | ❌ | ✅ | ❌ | ❌ 沒有按鈕 | （沒東西可抓） | YC library 可行，但 RSS 停在 2025-12-03 |
| **合計** | **48** | **48.2h** | **60 分** | | | | **8/10 有** | | |

## 結論

> **2026-09-27 定案：10 個節目全部走 Spotify 逐字稿，拿不到才退回音檔**（寫入 `implementation-spec.md`「內容取得層」）。
> 依據是第五輪（見文末）九月整月實抓 57/57 集全部拿到，包括 Lenny's——它在第四輪時沒有 Transcript 按鈕，現在有了；
> Dwarkesh 也改走 Spotify，讓程式只有一條路徑。之後新增的節目到時候再個別確認。
> 下表與下方「第四輪時的結論」保留當時的狀態作為記錄。

**第四輪時的結論**（已被上方定案取代）：

**主力走 Spotify 逐字稿（45 集），Lenny's 走音檔（3 集），Dwarkesh 走官網。**

九月 48 集裡有 45 集拿得到逐字稿，只有 Lenny's 的 3 集要餵音檔。

token 差距很大：逐字稿平均每集約 11,000 字（約 1.5 萬 tokens），音檔每集約 11.6 萬 tokens。
全部走音檔要 560 萬，改走逐字稿約 70 萬，**省下約 88%**。

Dwarkesh 建議仍走官網而不是 Spotify，因為官網是靜態 HTML，不用登入、不用瀏覽器、不依賴非公開 API，
是所有路徑裡最穩的。

## Spotify 逐字稿怎麼拿（已驗證可行）

Spotify 幾乎替所有節目自動產生逐字稿。未登入完全拿不到（token 端點直接回 400
`Usage of this endpoint is not permitted`），登入後可以。

實際做法（2026-09-24 實測成功）：

1. Playwright 用**持久化的瀏覽器設定檔**，手動登入 Spotify 一次，之後登入狀態就存在那個設定檔裡
2. 開 `https://open.spotify.com/episode/<episode_id>`
3. **不要自己去要 token**（無頭模式自己 fetch `/api/token` 會被擋，回 401），讓頁面自己點 Transcript 按鈕
4. 攔截頁面發出的這個請求的回應：
   ```
   https://spclient.wg.spotify.com/transcript-read-along/v2/episode/<id>
       ?format=json&maxSentenceLength=500&excludeCC=true
   ```
5. 回傳 JSON 結構是 `{"language":"en-us","section":[{"text":{"sentence":{"text":"..."}}}, ...]}`，
   把每個 sentence 的 text 串起來就是完整逐字稿

**無頭模式可行**，驗證過。所以回填時不用一直開著視窗。

### RSS 集數怎麼對應到 Spotify episode id（2026-09-27 已解決）

實作在 `src/podcast_notes/sources/spotify_catalog.py`：用官方 Web API 的 `/shows/{id}/episodes`
列出集數，先比「正規化後標題完全相同」，比不到再比「日期差一天內 + 標題相似度 ≥ 0.8，且只有一個候選」。
九月 57 集全部在第一輪（標題完全相同）配對成功。以下是當時的問題記錄：

**怎麼把 RSS 的集數對應到 Spotify 的 episode id。** 這次測試是用「搜尋節目 → 取最新一集」，
pipeline 需要的是「這一集的 guid → 對應的 Spotify episode id」。兩個方向：

- Spotify 官方 Web API（需申請 client credentials，免費、合法）的 `/shows/{id}/episodes` 端點，
  回傳每集的 id、名稱、發布日期，用標題或日期比對
- 或在網頁上搜尋集數標題

另外要注意：搜尋分頁的網址是 `/search/<q>/podcastAndEpisodes`，不是 `/shows`（這次踩過坑）。

### 風險

依賴登入狀態和非公開 API，Spotify 改版就會壞。程式要留降級路徑：拿不到逐字稿就退回餵音檔。
登入 cookie 也會過期，需要偶爾手動重登一次。

## 音檔路線（備援，Lenny's 用）

每一項限制都遠遠夠用：單次 prompt 音訊上限 9.5 小時，我們最長一集 1.8 小時；Files API 單檔上限
2 GB，我們最大約 150 MB；專案總量上限 20 GB，九月全部約 2.3 GB；檔案存 48 小時自動刪；免費。

音訊換算 token 是 32 tokens/秒，平均每集 116K、最長一集 203K。

免費額度要到 https://aistudio.google.com/rate-limit 查自己帳號的 RPD 與 TPM。

## 為什麼 RSS 幾乎都沒有逐字稿

Spotify 的自動逐字稿**預設只在 Spotify App 內顯示**。要讓它出現在 RSS（`podcast:transcript` 標籤），
節目主必須到 Spotify for Creators 手動打開「Distribute transcripts across other platforms via RSS」，
這是 opt-in，幾乎沒人打開。

所以「RSS 沒有逐字稿」不代表節目沒逐字稿。Newcomer 是唯一開過的（RSS 網址是
`transcript-files.spotifycdn.com`），2026-06 後也停了。

實作注意：feedparser **不支援** Podcasting 2.0 命名空間，不會解析 `podcast:transcript`。
`src/podcast_notes/sources/podcast.py` 是自己用 ElementTree 撈的，別改回去。

## 第三方逐字稿站

| 站 | 結果 |
|---|---|
| podcasts.happyscribe.com | 只有 Invest Like the Best（27 筆）與 Lenny（12 筆）有真結果，其餘 8 個搜尋頁明說沒結果。ILTB 實測 22,399 字完整逐字稿 |
| podscripts.co | 擋爬蟲，`ERR_EMPTY_RESPONSE` |
| spoken.md | 搜尋不到我們的節目 |

Spotify 那條路打通之後，第三方站只剩一個用途：Lenny's 在 happyscribe 有 12 筆結果，
那是它唯一可能的逐字稿來源（Spotify 沒有、官網付費牆）。內容還沒驗證。

---

# 附錄：探勘過程與證據

保留下來是為了之後判斷「某個節目的狀況是不是變了」時有對照基準。

## 第一輪（2026-09-23）：RSS 的 link 指向哪裡

原本的假設是這條通用路徑：每集 RSS 都有 `<link>` 指向該集網頁 → 抓那一頁 → trafilatura 抽正文 →
得到逐字稿。這條路如果成立，新增節目就是零成本。

方法：每節目取最新 1 集，讀 `entry.link`，httpx 抓頁（跟隨轉址、標明 User-Agent、限速），trafilatura
抽正文，計算字數與字數÷音檔分鐘數（正常說話約每分鐘 150 字）。

結果：**10 個節目只有 Dwarkesh 成立。** 四種失敗模式：

1. **假陽性**（BG2、Lightcone、Newcomer）：Spotify creators 頁字數高達 8,080 / 2,953 / 7,559，
   實際讀內容是「該集簡介 + 其他幾十集的簡介列表」。**字數和字/分比會被騙**，要看講者標記行數才準
2. **RSS 沒有 link**（No Priors、Invest Like the Best）：Megaphone 的 entry 只有 enclosure
3. **要 JavaScript**（Dialectic、a16z、20VC）：抽出來 0～14 字
4. **付費牆**（Lenny's）：只有 504 字節目簡介

## 第二輪（2026-09-24）：各節目真正的官網

第一輪只測 RSS 給的 link，這輪改測真正官網。結論：官網這條路也大致不通，障礙不是「沒有逐字稿」，
是幾乎每個官網都要 JavaScript。

Colossus 值得記一筆：單集頁 trafilatura 只抽到 301 字，但頁面 CSS 裡有
`--color-transcript-speaker-1` 一路到 `speaker-6`，代表網站設計上就是要顯示多講者逐字稿，
東西存在只是靠 JS 載入。

## 第三輪（2026-09-24）：用 Playwright 開真瀏覽器

前提：爬蟲不必跑在 Oracle 主機上，回填是一次性工作，可在本機 Mac 跑，所以「要跑瀏覽器」不再是否決理由。

| 節目 | 瀏覽器實測 |
|---|---|
| Lightcone | YC library 點開 Transcript，4,446 字、71 個講者行，真逐字稿 |
| Invest Like the Best | 點開後出現「TRANSCRIPT CONTENTS / Patrick: My guest today is...」但全頁只有 373 字，且有 log in / subscribe / member |
| a16z | 580 字，show notes |
| Dialectic | 等 8 秒、捲動 5 次，只有 22 字 |
| BG2 Pod | 官網連結只有 /cart、/terms，沒有單集頁 |
| No Priors | 瀏覽器一樣 ERR_CONNECTION_RESET |

踩到的坑：Dialectic 用 `wait_until="networkidle"` 會 45 秒超時，因為 Notion 的連線永遠不會閒置，
要改用 `domcontentloaded`。

## 第四輪（2026-09-24）：Spotify 逐字稿

前三輪的結論是「爬蟲只能覆蓋 3/48 集」。第四輪推翻了它。

**未登入**：20VC 單集頁整頁 1,861 字全是 Sign up / Log in，連 transcript 這個字都沒出現，
頁面沒發出任何逐字稿請求；取匿名 token 的端點回 400 `Usage of this endpoint is not permitted
under the Spotify Developer Terms and Developer Policy, and applicable law`。

**登入後**（Veky 在可見瀏覽器手動登入一次，登入狀態存進持久化設定檔）：點開 Transcript，
攔截到 `spclient.wg.spotify.com/transcript-read-along/v2/episode/...` 回 200，完整逐字稿 JSON。
20VC 那集 799 句、11,327 字（73 分鐘節目換算 155 字/分，合理）。

**無頭模式同樣有效**，但不能自己去要 token（會被擋，401），要讓頁面自己呼叫、只攔截回應。

10 個節目覆蓋率實測：

| 節目 | 句數 | 字數 |
|---|---|---|
| Dwarkesh | 833 | 16,485 |
| BG2 Pod | 956 | 15,058 |
| Invest Like the Best | 725 | 14,327 |
| Newcomer | 604 | 11,418 |
| 20VC | 799 | 11,327 |
| a16z Podcast | 495 | 8,454 |
| No Priors | 359 | 7,067 |
| Dialectic | 323 | 5,188 |
| Lenny's Podcast | — | 沒有 Transcript 按鈕 |
| Lightcone | — | 沒有 Transcript 按鈕 |

踩到的坑：Spotify 的 podcast 搜尋分頁是 `/search/<q>/podcastAndEpisodes`，不是 `/shows`。
用錯網址會拿到「搜尋不到節目」，我第一版就是這樣全部誤判成沒有。

## 第五輪（2026-09-27）：九月整月實抓

用正式程式（`scripts/spotify_transcript_dry_run.py`）把九月 57 集全部跑一次：

- **比對**：57/57 集對應到 Spotify episode id，全部是標題完全相同配上的
- **逐字稿**：57/57 集拿到，0 集沒有、0 集失敗，連續抓完沒被擋、登入也沒失效
- **Lenny's 現在有逐字稿了**（3 集，15,837～18,696 字）。第四輪時沒有 Transcript 按鈕，推測是 Spotify 後來補產生的
- 字數合理性抽查：最短的 a16z 09-07 只有 1,522 字，但該集本來就只有 8 分鐘（約 188 字/分）

實測到的新細節：

- Transcript 是集數頁的分頁（`#transcript-tab`，role=tab），不是按鈕；它和 `#description-tab` 同時出現，
  所以「Description 出現但 Transcript 沒出現」就代表這集沒有逐字稿，不用等逾時
- 未登入時頁面有 `[data-testid="login-button"]`、沒有任何分頁，用它判斷登入失效
- 逐字稿 JSON 的 `title` 區塊不只是講者（`Speaker 1`），也可能是 Spotify 自動切的章節標題，
  所以輸出時標題獨立一行，不寫成「標題: 內文」
- Spotify Web API 的集數清單裡真的會出現 `null`（官方文件說不會），要跳過
- a16z 在 API 上顯示剛好 1000 集、最舊到 2016 年，可能是上限；回填只需 2025 年以後，不受影響
- 2026-02 起 Spotify 開發者 app 的擁有者必須有 Premium

## 還沒查的

- ~~每節目只取了最新 1～2 集~~ → 九月整月已驗證（第五輪）；2025 年的舊集數還沒驗證
- ~~怎麼把 RSS 的 guid 對應到 Spotify 的 episode id~~ → 已解決（第五輪）
- BG2 Pod、Lightcone 九月沒有新集數，沒被第五輪涵蓋
- ~~Lenny's 唯一的可能來源：happyscribe~~ → Spotify 現在有了，happyscribe 不用再查
- Spotify 登入 cookie 多久過期
- spec 待定事項裡的其他來源：獨立 /transcript 子頁、PDF、YouTube 字幕
