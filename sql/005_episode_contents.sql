-- episode_contents：每一集要拿什麼內容去寫摘要（逐字稿全文，或改用音檔）。
--
-- 由「抓內容」任務寫入、「摘要」任務讀取（spec「排程／執行層」任務 3、4）。
-- 為什麼另開一張表、不放進 episode_status（2026-09-27 Veky 決定）：
-- episode_status 只回答「這一集現在該怎麼處理」，每輪都要查它；
-- 逐字稿一集 1～3.5 萬字，放進去會讓每次查狀態都拖著大欄位，也混淆了那張表的用途。
-- 逐字稿先存起來的好處：Gemini 失敗重試時不用重開瀏覽器再抓一次。

create table episode_contents (
    id bigint generated always as identity primary key,

    -- 對應哪一集。跟 notes 一樣用 (source_type, source_item_id) 對應 episode_status。
    source_type text not null,
    source_item_id text not null,

    -- 內容來源：
    --   spotify_transcript  Spotify 逐字稿，全文在 transcript 欄位
    --   audio               拿不到逐字稿（沒比對到 Spotify 集數、或那集沒有逐字稿），
    --                       摘要改餵音檔，音檔網址在 episode_status.source_url
    -- 記下 audio 也是一筆資料：代表「已經試過、確定要用音檔」，摘要任務不用再猜。
    content_source text not null
        check (content_source in ('spotify_transcript', 'audio')),

    -- 逐字稿全文。只有 spotify_transcript 才有值，下面的 CHECK 保證兩者一致。
    transcript text,

    -- 逐字稿語言（Spotify 回傳，例如 en-us）與字數，方便事後檢查品質（字數對不上時長就可疑）。
    transcript_language text,
    transcript_word_count int,

    fetched_at timestamptz not null default now(),

    -- 一集只有一筆內容。
    constraint uq_episode_contents_source unique (source_type, source_item_id),

    -- 是逐字稿就一定要有全文，是音檔就不能有全文，避免資料自相矛盾。
    constraint ck_episode_contents_transcript
        check ((content_source = 'spotify_transcript') = (transcript is not null)),

    constraint fk_episode_contents_episode_status
        foreign key (source_type, source_item_id)
        references episode_status (source_type, source_item_id)
);
