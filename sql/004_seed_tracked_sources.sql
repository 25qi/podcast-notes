-- 追蹤清單的初始資料：規格書的 10 個候選節目。
-- feed_url 來源：Apple iTunes Search API 查出各節目登記的 RSS 網址，
-- 2026-09-21 實際下載每個 feed 核對過節目名稱、作者與最新一集。
--
-- on conflict do nothing：重複執行這個檔案不會報錯，也不會插入重複資料。

insert into tracked_sources (source_type, name, host_name, feed_url) values
    ('podcast', '20VC', 'Harry Stebbings',
        'https://rss.libsyn.com/shows/61840/destinations/240976.xml'),
    ('podcast', 'a16z Podcast', 'a16z 編輯團隊',
        'https://feeds.simplecast.com/JGE3yC0V'),
    ('podcast', 'No Priors', 'Sarah Guo、Elad Gil',
        'https://feeds.megaphone.fm/nopriors'),
    ('podcast', 'Invest Like the Best', 'Patrick O''Shaughnessy',
        'https://feeds.megaphone.fm/CLS2859450455'),
    ('podcast', 'Dwarkesh', 'Dwarkesh Patel',
        'https://apple.dwarkesh-podcast.workers.dev/feed.rss'),
    ('podcast', 'BG2 Pod', 'Bill Gurley、Brad Gerstner',
        'https://anchor.fm/s/f06c2370/podcast/rss'),
    ('podcast', 'Lenny''s Podcast', 'Lenny Rachitsky',
        'https://api.substack.com/feed/podcast/10845.rss'),
    ('podcast', 'Lightcone', 'Y Combinator 合夥人',
        'https://anchor.fm/s/f58d3330/podcast/rss'),
    ('podcast', 'Newcomer', 'Eric Newcomer',
        'https://anchor.fm/s/ffef15f0/podcast/rss'),
    ('podcast', 'Dialectic', 'Jackson Dahl',
        'https://feeds.megaphone.fm/ICDEI1431648742')
on conflict (feed_url) do nothing;
