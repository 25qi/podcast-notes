# Bash 指令小抄

## 檔案與資料夾

| 指令 | 意思 | 範例 |
|---|---|---|
| `ls` | 列出資料夾內容 | `ls -la`（`-l` 詳細資訊、`-a` 連隱藏檔一起列） |
| `mkdir -p 路徑` | 建資料夾，`-p` 是「中間缺的層也一起建、已存在不報錯」 | `mkdir -p src/podcast_notes/sources` |
| `touch 檔名` | 建一個空檔案；檔案已存在則只更新修改時間 | `touch src/podcast_notes/__init__.py` |
| `rm 檔名` | 刪除檔案 | `rm demo.txt` |
| `rm -rf 資料夾` | 強制刪除資料夾及裡面所有內容，**沒有垃圾桶、刪了就是刪了，下手前多想一次** | `rm -rf /tmp/flat_demo` |
| `cp 來源 目的地` | 複製檔案 | `cp .env.example .env` |
| `cd 路徑` | 切換所在資料夾 | `cd /tmp` |

## 看檔案內容

| 指令 | 意思 | 範例 |
|---|---|---|
| `cat 檔名` | 印出整個檔案內容 | `cat .gitignore` |
| `cat > 檔名 <<'EOF' ... EOF` | 把多行文字寫進檔案（heredoc），單引號 `'EOF'` 確保內容不被 shell 亂改 | 寫 `.gitignore` 用過 |
| `wc -l 檔名` | 算檔案有幾行（word count 的 `-l` = 只算行數） | `wc -l uv.lock` → `2461` |
| `head -N 檔名` | 只看檔案前 N 行 | `head -20 uv.lock` |
| `grep "關鍵字" 檔名` | 在檔案裡搜尋含有這個關鍵字的行 | `grep "backfill_since" src/podcast_notes/config.py` |
| `grep -A N` | 找到符合的行後，多印出後面 N 行 | `grep -A 6 'name = "psycopg"' uv.lock` |
| `grep -c "關鍵字"` | 不印內容，只算符合的行數 | `grep -c '\[\[package\]\]' uv.lock` |

## 找檔案

| 指令 | 意思 | 範例 |
|---|---|---|
| `find 路徑 -name "樣式"` | 依檔名找檔案 | `find . -name "__init__.py"` |
| `find 路徑 -not -path "排除的路徑/*"` | 找檔案時排除某些資料夾（例如 `.venv/`） | `find . -not -path "./.venv/*"` |

## Python / uv

| 指令 | 意思 | 範例 |
|---|---|---|
| `python3 -c "程式碼"` | 不開檔案，直接執行一段程式碼 | `python3 -c "print(1+1)"` |
| `command -v 工具名` | 檢查電腦上有沒有裝這個指令 | `command -v docker` |
| `uv python pin 版本` | 鎖定這個專案要用的 Python 版本 | `uv python pin 3.12` |
| `uv sync` | 照 `pyproject.toml` 安裝所有依賴，建好虛擬環境 | `uv sync --extra dev` |
| `uv run 指令` | 自動切到專案虛擬環境，再執行這個指令 | `uv run pytest` |
| `uv add 套件名` | 加一個新套件（正式場合用這個，比手動改 `pyproject.toml` 方便） | `uv add requests` |
| `uv --version` | 看 uv 本身的版本 | |

## Docker

| 指令 | 意思 | 範例 |
|---|---|---|
| `docker images` | 列出已下載的 image（安裝包） | |
| `docker run -d --rm --name 名字 -e KEY=VALUE -p 主機埠:容器埠 image名` | 啟動一個 container；`-d` 背景執行、`--rm` 停止時自動刪除、`-e` 傳環境變數、`-p` 對應連接埠 | `docker run -d --rm --name pn-check -e POSTGRES_PASSWORD=test -p 55432:5432 postgres:16-alpine` |
| `docker exec 容器名 指令` | 在已啟動的 container 裡面執行一個指令 | `docker exec pn-check pg_isready -U postgres` |
| `docker exec -i 容器名 psql -U postgres` | 進到 container 裡跑 psql（`-i` 讓它能接收從外面貼進去的一大段 SQL） | 驗證 `sql/002_episode_status.sql` 時用過 |
| `docker stop 容器名` | 停止 container（因為有 `--rm`，停止時會自動整個刪掉） | `docker stop pn-check` |
| `docker ps -a` | 列出所有 container（含已停止的），用來確認有沒有清乾淨 | |

## Git

| 指令 | 意思 | 範例 |
|---|---|---|
| `git init -q` | 在目前資料夾初始化一個新的版本控制（`-q` 安靜模式） | |
| `git status --short --ignored` | 看目前有哪些檔案是新的、哪些被 `.gitignore` 擋住 | `!!` 開頭代表被擋、`??` 代表新檔案還沒加入版控 |

## 其他

| 指令 | 意思 | 範例 |
|---|---|---|
| `echo "文字"` | 印出一段文字，也常用來確認變數值 | `echo $HOME` |
| `for c in A B C; do ... done` | 迴圈，把 A、B、C 輪流代入變數執行 | 一次檢查好幾個工具有沒有裝過 |
| `&&` | 前一個指令成功才執行下一個 | `mkdir -p x && git init` |
| `2>&1` | 把錯誤訊息也導向跟正常輸出同一個地方，方便一起看 | |
| `> /dev/null` | 把輸出丟進「黑洞」，不想看到訊息時用 | |


