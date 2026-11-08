"""Data model for the generated world.

Everything is a frozen dataclass holding tuples, so a World cannot change after
it is built and two worlds from the same seed compare equal.
"""

from dataclasses import dataclass
from typing import Literal

type Scenario = Literal["normal", "duplicate_name", "colleague_lookup", "unauthenticated"]


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
    message_pool: str  # "tuning" or "held_out" (ADR-013)
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
