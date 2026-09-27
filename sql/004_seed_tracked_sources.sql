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

-- 各節目在 Spotify 上的 show id。
-- 來源：網路搜尋 open.spotify.com/show/ 的候選，2026-09-27 逐一核對——
-- 每個候選的 Spotify 最新一集標題，要跟該節目 RSS 最新一集完全相同才採用。
-- 有同名的其他節目被排除：20VC 的 UNEDITED 版、a16z Live / 16 Minutes News、Newcomer News。
-- 用 update 而不是寫進上面的 insert：上面那段的欄位格式有腳本（scripts/fetch_dry_run.py）在解析，
-- 分開寫就不用動它；update 本身重複執行結果也一樣。
update tracked_sources as t
set spotify_show_id = v.spotify_show_id
from (values
    ('https://rss.libsyn.com/shows/61840/destinations/240976.xml', '3j2KMcZTtgTNBKwtZBMHvl'),
    ('https://feeds.simplecast.com/JGE3yC0V', '5bC65RDvs3oxnLyqqvkUYX'),
    ('https://feeds.megaphone.fm/nopriors', '0O65xhqvGVhpgdIrrdlEYk'),
    ('https://feeds.megaphone.fm/CLS2859450455', '22fi0RqfoBACCuQDv97wFO'),
    ('https://apple.dwarkesh-podcast.workers.dev/feed.rss', '4JH4tybY1zX6e5hjCwU6gF'),
    ('https://anchor.fm/s/f06c2370/podcast/rss', '3dVqgYXN29DwwnqdY3YsCk'),
    ('https://api.substack.com/feed/podcast/10845.rss', '2dR1MUZEHCOnz1LVfNac0j'),
    ('https://anchor.fm/s/f58d3330/podcast/rss', '0hCbmBt7bJVqtvg3aNUsgt'),
    ('https://anchor.fm/s/ffef15f0/podcast/rss', '34NxWrRHHAU5uWG5RfnZIP'),
    ('https://feeds.megaphone.fm/ICDEI1431648742', '2IEN4eE9HvNKJHnLv5EMG9')
) as v (feed_url, spotify_show_id)
where t.feed_url = v.feed_url;
