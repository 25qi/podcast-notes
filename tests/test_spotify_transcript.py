"""測試逐字稿 JSON 的解析。不開瀏覽器、不連網；JSON 結構照 2026-09-27 實際抓到的回應。"""

from podcast_notes.sources.spotify_transcript import parse_transcript


def _sentence(text: str) -> dict:
    return {"startMs": 0, "text": {"sentence": {"text": text}}}


def _title(name: str | None) -> dict:
    return {"startMs": 0, "title": {"title": name} if name else {}}


def test_groups_sentences_by_title() -> None:
    payload = {
        "language": "en-us",
        "section": [
            _title("Speaker 1"),
            _sentence("Welcome to the show."),
            _sentence("Today we talk AI."),
            _title("Speaker 2"),
            _sentence("Thanks for having me."),
        ],
    }
    t = parse_transcript(payload)
    assert t.text == (
        "Speaker 1\nWelcome to the show. Today we talk AI.\n\n"
        "Speaker 2\nThanks for having me."
    )
    assert t.language == "en-us"
    assert t.sentence_count == 3
    assert t.word_count == 12  # 不含標題


def test_empty_title_starts_new_paragraph_without_heading() -> None:
    """title 是空物件：換段落，但沒有新標題（不重複上一段的標題）。"""
    payload = {
        "section": [
            _title("Speaker 1"),
            _sentence("First point."),
            _title(None),
            _sentence("Second point."),
        ]
    }
    assert parse_transcript(payload).text == "Speaker 1\nFirst point.\n\nSecond point."


def test_chapter_title_is_kept_as_heading() -> None:
    """標題也可能是章節名稱（實測 a16z），一樣獨立成一行。"""
    payload = {"section": [_title("Why AI Power Concentrates"), _sentence("AI is powerful.")]}
    assert parse_transcript(payload).text == "Why AI Power Concentrates\nAI is powerful."


def test_sentences_before_any_heading_have_no_label() -> None:
    payload = {"section": [_sentence("Cold open."), _title("Speaker 1"), _sentence("Hi.")]}
    assert parse_transcript(payload).text == "Cold open.\n\nSpeaker 1\nHi."


def test_unknown_section_types_are_ignored() -> None:
    """Spotify 之後加了新的區塊類型也不會壞，只是略過。"""
    payload = {"section": [_title("Speaker 1"), {"startMs": 0, "ad": {}}, _sentence("Hi.")]}
    t = parse_transcript(payload)
    assert t.text == "Speaker 1\nHi."
    assert t.sentence_count == 1
