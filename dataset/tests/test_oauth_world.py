"""The OAuth tuning world: seeded, deterministic, the shared people, 8 plants.

A tuning world only; no held-out or sealed OAuth world exists. The answer key is written
apart from everything the radar reads.
"""

import hashlib
import json
from collections import Counter
from pathlib import Path

from meridian_dataset.access import ACCESS_TUNING_SEED, generate_access, write_access
from meridian_dataset.month.personas import build_personas
from meridian_dataset.oauth import (
    BANNED_PLANT,
    HRIS_LEAVERS_FILE,
    KEY_DIR,
    OAUTH_TUNING_SEED,
    PLANTS,
    RADAR_DIR,
    generate_oauth_world,
    hris_leavers,
    write_oauth_world,
)
from meridian_dataset.oauth.__main__ import main
from meridian_dataset.oauth.apps import ALL_APPS, BANNED, PLANTED

# Moves only when the OAuth generator changes on purpose, in the same pull request.
GOLDEN_MANIFEST_SHA256 = "2ea62cf88a3bbe4fac1f652a6374a512c66cb49ff1722e8414b2a93af08f6768"


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


def test_the_people_are_the_shared_230_plus_one_leaver_and_stay_unchanged() -> None:
    before = personas_digest()
    world = generate_oauth_world()
    assert personas_digest() == before
    roster = tuple(p.email for p in build_personas())
    assert len(roster) == 230
    assert world.users[:230] == roster
    [leaver] = world.users[230:]
    assert leaver not in roster
    assert leaver in {r.work_email for r in world.leavers}
    inputs = world.radar_inputs()
    listed = [user["primaryEmail"] for user in inputs["directory_users.json"]]
    assert listed == list(world.users)
    assert set(inputs["tokens.json"]) <= set(world.users)


def test_leavers_are_read_from_the_access_worlds_hris() -> None:
    access = generate_access(ACCESS_TUNING_SEED)
    records = {(r.employee_id, r.work_email): r for r in access.hris.records}
    world = generate_oauth_world()
    copied = world.radar_inputs()[HRIS_LEAVERS_FILE]
    assert copied["as_of"] == "2026-11-16"
    assert copied["records"] and len(copied["records"]) == len(world.leavers)
    for row in copied["records"]:
        assert set(row) == {"employee_id", "work_email", "status", "end_date"}
        source = records[(row["employee_id"], row["work_email"])]
        assert (source.status, source.end_date) == (row["status"], row["end_date"])
        assert row["status"] == "terminated" and row["end_date"] < "2026-11-16"
    # Nobody still employed (a rehire's old record) is listed, and nobody on the roster.
    employed = {r.work_email for r in access.hris.records if r.status != "terminated"}
    emails = {row["work_email"] for row in copied["records"]}
    assert not emails & employed
    assert not emails & {p.email for p in build_personas()}
    assert world.leavers == hris_leavers()


def test_building_the_oauth_world_leaves_the_access_world_unchanged(tmp_path: Path) -> None:
    def access_manifest(folder: Path) -> bytes:
        paths = write_access(generate_access(ACCESS_TUNING_SEED), folder)
        return b"".join(name.encode() + paths[name].read_bytes() for name in sorted(paths))

    before = hashlib.sha256(access_manifest(tmp_path / "before")).hexdigest()
    generate_oauth_world()
    write_oauth_world(generate_oauth_world(), tmp_path / "oauth")
    assert hashlib.sha256(access_manifest(tmp_path / "after")).hexdigest() == before


def test_eight_risky_grants_are_planted() -> None:
    world = generate_oauth_world()
    key = world.answer_key()["risky_grants"]
    assert len(key) == len(PLANTS) + 2 == 8
    assert len({row["user_key"] for row in key}) == 8
    tokens = world.radar_inputs()["tokens.json"]
    for row in key:
        held = [t for t in tokens[row["user_key"]] if t["clientId"] == row["client_id"]]
        assert len(held) == 1, row["app"]
    holders = Counter(g.app.slug for g in world.grants)
    # The catalogue's 6 and the banned-app grant each go to an app nobody else holds.
    assert all(holders[app.slug] == 1 for app in (*PLANTED, BANNED))
    assert Counter(row["kind"] for row in key) == {
        "dormant_restricted": 2,
        "risky_in_use": 2,
        "compromised_app": 1,
        "mail_settings": 1,
        "banned_app": 1,
        "leaver_grant": 1,
    }
    banned = next(row for row in key if row["kind"] == BANNED_PLANT.kind)
    assert banned["client_id"] == BANNED.client_id
    leaver = next(row for row in key if row["kind"] == "leaver_grant")
    assert leaver["app"] == "Huddlewave Meet"
    assert leaver["user_key"] == world.users[-1]


# The 524 grants of generator 0.2.0 (before the banned-app and leaver plants), as a digest of
# (user, app, granted, last used). The two new plants are drawn last, so these don't move.
EARLIER_GRANTS_SHA256 = "0379b3747b5420b910777402d73f3fe647ef6ce197cc92c07c1606d51fe6657d"


def test_the_new_plants_leave_the_earlier_grants_where_they_were() -> None:
    world = generate_oauth_world()
    new = {(g.user, g.app.slug) for _, g in world.plants[len(PLANTS) :]}
    rows = sorted(
        (g.user, g.app.slug, g.granted.isoformat(), g.last_used.isoformat())
        for g in world.grants
        if (g.user, g.app.slug) not in new
    )
    assert len(rows) == len(world.grants) - 2 == 524
    assert hashlib.sha256(repr(rows).encode()).hexdigest() == EARLIER_GRANTS_SHA256


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


def test_three_exceptions_one_expired_and_one_over_90_days() -> None:
    exceptions = generate_oauth_world().radar_inputs()["exceptions.json"]["exceptions"]
    assert [(e["approved_on"], e["expires"]) for e in exceptions] == [
        ("2026-11-02", "2027-01-31"),
        ("2026-08-03", "2026-11-01"),
        ("2026-06-01", "2027-06-01"),
    ]
    assert [e["user_key"] is None for e in exceptions] == [False, False, True]


def test_write_and_the_command_line(tmp_path: Path) -> None:
    world = generate_oauth_world()
    paths = write_oauth_world(world, tmp_path / "w")
    assert {name: path.read_bytes() for name, path in paths.items()} == world.files()
    assert main(["--out", str(tmp_path / "cli")]) == 0
    written = (tmp_path / "cli" / "tuning" / "manifest.json").read_bytes()
    assert hashlib.sha256(written).hexdigest() == GOLDEN_MANIFEST_SHA256
