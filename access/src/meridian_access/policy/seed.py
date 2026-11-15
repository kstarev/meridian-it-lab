"""Seed title_map.yaml from the TUNING world only (a stand-in for the IT team's review)."""

import collections

import yaml

from meridian_dataset.access import ACCESS_TUNING_SEED, generate_access, load_recipe

HEADER = (
    '# The reviewed map from HRIS job title to role. Titles not listed are "unmapped":\n'
    "# birthright apps only, until someone decides. Fake data.\n"
    "# Seeded by `python -m meridian_access.policy seed-titles` from the tuning (practice) world,\n"
    "# which models the IT team reviewing those titles. Changes need a code-owner review.\n"
)


def seed_title_map() -> str:
    """title_map.yaml text: per distinct tuning-world title, the role most people with it have."""
    world = generate_access(ACCESS_TUNING_SEED, load_recipe("tuning"))
    true_role = dict(world.answer_key.true_roles)
    counts: dict[str, collections.Counter[str]] = collections.defaultdict(collections.Counter)
    for record in world.hris.records:
        role = true_role.get(record.employee_id)
        if role:
            counts[record.job_title][role] += 1
    titles = []
    for title in sorted(counts):
        tally = counts[title]
        role = min(tally, key=lambda r: (-tally[r], r))
        titles.append(
            {"title": title, "role": role, "source": "tuning-review", "reviewed_by": "owner"}
        )
    return HEADER + yaml.safe_dump(
        {"titles": titles}, sort_keys=False, allow_unicode=True, width=100
    )
