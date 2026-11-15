"""Shadow AI Radar (oauth-radar) for the fictional company Meridian. See oauth-radar/BRIEF.md.

Milestone 0 holds the data model, a loader for one export shape (`load`), a two-rule stub of
the rules engine (`rules`) and a command line (`python -m meridian_oauth_radar`).

- A **grant** is one person's OAuth consent to one third-party app, exactly as an export
  shows it: who, which app, which scopes.
- A **finding** is a grant that at least one rule flagged, for a person in IT to review.

The radar reads files and writes findings. It never revokes a grant, never calls an API and
never reads message or file content (OAR-004, OAR-005). All data is fake.
"""

from dataclasses import dataclass

# Where a grant came from. Slack joins in milestone 1, when the dataset writes its exports.
SOURCES: tuple[str, ...] = ("google",)
SEVERITIES: tuple[str, ...] = ("high", "medium", "low")
# The only thing a finding can ask for. There is no revoke action (OAR-004).
REVIEW = "review"


@dataclass(frozen=True, slots=True)
class Grant:
    """One person's OAuth grant to one app, as the export shows it.

    `user` is the account exactly as written in the export (an email address for Google). It
    isn't matched to a person yet. `scopes` are sorted, with no duplicates. There is no field
    for message or file content, because the radar never reads any (OAR-005).
    """

    source: str
    user: str
    app_id: str  # Google: the OAuth client ID
    app_name: str  # the name the export shows, as the app's publisher typed it
    scopes: tuple[str, ...]

    def __post_init__(self) -> None:
        if self.source not in SOURCES:
            raise ValueError(f"unknown source {self.source!r}; expected one of {SOURCES}")
        if self.scopes != tuple(sorted(set(self.scopes))):
            raise ValueError("scopes must be sorted and unique")


@dataclass(frozen=True, slots=True)
class RuleHit:
    """One rule that fired on a grant, with its points and the reason in plain English."""

    rule: str
    points: int
    reason: str


@dataclass(frozen=True, slots=True)
class Finding:
    """A flagged grant. Its only action is a review by a person in IT (OAR-004)."""

    grant: Grant
    hits: tuple[RuleHit, ...]
    severity: str
    action: str = REVIEW

    def __post_init__(self) -> None:
        if not self.hits:
            raise ValueError("a finding needs at least one rule hit")
        if self.severity not in SEVERITIES:
            raise ValueError(f"unknown severity {self.severity!r}; expected one of {SEVERITIES}")
        if self.action != REVIEW:
            raise ValueError(
                f"a finding can only ask for {REVIEW!r}, not {self.action!r}: "
                "the radar never revokes anything (OAR-004)"
            )

    @property
    def score(self) -> int:
        """The sum of the rule hits' points. Findings are ranked by it, highest first."""
        return sum(hit.points for hit in self.hits)


__all__ = ["REVIEW", "SEVERITIES", "SOURCES", "Finding", "Grant", "RuleHit"]
