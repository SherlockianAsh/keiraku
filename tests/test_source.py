"""A source is a directory with a descriptor. Nothing implicit. (SPEC §1)"""

import json

import pytest

import keiraku


def test_init_source_writes_descriptor(tmp_path):
    d = tmp_path / "src"
    keiraku.init_source(d)

    desc = json.loads((d / "keiraku.json").read_text())
    assert desc["keiraku"] == keiraku.SPEC_VERSION
    assert "created_at" in desc


def test_init_source_creates_subdirs(source):
    assert (source / "presence").is_dir()
    assert (source / "stream").is_dir()


def test_init_source_is_idempotent(tmp_path):
    d = tmp_path / "src"
    keiraku.init_source(d)
    created = json.loads((d / "keiraku.json").read_text())["created_at"]

    keiraku.init_source(d)  # must not clobber
    assert json.loads((d / "keiraku.json").read_text())["created_at"] == created


def test_attach_refuses_a_directory_that_is_not_a_source(empty_dir):
    """SPEC §1.1 — refusing beats silently creating a source in the wrong place."""
    with pytest.raises(keiraku.NotASource):
        keiraku.attach(empty_dir, host="python", session_id="s1", cwd="/tmp")


def test_attach_refuses_a_missing_directory(tmp_path):
    with pytest.raises(keiraku.NotASource):
        keiraku.attach(tmp_path / "nope", host="python", session_id="s1", cwd="/tmp")


def test_attach_succeeds_on_a_real_source(source):
    v = keiraku.attach(source, host="python", session_id="s1", cwd="/tmp")
    assert v.vessel_id
