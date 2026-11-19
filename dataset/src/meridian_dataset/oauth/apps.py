"""The made-up third-party apps of the OAuth tuning world, and what IT and Security know.

Every app, publisher and client ID is invented; every client ID contains "-fake". Scopes
are real Google OAuth scope URLs, because the radar explains them.

Three groups:

- **Background apps** that many people use, each with an adoption rate per department and
  a share of grants nobody has used for a while. Five are approved by IT; the rest were never
  reviewed. None of them is planted as risky.
- **Planted apps**, each granted by exactly one person: the 6 risky grants. Which grants
  are planted, and why, is only in the answer key, never in a file the radar reads.
- **A banned app,** which one person still holds: a plant (OAR-009).
"""

from dataclasses import dataclass, field

G = "https://www.googleapis.com/auth/"
OPENID = "openid"
EMAIL = G + "userinfo.email"
PROFILE = G + "userinfo.profile"
SIGN_IN = (OPENID, EMAIL, PROFILE)

# What the app access list (Google) and a vendor directory would say about an app.
ESTABLISHED, YOUNG, UNKNOWN = "established", "young", "unknown"


@dataclass(frozen=True, slots=True)
class App:
    slug: str
    number: int
    name: str
    vendor: str
    category: str
    ai: bool
    verified_publisher: bool
    data_location: str
    vendor_maturity: str
    scopes: tuple[str, ...]
    # IT's catalogue: "approved", or None when IT never reviewed the app.
    it_status: str | None = None
    # Department -> chance a person there holds a grant; "*" is everyone else.
    adoption: dict[str, float] = field(default_factory=dict)
    # Share of this app's grants not used for more than 90 days.
    dormant_share: float = 0.0
    # False when the vendor directory has no entry for the app.
    in_vendor_directory: bool = True

    @property
    def client_id(self) -> str:
        return f"{self.number:012d}-fake{self.slug}.apps.googleusercontent.com"

    def rate(self, department: str) -> float:
        return self.adoption.get(department, self.adoption.get("*", 0.0))


AI_ASSISTANT = "ai assistant"

BACKGROUND: tuple[App, ...] = (
    App(
        "vellumine", 301, "Vellumine Assist", "Vellumine Labs", AI_ASSISTANT, True, True,
        "EU", ESTABLISHED, (*SIGN_IN, G + "calendar.readonly", G + "drive.readonly"),
        it_status="approved", adoption={"*": 0.45}, dormant_share=0.05,
    ),
    App(
        "huddlewave", 302, "Huddlewave Meet", "Huddlewave Inc.", "video meetings", False, True,
        "US", ESTABLISHED, (*SIGN_IN, G + "calendar.events"),
        it_status="approved", adoption={"*": 0.7}, dormant_share=0.1,
    ),
    App(
        "tallyfern", 303, "Tallyfern Expenses", "Tallyfern Ltd", "expenses", False, True,
        "EU", ESTABLISHED, (EMAIL, G + "gmail.readonly"),
        it_status="approved", adoption={"Finance": 0.9, "Sales": 0.3, "*": 0.02},
        dormant_share=0.1,
    ),
    App(
        "inkpact", 304, "Inkpact Sign", "Inkpact GmbH", "e-signature", False, True,
        "EU", ESTABLISHED, (EMAIL, G + "drive.file"),
        it_status="approved",
        adoption={"Sales": 0.4, "People": 0.4, "Finance": 0.4, "*": 0.05}, dormant_share=0.2,
    ),
    App(
        "kanbrook", 305, "Kanbrook Boards", "Kanbrook Oy", "project boards", False, True,
        "EU", ESTABLISHED, (*SIGN_IN, G + "drive.file"),
        it_status="approved", adoption={"Engineering": 0.3, "*": 0.1}, dormant_share=0.1,
    ),
    App(
        "sketchlattice", 306, "Sketchlattice Diagrams", "Sketchlattice", "diagrams", False,
        True, "US", ESTABLISHED, (EMAIL, G + "drive.file"),
        adoption={"Engineering": 0.25, "*": 0.03}, dormant_share=0.2,
    ),
    App(
        "slotwren", 307, "Slotwren Scheduler", "Slotwren", "scheduling", False, True,
        "US", YOUNG, (EMAIL, G + "calendar.events", G + "calendar.readonly"),
        adoption={"Sales": 0.15, "Customer Success": 0.15}, dormant_share=0.15,
    ),
    App(
        "pollquill", 308, "Pollquill Surveys", "Pollquill", "surveys", False, True,
        "US", ESTABLISHED, SIGN_IN, adoption={"*": 0.1}, dormant_share=0.4,
    ),
    App(
        "cheerboard", 309, "Cheerboard Kudos", "Cheerboard", "recognition", False, False,
        UNKNOWN, YOUNG, SIGN_IN, adoption={"*": 0.08}, dormant_share=0.3,
    ),
    App(
        "lessonmoss", 310, "Lessonmoss Learning", "Lessonmoss", "learning", False, True,
        "EU", ESTABLISHED, SIGN_IN, adoption={"*": 0.12}, dormant_share=0.3,
    ),
    App(
        "quillnest", 311, "Quillnest AI Writer", "Quillnest", AI_ASSISTANT, True, False,
        UNKNOWN, YOUNG, (EMAIL, G + "drive.file"),
        adoption={"Marketing": 0.3, "*": 0.02}, dormant_share=0.1,
    ),
    App(
        "sheetsmith", 312, "Sheetsmith Charts", "Sheetsmith", "charts", False, True,
        "US", ESTABLISHED, (EMAIL, G + "drive.file", G + "spreadsheets.readonly"),
        adoption={"Finance": 0.2, "Marketing": 0.1}, dormant_share=0.2,
    ),
    App(
        "echoloop", 313, "Echoloop Transcribe", "Echoloop", AI_ASSISTANT, True, True,
        "US", YOUNG, (EMAIL, G + "calendar.readonly", G + "drive.file"),
        adoption={"Customer Success": 0.12, "Sales": 0.12}, dormant_share=0.1,
    ),
)  # fmt: skip

# The apps of the 6 planted risky grants. Each is granted by one person only.
PLANTED: tuple[App, ...] = (
    App(
        "murmurlark", 321, "Murmurlark Notes AI", "Murmurlark", AI_ASSISTANT, True, False,
        UNKNOWN, YOUNG, (OPENID, G + "calendar.readonly", G + "drive"),
    ),
    App(
        "mergewick", 322, "Mergewick Mail Merge", "Mergewick", "mail merge", False, True,
        "US", YOUNG, (EMAIL, "https://mail.google.com/", G + "spreadsheets"),
    ),
    App(
        "draftmoth", 323, "Draftmoth AI Writer", "Draftmoth", AI_ASSISTANT, True, False,
        UNKNOWN, YOUNG, (EMAIL, G + "gmail.modify"),
    ),
    App(
        "scribblebee", 324, "Scribblebee Recorder", "Scribblebee", AI_ASSISTANT, True, False,
        UNKNOWN, YOUNG, (EMAIL, G + "calendar.readonly", G + "drive.readonly"),
    ),
    App(
        "synapsel", 325, "Synapsel Agent", "Synapsel Labs", AI_ASSISTANT, True, True,
        "US", YOUNG, (EMAIL, G + "drive.readonly"),
    ),
    App(
        "sortwhistle", 326, "Sortwhistle Inbox Rules", "Sortwhistle", "inbox rules", False,
        False, UNKNOWN, UNKNOWN, (EMAIL, G + "gmail.settings.basic", G + "gmail.settings.sharing"),
        in_vendor_directory=False,
    ),
)  # fmt: skip

BANNED = App(
    "notesprout", 331, "NoteSprout AI", "NoteSprout", AI_ASSISTANT, True, False,
    UNKNOWN, YOUNG, (OPENID, G + "calendar.readonly", G + "drive"),
)  # fmt: skip
BANNED_REASON = (
    "an AI note-taker, banned by IT on 2026-10-01 because it copies meetings and files to its "
    "own servers"
)

ALL_APPS: tuple[App, ...] = (*BACKGROUND, *PLANTED, BANNED)
