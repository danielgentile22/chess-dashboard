"""
user_config.py
==============
The multi-user configuration parser (issue #71 [G1]).

The dashboard is no longer hard-wired to one player.  A plain text-style config
lists who is allowed: one record per user, each with a username, a **hashed**
password, the Study IDs that are the source of truth for their Games, the Study
IDs that hold their coach's reviews, their USCF member ID, and their Lichess
token (needed to read their private coach Studies).  Adding a user is adding a
record — there is no self-service signup and no settings UI (the Render free
tier has no persistent disk; that is a future PRD).

The config is a JSON array, supplied (consistent with the rest of ``config.py``)
via the ``USCF_DASHBOARD_USERS`` environment variable::

    [
      {
        "username": "daniel",
        "password_hash": "scrypt:32768:8:1$...",
        "study_ids": ["6jYtXHGp"],
        "coach_study_ids": ["WnMe519K", "ws2qUCW4"],
        "uscf_member_id": "32487228",
        "lichess_token": "lip_xxx"
      }
    ]

A malformed record raises :class:`UserConfigError` clearly at load, so a bad
config fails loudly rather than silently serving the wrong data.  Passwords are
stored and compared as hashes, never plaintext — mint one with::

    python -m user_config hash 'my password'

Public API
----------
parse_users     Parse a config block → {username: UserRecord}; raises on malformed.
hash_password   Hash a plaintext password for a config record.
UserRecord      One validated user record, with a ``verify(password)`` method.
UserConfigError The clear failure raised on any malformed config.
"""
from __future__ import annotations

import json
from dataclasses import dataclass

from werkzeug.security import check_password_hash, generate_password_hash


class UserConfigError(ValueError):
    """A user-config block was malformed — it fails loudly, never silently."""


@dataclass(frozen=True)
class UserRecord:
    """One allow-listed user: their credentials and their configured sources."""

    username: str
    password_hash: str
    study_ids: tuple[str, ...]          # the Games' source of truth (ADR 0001)
    coach_study_ids: tuple[str, ...]    # the coach's review Studies (issue #74)
    uscf_member_id: str | None          # enriches the Games (ADR 0003)
    lichess_token: str | None           # reads this user's private coach Studies

    def verify(self, password: str) -> bool:
        """Whether *password* matches this user's stored hash."""
        return check_password_hash(self.password_hash, password)


def hash_password(plain: str) -> str:
    """Hash a plaintext password for storage in a config record."""
    return generate_password_hash(plain)


def parse_users(raw: str) -> dict[str, UserRecord]:
    """
    Parse a multi-user config block into ``{username: UserRecord}``.

    An empty or blank block means no users are configured (the dashboard runs
    in its single-user, ungated mode — exactly as before multi-user landed).

    Raises
    ------
    UserConfigError : the block is not valid JSON, is not a list of records, a
        record is missing a required field, a password is given in plaintext,
        or two records share a username.
    """
    if not raw or not raw.strip():
        return {}

    try:
        items = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise UserConfigError(f"User config is not valid JSON: {exc}") from exc

    if not isinstance(items, list):
        raise UserConfigError(
            "User config must be a JSON list of user records, "
            f"got {type(items).__name__}."
        )

    users: dict[str, UserRecord] = {}
    for index, item in enumerate(items):
        record = _record_from_item(item, index)
        if record.username in users:
            raise UserConfigError(
                f"Duplicate username {record.username!r} in user config — "
                "each user record must have a unique username."
            )
        users[record.username] = record
    return users


def _record_from_item(item: object, index: int) -> UserRecord:
    where = f"user record #{index + 1}"
    if not isinstance(item, dict):
        raise UserConfigError(f"{where} must be an object, got {type(item).__name__}.")

    if "password" in item:
        raise UserConfigError(
            f"{where} carries a plaintext 'password' — passwords must be stored "
            "as a 'password_hash'.  Mint one with: python -m user_config hash '<pw>'"
        )

    username = _require_str(item, "username", where)
    password_hash = _require_str(item, "password_hash", where)
    study_ids = _require_ids(item, "study_ids", where)
    coach_study_ids = _optional_ids(item, "coach_study_ids", where)
    uscf_member_id = _optional_str(item, "uscf_member_id")
    lichess_token = _optional_str(item, "lichess_token")

    return UserRecord(
        username=username,
        password_hash=password_hash,
        study_ids=study_ids,
        coach_study_ids=coach_study_ids,
        uscf_member_id=uscf_member_id,
        lichess_token=lichess_token,
    )


def _require_str(item: dict, key: str, where: str) -> str:
    value = item.get(key)
    if not isinstance(value, str) or not value.strip():
        raise UserConfigError(f"{where} is missing a non-empty {key!r}.")
    return value.strip()


def _optional_str(item: dict, key: str) -> str | None:
    value = item.get(key)
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _require_ids(item: dict, key: str, where: str) -> tuple[str, ...]:
    ids = _optional_ids(item, key, where)
    if not ids:
        raise UserConfigError(
            f"{where} is missing a non-empty {key!r} (a user needs at least one "
            "Study holding their Games)."
        )
    return ids


def _optional_ids(item: dict, key: str, where: str) -> tuple[str, ...]:
    """Accept either a JSON list of IDs or a comma-separated string, mirroring
    config.parse_study_ids; whitespace stripped, blanks dropped."""
    value = item.get(key)
    if value is None:
        return ()
    if isinstance(value, str):
        return tuple(s.strip() for s in value.split(",") if s.strip())
    if isinstance(value, list):
        return tuple(str(s).strip() for s in value if str(s).strip())
    raise UserConfigError(
        f"{where} field {key!r} must be a list of Study IDs or a "
        "comma-separated string."
    )


def _hash_cli() -> int:
    """`python -m user_config hash '<password>'` — print a hash for a config."""
    import getpass
    import sys

    if len(sys.argv) >= 3 and sys.argv[1] == "hash":
        print(hash_password(sys.argv[2]))
        return 0
    if len(sys.argv) == 2 and sys.argv[1] == "hash":
        print(hash_password(getpass.getpass("Password: ")))
        return 0
    print("usage: python -m user_config hash '<password>'", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(_hash_cli())
