-- tracked_sources：要追蹤哪些節目或頻道（追蹤清單）。
--
-- 抓取任務每次先讀這張表（只讀 is_active = true 的），
-- 才知道要去哪些網址找新集數。清單放在資料庫而不是寫在程式碼裡，
-- 新增或停用一個節目只是改資料，不用改程式。

create table tracked_sources (
    id bigint generated always as identity primary key,

    source_type text not null
        check (source_type in ('youtube', 'podcast')),

    -- 節目或頻道名稱
    name text not null,

    -- 主持人，純顯示用，不影響任何處理邏輯，所以允許空。
    host_name text,

    -- 抓取任務用來找新集數的網址。
    -- podcast 放 RSS 網址；YouTube 要怎麼追蹤新影片規格還沒決定，這個欄位先不綁死。
    feed_url text not null,

    -- 這個節目在 Spotify 上的 show id（open.spotify.com/show/<這一段>）。
    -- 用途：靠它呼叫 Spotify 官方 Web API 列出集數，把 RSS 的每一集對應到 Spotify 的 episode id，
    -- 之後才能去抓 Spotify 逐字稿。
    -- 這裡只記錄「這個節目在 Spotify 的 id 是什麼」這個事實；
    -- 某個節目要不要走 Spotify 逐字稿（例如 Lenny's 在 Spotify 上沒有逐字稿）是另一個決定，不靠這欄表達。
    -- 可空：節目不在 Spotify 上（例如之後加入的 YouTube 頻道）。
    -- 不在執行時用搜尋找：同名節目可能不只一個（例如 20VC 另有 UNEDITED 版），
    -- 人工確認一次存下來，比每次靠搜尋猜可靠。
    -- CHECK：Spotify id 固定是 22 個英數字元，擋掉誤貼整串網址之類的錯誤。
    spotify_show_id text
        check (spotify_show_id ~ '^[0-9A-Za-z]{22}$'),

    -- 是否啟用追蹤。「候選池」和「最終追蹤清單」不用刪資料來區分，
    -- 把某個節目設成 false 即可，抓取任務只處理 true 的。
    is_active boolean not null default true,

    created_at timestamptz not null default now(),

    constraint uq_tracked_sources_feed_url unique (feed_url),

    -- 兩個節目不該對應到同一個 Spotify 節目。Postgres 的 unique 允許多個 null，
    -- 所以沒填 spotify_show_id 的節目不受影響。
    constraint uq_tracked_sources_spotify_show_id unique (spotify_show_id),

    -- id 本來就不會重複，這條看起來多餘，但 Postgres 規定：
    -- 外鍵要指向「有唯一約束的欄位組合」。episode_status 用 (source_id, source_type)
    -- 這組合來對應這裡，藉此保證每一集記的 source_type 跟它所屬節目的 source_type 一致。
    constraint uq_tracked_sources_id_type unique (id, source_type)
);
