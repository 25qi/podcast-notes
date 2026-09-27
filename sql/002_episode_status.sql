-- episode_status：追蹤每一集的處理進度，不存放筆記內容本身。
--
-- 設計原則：這張表只回答一個問題，「這一集現在該怎麼處理」，
-- 所以每個欄位存在的理由，都是「抓取任務查詢時，判斷下一步要不要處理它」需要用到的資訊。

create table episode_status (
    id bigint generated always as identity primary key,

    -- 這一集屬於追蹤清單（tracked_sources）裡的哪個節目或頻道。
    source_id bigint not null,

    -- 來源類型，加 CHECK 擋錯字（例如 "Youtube" 大寫），
    -- 不用 Postgres ENUM 是因為以後要加新值時，text + CHECK 改起來比 ENUM 有彈性、不會鎖表。
    source_type text not null
        check (source_type in ('youtube', 'podcast')),

    -- 該來源自己的集數識別碼：YouTube video ID / podcast episode GUID
    source_item_id text not null,

    show_name text not null,
    episode_title text not null,
    published_at timestamptz not null,

    -- 原始來源的網址。抓取階段就要存下來，
    -- 因為 podcast 的音檔網址無法從 GUID 推導，摘要任務需要直接讀這裡，
    -- 不然就得在摘要階段回頭重抓一次 RSS。
    source_url text not null,

    -- 這一集在 Spotify 上的 episode id，抓 Spotify 逐字稿時要用。
    -- 可空：節目不走 Spotify、或還沒比對到（比對失敗時留空，退回用音檔）。
    -- 比對成功就存下來，重試時不用再呼叫一次 Spotify API 重新比對。
    spotify_episode_id text
        check (spotify_episode_id ~ '^[0-9A-Za-z]{22}$'),

    status text not null default 'pending'
        check (status in ('pending', 'processing', 'completed', 'failed')),

    -- 這一集被標成 processing 的時間點。
    -- 抓取任務除了找 pending，也要找「processing 但這個時間超過某個時限」的資料，
    -- 重置回 pending，避免 Airflow 中途掛掉（主機被回收 / RDS 被關 / 程式 crash）
    -- 導致這筆資料永遠卡在 processing、再也不會被任何查詢撿到。
    processing_started_at timestamptz,

    -- 重試次數。超過上限（目前訂 3 次，寫在應用程式邏輯，不寫死在這裡）
    -- 就不再自動重試，等人工確認、手動重置。
    attempt_count int not null default 0,

    last_error text,

    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),

    constraint uq_episode_status_source unique (source_type, source_item_id),

    -- 兩集 RSS 不該對應到同一個 Spotify 集數；真的發生代表比對邏輯配錯了，
    -- 讓資料庫直接擋下來，而不是默默把同一份逐字稿拿去寫兩篇筆記。
    constraint uq_episode_status_spotify_episode_id unique (spotify_episode_id),

    -- 複合外鍵：每一集必須屬於追蹤清單裡真實存在的節目，
    -- 而且這裡的 source_type 必須跟該節目登記的 source_type 一致
    -- （例如不能有一集標成 youtube，卻掛在 podcast 節目底下）。
    constraint fk_episode_status_source
        foreign key (source_id, source_type)
        references tracked_sources (id, source_type)
);

-- 抓取任務的核心查詢是「找出該處理的集數」，這條複合索引讓那次查詢直接命中，
-- 不用整張表掃過一遍。
create index idx_episode_status_status_processing_started_at
    on episode_status (status, processing_started_at);
