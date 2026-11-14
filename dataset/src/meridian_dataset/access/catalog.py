"""Fixed data for the access world: departments, roles, apps. No randomness here.

Role ids are private (they live only in the answer key); HR only ever types the messy
job titles. App names are made-up compounds, never real products.
"""

import unicodedata
from dataclasses import dataclass

from meridian_dataset.names import DEPARTMENTS as TIER0_DEPARTMENTS
from meridian_dataset.names import HUBS

DEPT_NAMES: tuple[str, ...] = (*TIER0_DEPARTMENTS, "Product", "Design")
# Share of everyone outside IT (IT is always exactly 3 people).
DEPT_WEIGHTS: dict[str, int] = {
    "Engineering": 30,
    "Sales": 15,
    "Customer Success": 16,
    "Marketing": 9,
    "Finance": 6,
    "People": 4,
    "Product": 11,
    "Design": 9,
}
IT_SIZE = 3
CONTRACTOR_DEPTS: tuple[str, ...] = ("Engineering", "Engineering", "Design", "Marketing", "Sales")

COUNTRIES: tuple[str, ...] = tuple(c for countries in HUBS.values() for c in countries)
HUB_OF: dict[str, str] = {c: hub for hub, countries in HUBS.items() for c in countries}
_COUNTRY_WEIGHT = {
    "US": 6, "CA": 2, "BR": 1, "MX": 1, "DE": 4, "GB": 4, "PL": 2,
    "NL": 2, "ES": 1, "IE": 1, "SG": 1, "AU": 2, "JP": 1, "IN": 3,
}  # fmt: skip
COUNTRY_WEIGHTS: tuple[int, ...] = tuple(_COUNTRY_WEIGHT[c] for c in COUNTRIES)

# Extra family names with accents; names.py has none and must not change.
EXTRA_FAMILY_NAMES: tuple[str, ...] = ("Müller", "Çelik", "Gómez", "Núñez", "Østergaard")
PREFERRED_NAMES: dict[str, str] = {
    "Mateus": "Matt",
    "Oliver": "Ollie",
    "Elena": "Ellie",
    "Jonas": "Joe",
    "Tomasz": "Tom",
    "Rahul": "Ray",
    "Hannah": "Annie",
    "Camila": "Cami",
}
# Accounts that match nobody in HRIS (local part, display name).
NOBODY_ACCOUNTS: tuple[tuple[str, str], ...] = (
    ("old.sales.shared", "Old Sales Shared"),
    ("temp-contractor-04", "Temp Contractor 04"),
    ("dana.whitfield", "Dana Whitfield"),
    ("svc.reporting", "Reporting Service"),
    ("intern.summer2", "Summer Intern 2"),
    ("jo.marlowe", "Jo Marlowe"),
)

DATE_FORMATS: tuple[str, ...] = ("iso", "dmy_slash", "mdy_slash", "d_mon_y")
MONTH_ABBR: tuple[str, ...] = (
    "Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec",
)  # fmt: skip

HIRES_IN_MONTH = 6
CLEAN_FORMER = 10  # left before the month and fully deprovisioned
ACCENTED_PEOPLE = 8
BOM_MODULUS, BOM_REMAINDER = 16, 7  # which exports start with a byte order mark


def ascii_fold(text: str) -> str:
    text = text.replace("Ø", "O").replace("ø", "o")
    return "".join(c for c in unicodedata.normalize("NFKD", text) if not unicodedata.combining(c))


# ---- apps: 140 = 45 SSO + 95 not ---------------------------------------------------------

ADJECTIVES = (
    "Amber", "Birch", "Cobalt", "Dune", "Ember", "Fjord", "Garnet", "Harbor", "Indigo",
    "Juniper", "Kestrel", "Lumen", "Maple", "Nimbus", "Onyx", "Pebble", "Quartz", "Raven",
    "Sable", "Tundra", "Umber", "Vale", "Willow", "Yarrow", "Zephyr",
)  # fmt: skip
NOUNS = (
    "Ledger", "Board", "Desk", "Vault", "Forms", "Pulse", "Notes", "Studio", "Track", "Hub",
    "Sheets", "Signal", "Atlas", "Relay", "Canvas", "Docs",
)  # fmt: skip
CATEGORIES = (
    "Finance", "Collaboration", "Engineering", "Sales", "Support", "Design", "HR", "Analytics",
)  # fmt: skip
SSO_COUNT, NON_SSO_COUNT, BIRTHRIGHT_COUNT = 45, 95, 8


@dataclass(frozen=True, slots=True)
class App:
    app_id: str
    name: str
    category: str
    sso: bool
    has_admin_role: bool


def _build_apps() -> tuple[App, ...]:
    apps = []
    for i in range(SSO_COUNT + NON_SSO_COUNT):
        adjective = ADJECTIVES[i % 25]
        noun = NOUNS[(i % 25 * 3 + i // 25) % 16]  # unique for i < 400
        apps.append(
            App(
                app_id=f"{adjective}-{noun}".lower(),
                name=f"{adjective} {noun}",
                category=CATEGORIES[i * 5 % len(CATEGORIES)],
                sso=i < SSO_COUNT,
                has_admin_role=i % 3 != 1,
            )
        )
    return tuple(apps)


APPS: tuple[App, ...] = _build_apps()
APP_BY_ID: dict[str, App] = {a.app_id: a for a in APPS}
SSO_IDS: tuple[str, ...] = tuple(a.app_id for a in APPS if a.sso)
NON_SSO_IDS: tuple[str, ...] = tuple(a.app_id for a in APPS if not a.sso)
BIRTHRIGHT_IDS: tuple[str, ...] = SSO_IDS[:BIRTHRIGHT_COUNT]


# ---- roles -------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Role:
    role_id: str
    department: str
    title: str
    team: str
    weight: int
    variants: tuple[str, ...]  # job titles HR might type
    sso_apps: tuple[str, ...]  # beyond birthright
    other_apps: tuple[str, ...]  # non-SSO apps


_RAW_ROLES: tuple[tuple[str, str, str, str, int, tuple[str, ...]], ...] = (
    ("eng-backend", "Engineering", "Backend Engineer", "Platform", 4,
     ("Backend Engineer", "Sr. Backend Eng", "Software Eng II (Backend)", "Server-side Dev")),
    ("eng-frontend", "Engineering", "Frontend Engineer", "Web", 3,
     ("Frontend Engineer", "Front-End Dev", "Software Engineer II, Web", "UI Engineer")),
    ("eng-sre", "Engineering", "Site Reliability Engineer", "Reliability", 2,
     ("Site Reliability Engineer", "SRE", "Sr SRE (on-call)", "DevOps Eng")),
    ("eng-data", "Engineering", "Data Engineer", "Data", 1,
     ("Data Engineer", "Sr. Data Eng", "Analytics Engineer", "Data Platform Eng II")),
    ("sales-ae", "Sales", "Account Executive", "Enterprise", 3,
     ("Senior Account Executive", "Sr. Acct Exec, EMEA", "AE (DACH)", "Account Exec II")),
    ("sales-sdr", "Sales", "Sales Development Representative", "Outbound", 3,
     ("SDR", "Sales Dev Rep", "Sales Development Rep (Inbound)", "BDR - Americas")),
    ("sales-ops", "Sales", "Sales Operations Analyst", "Revenue Operations", 1,
     ("Sales Ops Analyst", "RevOps Specialist", "Sales Operations Analyst II")),
    ("cs-csm", "Customer Success", "Customer Success Manager", "Accounts", 3,
     ("Customer Success Manager", "CSM - Enterprise", "Sr. CS Manager", "Client Success Mgr")),
    ("cs-support", "Customer Success", "Support Specialist", "Support", 4,
     ("Support Specialist", "Customer Support Rep", "Support Eng (Tier 2)", "Helpdesk Agent")),
    ("cs-onboarding", "Customer Success", "Onboarding Specialist", "Onboarding", 1,
     ("Onboarding Specialist", "Implementation Consultant", "Customer Onboarding Lead")),
    ("mkt-pmm", "Marketing", "Product Marketing Manager", "Product Marketing", 2,
     ("Product Marketing Manager", "PMM", "Sr. Product Mktg Mgr", "Product Marketing Lead")),
    ("mkt-content", "Marketing", "Content Lead", "Content", 2,
     ("Content Lead", "Content Marketing Mgr", "Sr Content Writer", "Editorial Lead")),
    ("mkt-growth", "Marketing", "Growth Marketer", "Growth", 2,
     ("Growth Marketer", "Demand Gen Manager", "Performance Marketing Specialist")),
    ("fin-analyst", "Finance", "Financial Analyst", "FP&A", 3,
     ("Financial Analyst", "Fin Analyst II", "FP&A Analyst", "Sr. Financial Analyst")),
    ("fin-accountant", "Finance", "Accountant", "Accounting", 2,
     ("Accountant", "Staff Accountant", "Sr Acct (AP/AR)")),
    ("ppl-partner", "People", "People Partner", "People Partners", 2,
     ("People Partner", "HR Business Partner", "Sr. HRBP", "People Ops Partner")),
    ("ppl-recruiter", "People", "Recruiter", "Talent", 2,
     ("Recruiter", "Technical Recruiter", "Talent Acquisition Partner", "Sr. Recruiter (Tech)")),
    ("it-lead", "IT", "IT Lead", "IT", 1, ("IT Lead", "Head of IT", "IT Manager")),
    ("it-engineer", "IT", "IT Engineer", "IT", 1,
     ("IT Engineer", "Sys Admin / IT Eng", "IT Support Engineer")),
    ("prod-pm", "Product", "Product Manager", "Product", 3,
     ("Product Manager", "Sr. PM", "Group Product Mgr", "Technical Product Manager")),
    ("prod-analyst", "Product", "Product Analyst", "Insights", 1,
     ("Product Analyst", "Product Data Analyst", "Sr Analyst, Product")),
    ("des-product", "Design", "Product Designer", "Product Design", 3,
     ("Product Designer", "Sr. UX Designer", "UX/UI Designer", "Interaction Designer")),
    ("des-brand", "Design", "Brand Designer", "Brand", 1,
     ("Brand Designer", "Visual Designer", "Sr. Graphic Designer")),
)  # fmt: skip


def _build_roles() -> tuple[Role, ...]:
    roles = []
    n = len(_RAW_ROLES)
    for idx, (role_id, dept, title, team, weight, variants) in enumerate(_RAW_ROLES):
        # 3-8 SSO apps beyond birthright, stepping through the 37 non-birthright SSO apps.
        rest = SSO_IDS[BIRTHRIGHT_COUNT:]
        sso = tuple(rest[(idx * 5 + k * 4) % len(rest)] for k in range(3 + idx % 6))
        # Every non-SSO app belongs to some role (index mod n), plus one shared extra each.
        own = [j for j in range(NON_SSO_COUNT) if j % n == idx]
        extra = (idx * 7 + 13) % NON_SSO_COUNT
        while extra in own:
            extra = (extra + 1) % NON_SSO_COUNT
        other = tuple(NON_SSO_IDS[j] for j in [*own, extra])
        roles.append(Role(role_id, dept, title, team, weight, variants, sso, other))
    return tuple(roles)


ROLES: tuple[Role, ...] = _build_roles()
ROLE_BY_ID: dict[str, Role] = {r.role_id: r for r in ROLES}
ROLES_BY_DEPT: dict[str, tuple[Role, ...]] = {
    d: tuple(r for r in ROLES if r.department == d) for d in DEPT_NAMES
}
