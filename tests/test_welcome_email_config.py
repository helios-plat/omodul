"""Regression tests for welcome-email fingerprint identity fields."""

from __future__ import annotations

from omodul._fingerprint import compute_fingerprint
from omodul.send_welcome_email import WelcomeEmailConfig, WelcomeEmailInput


def test_welcome_email_fingerprint_uses_existing_user_hash_field():
    config = WelcomeEmailConfig(
        user_id_hash="user-hash-a",
        to_address="user@example.com",
        template_id="welcome_v1",
    )
    assert config._fingerprint_fields == {"user_id_hash", "template_id"}
    fingerprint = compute_fingerprint(config, WelcomeEmailInput())
    assert len(fingerprint) == 64


def test_welcome_email_fingerprint_changes_with_user_identity_hash():
    first = WelcomeEmailConfig(
        user_id_hash="user-hash-a",
        to_address="user@example.com",
        template_id="welcome_v1",
    )
    second = WelcomeEmailConfig(
        user_id_hash="user-hash-b",
        to_address="user@example.com",
        template_id="welcome_v1",
    )
    empty_input = WelcomeEmailInput()
    assert compute_fingerprint(first, empty_input) != compute_fingerprint(second, empty_input)
