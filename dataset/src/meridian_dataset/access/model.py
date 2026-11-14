"""Records for the Access-as-Code world (AAC-004). All data is fake.

Everything is a frozen dataclass holding tuples, so a world compares equal when
it was built from the same seed and recipe.
"""

from dataclasses import dataclass

ACCESS_GENERATOR_VERSION = "0.3.0"
ACCESS_TUNING_SEED = 6108
# The world is the state at the end of the simulated month; HRIS events fall on
# days 0-29 of November 2026.
REFERENCE_DATE = "2026-12-01"
MONTH_START = "2026-11-01"
DOMAIN = "meridian.example"
PERSONAL_DOMAIN = "mail.example"


@dataclass(frozen=True, slots=True)
class HrisRecord:
    employee_id: str
    given_name: str
    family_name: str
    preferred_name: str
    work_email: str
    personal_email: str
    job_title: str  # as typed by HR, one of the role's messy variants
    department: str
    team: str
    manager_id: str
    country: str
    hub: str
    employment_type: str  # employee | contractor
    status: str  # active | on_leave | terminated
    start_date: str
    end_date: str
    leave_start: str
    leave_end: str


@dataclass(frozen=True, slots=True)
class HrisEvent:
    day: int  # 0-29, days after MONTH_START
    date: str
    employee_id: str
    kind: str  # hire | move | terminate | leave_start | leave_end | rehire | name_change | ...
    fields: tuple[tuple[str, str], ...]
    source: str  # hr | bulk_import


@dataclass(frozen=True, slots=True)
class OktaUser:
    id: str  # 00u...
    status: str  # ACTIVE | SUSPENDED | DEPROVISIONED
    created: str
    last_login: str
    login: str
    email: str
    first_name: str
    last_name: str


@dataclass(frozen=True, slots=True)
class OktaApp:
    id: str  # 0oa...
    label: str
    app_id: str  # the catalog slug


@dataclass(frozen=True, slots=True)
class OktaAssignment:
    app_id: str  # the Okta app id (0oa...)
    user_id: str
    created: str
    scope: str  # USER | GROUP
    role: str  # member | admin


@dataclass(frozen=True, slots=True)
class OktaLogEvent:
    uuid: str
    published: str
    event_type: str  # application.user_membership.add | ...change_privilege
    actor_alternate_id: str
    actor_type: str
    via: str  # provisioner | group rule | admin console
    target_app_id: str  # the Okta app id
    target_user_id: str
    target_user_login: str


@dataclass(frozen=True, slots=True)
class GoogleUser:
    id: str
    primary_email: str
    given_name: str
    family_name: str
    full_name: str
    suspended: bool
    org_unit_path: str
    aliases: tuple[str, ...]
    creation_time: str
    last_login_time: str


@dataclass(frozen=True, slots=True)
class SlackScimUser:
    id: str  # U...
    user_name: str
    display_name: str
    email: str
    active: bool


@dataclass(frozen=True, slots=True)
class AppExport:
    app_id: str
    file_name: str  # <app_id>.csv
    encoding: str  # any Python text codec the recipe names
    layout: str  # the recipe's layout name
    date_format: str  # the recipe's date format name
    header: tuple[str, ...]
    rows: tuple[tuple[str, ...], ...]  # a blank row is all empty strings
    delimiter: str = ","
    quote_all: bool = False
    preamble: tuple[str, ...] = ()  # lines before the header
    footer: tuple[str, ...] = ()  # lines after the last row


@dataclass(frozen=True, slots=True)
class AccountOwner:
    """Answer key: who an account really belongs to ("" means nobody in HRIS)."""

    system: str  # okta | google | slack | an app_id for an export row
    account: str  # login, email or username exactly as it appears
    owner_employee_id: str


@dataclass(frozen=True, slots=True)
class AccessFinding:
    """Answer key: one planted problem.

    `day` is the simulated day (0-29) the problem first shows; 0 if it was already there
    before the month. `system` is okta, google, slack, hris or an app_id.
    """

    type: str
    employee_id: str  # "" for unowned accounts and for the mass-termination event
    account: str
    system: str
    app_id: str  # "" for system-level findings
    expected_action: str  # remove | suspend | add | flag | never_touch | block_two_approvers
    day: int
    detail: str


@dataclass(frozen=True, slots=True)
class AccessAnswerKey:
    seed: int
    generator_version: str
    recipe_name: str
    recipe_sha256: str
    true_roles: tuple[tuple[str, str], ...]  # (employee_id, role_id) for every real worker
    owners: tuple[AccountOwner, ...]
    findings: tuple[AccessFinding, ...]
    break_glass_accounts: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class Hris:
    records: tuple[HrisRecord, ...]
    events: tuple[HrisEvent, ...]


@dataclass(frozen=True, slots=True)
class Okta:
    users: tuple[OktaUser, ...]
    apps: tuple[OktaApp, ...]
    assignments: tuple[OktaAssignment, ...]
    log: tuple[OktaLogEvent, ...]


@dataclass(frozen=True, slots=True)
class AccessWorld:
    seed: int
    generator_version: str
    recipe_name: str
    reference_date: str
    month_start: str
    domain: str
    hris: Hris
    okta: Okta
    google_users: tuple[GoogleUser, ...]
    slack_users: tuple[SlackScimUser, ...]
    exports: tuple[AppExport, ...]
    answer_key: AccessAnswerKey
