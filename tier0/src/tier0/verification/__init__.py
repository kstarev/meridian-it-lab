"""The verification ladder: how much proof each tier needs before tier0 acts.

- Rung 0: a signed Slack user ID bound to the directory (`tier0.identity.binding`).
- Rung 1: context checks in code (`context`).
- Rung 2, for Tier B: a fresh SSO step-up (`step_up`) or an Okta Verify push with a
  number challenge (`push`).
- Rung 3, for Tier C: a human only. tier0 never passes this rung by itself.

`ladder` holds the policy and walks the rungs. Proof comes only from systems, never
from what a caller says about themselves.
"""
