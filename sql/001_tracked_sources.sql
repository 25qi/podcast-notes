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

    -- 是否啟用追蹤。「候選池」和「最終追蹤清單」不用刪資料來區分，
    -- 把某個節目設成 false 即可，抓取任務只處理 true 的。
    is_active boolean not null default true,

    created_at timestamptz not null default now(),

    constraint uq_tracked_sources_feed_url unique (feed_url),

    -- id 本來就不會重複，這條看起來多餘，但 Postgres 規定：
    -- 外鍵要指向「有唯一約束的欄位組合」。episode_status 用 (source_id, source_type)
    -- 這組合來對應這裡，藉此保證每一集記的 source_type 跟它所屬節目的 source_type 一致。
    constraint uq_tracked_sources_id_type unique (id, source_type)
);
