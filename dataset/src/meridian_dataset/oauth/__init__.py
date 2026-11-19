"""OAuth grants for the shared Meridian people: the Shadow AI Radar tuning world.

Google OAuth grants, shaped like the Admin SDK's `tokens.list` and the Reports API's token
activity, for the 230 people of `meridian_dataset.month.personas`. Its own generator version,
seed and random stream; it only reads the people and changes no existing world. A tuning
world only: no held-out or sealed OAuth world exists. All data is fake.

Files, under `<out>/`:

- `radar/`: what the radar may read. `world.json` (reference time), `directory_users.json`,
  `tokens.json` (`{primaryEmail: [token items]}`), `token_activity.json` (newest first),
  `app_info.json` (vendor directory and Google's verified flag), `it_app_catalogue.json`,
  `banned_apps.json`, `compromised_apps.json` (Security's list) and `exceptions.json`.
- `key/answer_key.json`: the 6 planted risky grants and the decoys. The radar never reads it.
- `manifest.json`: the SHA-256 of every file.
"""

from meridian_dataset.oauth.generator import (
    KEY_DIR,
    OAUTH_GENERATOR_VERSION,
    OAUTH_TUNING_SEED,
    PLANTS,
    RADAR_DIR,
    REFERENCE_TIME,
    OAuthWorld,
    generate_oauth_world,
    write_oauth_world,
)

__all__ = [
    "KEY_DIR",
    "OAUTH_GENERATOR_VERSION",
    "OAUTH_TUNING_SEED",
    "PLANTS",
    "RADAR_DIR",
    "REFERENCE_TIME",
    "OAuthWorld",
    "generate_oauth_world",
    "write_oauth_world",
]
