"""OAuth grants for the shared Meridian people: the Shadow AI Radar tuning world.

Google OAuth grants, shaped like the Admin SDK's `tokens.list` and the Reports API's token
activity, for the 230 people of `meridian_dataset.month.personas`, plus one leaver from the
Access tuning world's HRIS whose account was never removed (OAR-009). Its own generator
version, seed and random stream; it only reads the people and the HRIS and changes no
existing world. A tuning world only: no held-out or sealed OAuth world exists. All data is
fake.

Files, under `<out>/`:

- `radar/`: what the radar may read. `world.json` (reference time), `directory_users.json`,
  `tokens.json` (`{primaryEmail: [token items]}`), `token_activity.json` (newest first),
  `app_info.json` (vendor directory and Google's verified flag), `it_app_catalogue.json`,
  `banned_apps.json`, `compromised_apps.json` (Security's list), `exceptions.json` and
  `hris_leavers.json` (HR's leavers, from the Access world's HRIS).
- `key/answer_key.json`: the 8 planted risky grants and the decoys. The radar never reads it.
- `manifest.json`: the SHA-256 of every file.
"""

from meridian_dataset.oauth.generator import (
    BANNED_PLANT,
    HRIS_LEAVERS_FILE,
    KEY_DIR,
    OAUTH_GENERATOR_VERSION,
    OAUTH_TUNING_SEED,
    PLANTS,
    RADAR_DIR,
    REFERENCE_TIME,
    Leaver,
    OAuthWorld,
    generate_oauth_world,
    hris_leavers,
    write_oauth_world,
)

__all__ = [
    "BANNED_PLANT",
    "HRIS_LEAVERS_FILE",
    "KEY_DIR",
    "OAUTH_GENERATOR_VERSION",
    "OAUTH_TUNING_SEED",
    "PLANTS",
    "RADAR_DIR",
    "REFERENCE_TIME",
    "Leaver",
    "OAuthWorld",
    "generate_oauth_world",
    "hris_leavers",
    "write_oauth_world",
]
