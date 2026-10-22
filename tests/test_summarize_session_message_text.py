"""Regression tests for extracting readable text from session messages."""

from __future__ import annotations

from omodul.summarize_session import _msg_text


def test_message_text_accepts_plain_string_content():
    assert _msg_text({"content": "completed the API migration"}) == "completed the API migration"


def test_message_text_extracts_text_segments_from_multimodal_content():
    message = {
        "content": [
            {"type": "text", "text": "first decision"},
            {"type": "image", "source": {"type": "base64", "data": "ignored"}},
            {"type": "text", "text": "second decision"},
        ]
    }
    assert _msg_text(message) == "first decision second decision"


def test_message_text_handles_empty_and_missing_content():
    assert _msg_text({"content": []}) == ""
    assert _msg_text({}) == ""
    assert _msg_text({"content": None}) == ""
