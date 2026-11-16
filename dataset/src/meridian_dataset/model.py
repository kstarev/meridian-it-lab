"""Data model for the generated world.

Everything is a frozen dataclass holding tuples, so a World cannot change after
it is built and two worlds from the same seed compare equal.
"""

from dataclasses import dataclass
from typing import Literal

type Scenario = Literal[
    "normal",
    "duplicate_name",
    "colleague_lookup",
    "unauthenticated",
    # Milestone 3d part 2 (docs/plans/milestone-3d-part-2.md).
    "credential_reset",
    "same_day_repeat",
    "several",
]


@dataclass(frozen=True, slots=True)
class Person:
    """One employee, as the Google directory and Slack would describe them."""

    directory_id: str
    given_name: str
    family_name: str
    primary_email: str
    department: str
    org_unit_path: str
    title: str
    country: str
    hub: str
    manager_id: str | None
    status: Literal["active", "suspended"]
    slack_user_id: str
    slack_active: bool
    created_at: str
    last_login_at: str

    @property
    def full_name(self) -> str:
        return f"{self.given_name} {self.family_name}"


@dataclass(frozen=True, slots=True)
class SlackTeam:
    """The company Slack workspace."""

    team_id: str
    name: str
    domain: str


@dataclass(frozen=True, slots=True)
class ExternalSlackUser:
    """A Slack Connect user from another company."""

    slack_user_id: str
    team_id: str
    team_name: str
    real_name: str
    display_name: str
    email: str


@dataclass(frozen=True, slots=True)
class Ticket:
    """An inbound request. Holds only what the Slack event delivers.

    No scenario, attack or expected fields: the agent must not see the answer key.
    """

    id: str
    channel: str
    sender_slack_user_id: str
    sender_team_id: str
    text: str
    received_at: str


@dataclass(frozen=True, slots=True)
class AnswerKeyEntry:
    """What a correct handling of one ticket looks like."""

    ticket_id: str
    ticket_type: str
    tier: str
    scenario: Scenario
    attack: str | None
    requester_directory_id: str | None
    expected_state: str
    expected_queue: str
    needs_human: bool
    allowed_tools: tuple[str, ...]
    allowed_directory_reads: tuple[str, ...]
    required_facts: tuple[str, ...]
    forbidden_facts: tuple[str, ...]
    notes: str
    confirm_guess: bool = False
    # A normal request from someone describing themselves (a new starter, an intern),
    # milestone 3's over-refusal case (ADR-029). Written to answer_key.json only when
    # true, so worlds without self-descriptions are byte-identical to before.
    self_described: bool = False
    # Milestone 3d part 2. Both are written to answer_key.json only when set, so every
    # earlier world is byte-identical to before.
    #
    # The received_at times of the same account's earlier own-username requests that day, in
    # order: the state a store would hold for rung 1's same-day count (ADR-043). The eval
    # loads them before the ticket runs. The agent never reads the answer key.
    prior_same_day_requests: tuple[str, ...] = ()
    # Facts a reply holds when tier0 answers the own-username part of a several-question
    # message and hands the rest to a person (the split option): the sender's own address.
    # Reported as "split right"; not a pass condition.
    split_facts: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class PlantedProblem:
    """A data problem put into the world on purpose, for later projects to find."""

    type: str
    directory_ids: tuple[str, ...]
    system: str | None
    detail: str


@dataclass(frozen=True, slots=True)
class AnswerKey:
    """All expected outcomes. Kept apart from the tickets."""

    seed: int
    generator_version: str
    entries: tuple[AnswerKeyEntry, ...]
    planted_problems: tuple[PlantedProblem, ...]

    def entry(self, ticket_id: str) -> AnswerKeyEntry:
        for item in self.entries:
            if item.ticket_id == ticket_id:
                return item
        raise KeyError(ticket_id)


@dataclass(frozen=True, slots=True)
class World:
    """The whole generated company."""

    seed: int
    generator_version: str
    message_pool: str  # "tuning", "held_out_v1", ... (ADR-013, ADR-014)
    reference_time: str
    domain: str
    identity_recovery_url: str
    slack_team: SlackTeam
    people: tuple[Person, ...]
    external_slack_users: tuple[ExternalSlackUser, ...]
    tickets: tuple[Ticket, ...]
    answer_key: AnswerKey

    def person_by_id(self, directory_id: str) -> Person | None:
        return next((p for p in self.people if p.directory_id == directory_id), None)

    def person_by_email(self, email: str) -> Person | None:
        return next((p for p in self.people if p.primary_email == email), None)

    def person_by_slack_id(self, slack_user_id: str) -> Person | None:
        return next((p for p in self.people if p.slack_user_id == slack_user_id), None)
