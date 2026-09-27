"""測試共用的常數。放這裡而不是 conftest.py：pytest 不建議直接 import conftest。"""

SHOW_A = "A" * 22  # Show A 的 Spotify show id（走 Spotify 的節目）
FEED_A = "https://example.com/a.xml"
FEED_B = "https://example.com/b.xml"  # Show B：沒有 Spotify id 的節目
