-- notes：存放處理完成的筆記成果。
--
-- 設計原則：這張表是「輸出層」，要能獨立被讀取（之後接 Obsidian、網頁或 MCP 時，
-- 只查這一張表就拿得到完整資訊），所以保留節目名、標題等描述欄位，
-- 不強迫每次都要跟 episode_status 合併查詢。
-- 代價是這幾個欄位在兩張表各存一份，靠下面的外鍵確保兩邊對得上。

create table notes (
    id bigint generated always as identity primary key,

    -- 以下五個欄位跟 episode_status 對應同一集，值由摘要任務從 episode_status 帶過來。
    -- source_type 不用再寫一次 CHECK：外鍵已經規定它必須存在於 episode_status，
    -- 而 episode_status 那邊已經用 CHECK 限制過只能是 youtube / podcast。
    source_type text not null,
    source_item_id text not null,
    show_name text not null,
    episode_title text not null,
    published_at timestamptz not null,

    -- 原始連結。episode_status 那邊已經是 not null，這裡跟著不能空。
    source_url text not null,

    -- 以下是筆記內容，格式 2026-09-28 Veky 選定（試產三種風格後選「詳細結構化」）。
    -- 結構化存欄位而不是存一整篇 markdown：之後接 Obsidian、網頁或 MCP 時可以各取所需，
    -- 要組成 markdown 也很容易；反過來從一篇 markdown 拆欄位就很難。

    -- 一句話總結
    one_liner text not null,

    -- 來賓與背景（誰、什麼公司、為什麼值得聽）。只寫節目裡提到的資訊。
    -- 可空：沒有來賓的集數（例如主持人自己聊新聞）就沒有這段。
    guest_background text,

    -- 摘要（2～3 段）
    summary text not null,

    -- 重點條列，格式例如 ["重點一", "重點二"]。
    -- CHECK 規定一定要是 JSON 陣列，防止不小心寫進純文字或單一物件。
    key_points jsonb not null
        check (jsonb_typeof(key_points) = 'array'),

    -- 節目中提到的人物與公司，格式 [{"name": "...", "context": "在節目裡的角色或被提到的原因"}]
    people jsonb not null default '[]'
        check (jsonb_typeof(people) = 'array'),
    companies jsonb not null default '[]'
        check (jsonb_typeof(companies) = 'array'),

    -- 金句，格式 [{"original": "英文原文", "translation": "中文翻譯"}]
    quotes jsonb not null default '[]'
        check (jsonb_typeof(quotes) = 'array'),

    -- 值得延伸思考的問題，格式 ["問題一", "問題二"]
    follow_up_questions jsonb not null default '[]'
        check (jsonb_typeof(follow_up_questions) = 'array'),

    -- 這篇筆記是根據逐字稿還是音檔寫的（對應 episode_contents.content_source），
    -- 之後比較兩種來源的筆記品質時用得到。
    content_source text not null
        check (content_source in ('spotify_transcript', 'audio')),

    -- 這篇摘要是哪個模型產生的。設定檔用的是 gemini-flash-latest 這種會自動指向新版的別名，
    -- 記錄下來，之後發現某批摘要品質怪怪的才查得出是哪個模型做的。
    model_name text not null,

    created_at timestamptz not null default now(),

    -- 一集只會有一篇筆記。
    constraint uq_notes_source unique (source_type, source_item_id),

    -- 外鍵：每篇筆記必須對得上 episode_status 裡真實存在的一集，
    -- 也反過來讓「還有筆記的集數」無法被刪掉（預設行為是拒絕刪除）。
    constraint fk_notes_episode_status
        foreign key (source_type, source_item_id)
        references episode_status (source_type, source_item_id)
);
