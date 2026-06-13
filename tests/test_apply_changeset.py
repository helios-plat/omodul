"""Tests for apply_changeset.compute_fingerprint_for."""
from __future__ import annotations

import pytest

from omodul.apply_changeset import (
    ChangesetConfig,
    ChangesetInput,
    compute_fingerprint_for,
)


@pytest.fixture()
def config():
    return ChangesetConfig()


@pytest.fixture()
def empty_input():
    return ChangesetInput(edits=[], message="test")


def test_compute_fingerprint_returns_64_hex(config, empty_input):
    fp = compute_fingerprint_for(config, empty_input)
    assert len(fp) == 64
    assert all(c in "0123456789abcdef" for c in fp)


def test_same_input_same_fingerprint(config, empty_input):
    fp1 = compute_fingerprint_for(config, empty_input)
    fp2 = compute_fingerprint_for(config, empty_input)
    assert fp1 == fp2


def test_different_input_different_fingerprint(config):
    i1 = ChangesetInput(edits=[], message="a")
    i2 = ChangesetInput(edits=[], message="b")
    assert compute_fingerprint_for(config, i1) != compute_fingerprint_for(config, i2)
