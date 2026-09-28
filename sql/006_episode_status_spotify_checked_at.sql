-- episode_status 加 spotify_checked_at：這一集最後一次「真的完成」Spotify 比對的時間。
--
-- 為什麼需要（2026-09-28 第一次回填時發現）：
-- 原本撿集數的規則是「走 Spotify 的節目，還沒比對到的集數，發布滿 24 小時就放行、改用音檔」。
-- 但如果比對根本沒跑成（Spotify 限流、API 掛掉），回填的舊集數發布早就超過 24 小時，
-- 會被誤當成「Spotify 上沒有」而全部改走音檔——音檔的 token 是逐字稿的 8 倍，而且其實拿得到逐字稿。
-- 有了這個欄位，就能分清楚「比對過、確定找不到」和「還沒比對過」：只有前者才放行改用音檔。
--
-- 可空：還沒比對過就是 null。
-- 這是資料庫開始保存正式資料後的第一個變更，所以開新檔案，不改 002（見 scripts/init_db.py）。

alter table episode_status add column spotify_checked_at timestamptz;

-- 已經比對到的集數一定是比對過的，補上時間（用最後修改時間近似）
update episode_status
set spotify_checked_at = updated_at
where spotify_episode_id is not null;
