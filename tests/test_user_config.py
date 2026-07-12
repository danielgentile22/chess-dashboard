"""
tests/test_user_config.py
=========================
The multi-user configuration parser (issue #71 [G1]).

A well-formed config block yields one validated record per user; a malformed
record raises clearly at load rather than silently serving the wrong data;
passwords are stored and compared as hashes, never plaintext.
"""
from __future__ import annotations

import json

import pytest

from user_config import (
    UserConfigError,
    UserRecord,
    hash_password,
    parse_users,
)


def _record(**overrides) -> dict:
    base = {
        "username": "daniel",
        "password_hash": hash_password("hunter2"),
        "study_ids": ["abcdWXYZ"],
        "coach_study_ids": ["coachAAAA"],
        "uscf_member_id": "12345678",
        "lichess_token": "lip_secret",
    }
    base.update(overrides)
    return base


def _config(*records) -> str:
    return json.dumps(list(records))


# ---------------------------------------------------------------------------
# Well-formed config
# ---------------------------------------------------------------------------

class TestWellFormed:
    def test_parses_one_record_per_user(self):
        users = parse_users(_config(_record(username="daniel"),
                                    _record(username="friend")))
        assert set(users) == {"daniel", "friend"}
        assert all(isinstance(u, UserRecord) for u in users.values())

    def test_record_carries_every_configured_field(self):
        users = parse_users(_config(_record()))
        daniel = users["daniel"]
        assert daniel.username == "daniel"
        assert daniel.study_ids == ("abcdWXYZ",)
        assert daniel.coach_study_ids == ("coachAAAA",)
        assert daniel.uscf_member_id == "12345678"
        assert daniel.lichess_token == "lip_secret"

    def test_optional_fields_default_when_absent(self):
        users = parse_users(_config({
            "username": "minimal",
            "password_hash": hash_password("pw"),
            "study_ids": ["abcd1234"],
        }))
        rec = users["minimal"]
        assert rec.coach_study_ids == ()
        assert rec.uscf_member_id is None
        assert rec.lichess_token is None

    def test_study_ids_accept_a_comma_separated_string(self):
        users = parse_users(_config(_record(study_ids=" a , b ,")))
        assert users["daniel"].study_ids == ("a", "b")

    def test_empty_config_means_no_users(self):
        assert parse_users("") == {}
        assert parse_users("   ") == {}
        assert parse_users("[]") == {}


# ---------------------------------------------------------------------------
# Passwords are hashed, never plaintext
# ---------------------------------------------------------------------------

class TestPasswords:
    def test_stored_password_is_a_hash_not_the_plaintext(self):
        users = parse_users(_config(_record(password_hash=hash_password("hunter2"))))
        assert users["daniel"].password_hash != "hunter2"
        assert "hunter2" not in users["daniel"].password_hash

    def test_verify_accepts_the_right_password_and_refuses_the_wrong_one(self):
        users = parse_users(_config(_record(password_hash=hash_password("hunter2"))))
        rec = users["daniel"]
        assert rec.verify("hunter2") is True
        assert rec.verify("wrong") is False

    def test_a_plaintext_password_field_is_refused(self):
        """A config carrying a plaintext ``password`` fails loudly — passwords
        must be pre-hashed (there is a `python -m user_config hash` helper)."""
        with pytest.raises(UserConfigError, match="hash"):
            parse_users(json.dumps([{
                "username": "daniel", "password": "hunter2",
                "study_ids": ["abcdWXYZ"],
            }]))


# ---------------------------------------------------------------------------
# Malformed config fails clearly at load
# ---------------------------------------------------------------------------

class TestMalformed:
    def test_invalid_json_raises_clearly(self):
        with pytest.raises(UserConfigError, match="JSON"):
            parse_users("{not json")

    def test_top_level_must_be_a_list(self):
        with pytest.raises(UserConfigError, match="list"):
            parse_users(json.dumps({"username": "daniel"}))

    def test_missing_username_raises_clearly(self):
        with pytest.raises(UserConfigError, match="username"):
            parse_users(_config({"password_hash": "x", "study_ids": ["a"]}))

    def test_missing_password_hash_raises_clearly(self):
        with pytest.raises(UserConfigError, match="password_hash"):
            parse_users(_config({"username": "daniel", "study_ids": ["a"]}))

    def test_missing_study_ids_raises_clearly(self):
        with pytest.raises(UserConfigError, match="study_ids"):
            parse_users(_config({"username": "daniel",
                                 "password_hash": hash_password("pw")}))

    def test_empty_study_ids_raises_clearly(self):
        with pytest.raises(UserConfigError, match="study_ids"):
            parse_users(_config(_record(study_ids=[])))

    def test_duplicate_usernames_raise_clearly(self):
        with pytest.raises(UserConfigError, match="(?i)duplicate"):
            parse_users(_config(_record(username="daniel"),
                                _record(username="daniel")))

    @pytest.mark.parametrize("bad", ["john smith", "a/b", ".", "..", "he@llo"])
    def test_username_that_wont_map_to_a_cache_dir_raises(self, bad):
        # Only chars that survive the cache-dir mapping unchanged are allowed,
        # so distinct users can never collapse to one directory (#89).
        with pytest.raises(UserConfigError, match="(?i)username"):
            parse_users(_config(_record(username=bad)))

    def test_dotted_and_hyphenated_usernames_are_allowed(self):
        users = parse_users(_config(_record(username="a.b_c-1")))
        assert set(users) == {"a.b_c-1"}


# ---------------------------------------------------------------------------
# The hashing helper
# ---------------------------------------------------------------------------

class TestHashPassword:
    def test_hash_is_verifiable_and_not_the_plaintext(self):
        h = hash_password("correct horse")
        assert h != "correct horse"
        rec = UserRecord(username="u", password_hash=h, study_ids=("a",),
                         coach_study_ids=(), uscf_member_id=None,
                         lichess_token=None)
        assert rec.verify("correct horse")
        assert not rec.verify("battery staple")
