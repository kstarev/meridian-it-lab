"""The OAuth tuning world: seeded, deterministic, the shared people, exactly 6 plants.

A tuning world only; no held-out or sealed OAuth world exists. The answer key is written
apart from everything the radar reads.
"""

import hashlib
import json
from collections import Counter
from pathlib import Path

from meridian_dataset.month.personas import build_personas
from meridian_dataset.oauth import (
    KEY_DIR,
    OAUTH_TUNING_SEED,
    PLANTS,
    RADAR_DIR,
    generate_oauth_world,
    write_oauth_world,
)
from meridian_dataset.oauth.__main__ import main
from meridian_dataset.oauth.apps import ALL_APPS, PLANTED

# Moves only when the OAuth generator changes on purpose, in the same pull request.
GOLDEN_MANIFEST_SHA256 = "dd39c111b593921dc6db2d472a3e1efcb40840c30344aa00a27afb7de325fd3d"


def personas_digest() -> str:
    return hashlib.sha256(repr(build_personas()).encode()).hexdigest()


def test_same_seed_same_bytes_and_the_golden_hash() -> None:
    first, second = generate_oauth_world().files(), generate_oauth_world().files()
    assert first == second
    assert hashlib.sha256(first["manifest.json"]).hexdigest() == GOLDEN_MANIFEST_SHA256


def test_another_seed_gives_another_world() -> None:
    assert generate_oauth_world(OAUTH_TUNING_SEED + 1).files() != generate_oauth_world().files()


def test_the_manifest_hashes_every_other_file() -> None:
    files = generate_oauth_world().files()
    listed = json.loads(files["manifest.json"])["files"]
    assert set(listed) == set(files) - {"manifest.json"}
    for path, digest in listed.items():
        assert hashlib.sha256(files[path]).hexdigest() == digest


def test_the_people_are_the_shared_230_and_stay_unchanged() -> None:
    before = personas_digest()
    world = generate_oauth_world()
    assert personas_digest() == before
    assert world.users == tuple(p.email for p in build_personas())
    assert len(world.users) == 230
    inputs = world.radar_inputs()
    listed = [user["primaryEmail"] for user in inputs["directory_users.json"]]
    assert listed == list(world.users)
    assert set(inputs["tokens.json"]) <= set(world.users)


def test_exactly_six_risky_grants_are_planted() -> None:
    world = generate_oauth_world()
    key = world.answer_key()["risky_grants"]
    assert len(key) == len(PLANTS) == 6
    assert len({row["user_key"] for row in key}) == 6
    tokens = world.radar_inputs()["tokens.json"]
    for row in key:
        held = [t for t in tokens[row["user_key"]] if t["clientId"] == row["client_id"]]
        assert len(held) == 1, row["app"]
    holders = Counter(g.app.slug for g in world.grants)
    assert all(holders[app.slug] == 1 for app in PLANTED)
    assert Counter(row["kind"] for row in key) == {
        "dormant_restricted": 2,
        "risky_in_use": 2,
        "compromised_app": 1,
        "mail_settings": 1,
    }


def test_nothing_the_radar_reads_names_a_plant() -> None:
    files = generate_oauth_world().files()
    words = {"answer", "plant", "risky", "decoy", *(p.kind for p in PLANTS)}
    for path, data in files.items():
        if path.startswith(f"{RADAR_DIR}/"):
            text = data.decode().casefold()
            assert not [w for w in words if w in text], path
    assert [p for p in files if p.startswith(f"{KEY_DIR}/")] == [f"{KEY_DIR}/answer_key.json"]


def test_fake_data_only_and_no_token_values() -> None:
    files = generate_oauth_world().files()
    tokens = json.loads(files[f"{RADAR_DIR}/tokens.json"])
    for user, items in tokens.items():
        assert user.endswith("@meridian.example")
        for item in items:
            assert "-fake" in item["clientId"]
            assert set(item) == {
                "kind",
                "etag",
                "clientId",
                "displayText",
                "anonymous",
                "nativeApp",
                "userKey",
                "scopes",
            }
    for data in files.values():
        text = data.decode()
        assert "ya29." not in text
        assert "access_token" not in text
        assert "refresh_token" not in text
    assert all("-fake" in app.client_id for app in ALL_APPS)


def test_every_grant_has_activity_and_last_use_is_not_after_the_snapshot() -> None:
    world = generate_oauth_world()
    inputs = world.radar_inputs()
    seen = {
        (item["actor"]["email"], item["events"][0]["parameters"][0]["value"])
        for item in inputs["token_activity.json"]
    }
    assert {(g.user, g.app.client_id) for g in world.grants} == seen
    times = [item["id"]["time"] for item in inputs["token_activity.json"]]
    assert times == sorted(times, reverse=True)
    assert max(times) <= inputs["world.json"]["reference_time"]


def test_two_exceptions_one_expired() -> None:
    exceptions = generate_oauth_world().radar_inputs()["exceptions.json"]["exceptions"]
    assert [e["expires"] for e in exceptions] == ["2027-01-31", "2026-11-01"]


def test_write_and_the_command_line(tmp_path: Path) -> None:
    world = generate_oauth_world()
    paths = write_oauth_world(world, tmp_path / "w")
    assert {name: path.read_bytes() for name, path in paths.items()} == world.files()
    assert main(["--out", str(tmp_path / "cli")]) == 0
    written = (tmp_path / "cli" / "tuning" / "manifest.json").read_bytes()
    assert hashlib.sha256(written).hexdigest() == GOLDEN_MANIFEST_SHA256
