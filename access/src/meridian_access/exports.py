"""Read the messy CSV exports of the apps without SSO, with no help from the generator.

Everything is detected from the file itself: the encoding (BOM, utf-8, cp1252, latin-1),
the delimiter, where the header is (lines before it are a preamble), which lines after the
data are a footer, what each column means (a synonym table) and what each date format is.
A row is never silently dropped: it becomes an Account, is a blank row, is an exact
duplicate of an earlier row (counted), is a repeated header (counted), is a footer line (no
email- or username-looking cell), or is listed in `ExportsResult.unreadable` with a reason.
The body is read with `csv.reader` over the whole text, so a quoted newline stays inside its
row. For every file: lines = preamble + header + blank + footer + duplicates + repeated
headers + accounts + unreadable + continuation (the extra physical lines of multi-line rows).
Role and status labels are matched by token; a label that is not known is listed in the file
report and the field is left empty, never defaulted.
"""

import collections
import csv
import io
import re
import unicodedata
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

from meridian_access.models import Account
from meridian_access.policy.load import Policy

CANDIDATE_DELIMITERS = (",", ";", "\t", "|")

# Header text (normalised: lowercase, ASCII, non-alphanumerics to "_") -> field.
SYNONYMS: dict[str, tuple[str, ...]] = {
    "email": (
        "email",
        "e_mail",
        "email_address",
        "e_mail_address",
        "user_email",
        "useremail",
        "login",
        "work_email",
        "mail",
        "mail_adresse",
        "mailadresse",
        "email_adresse",
        "correo",
        "courriel",
        "upn",
        "user_principal_name",
        "primary_email",
        "login_email",
    ),
    "username": (
        "username",
        "user_name",
        "user",
        "handle",
        "userid",
        "user_id",
        "account",
        "account_name",
        "login_name",
        "benutzername",
        "nickname",
        "screen_name",
    ),
    "first_name": ("first", "first_name", "firstname", "given_name", "givenname", "vorname"),
    "last_name": (
        "last",
        "last_name",
        "lastname",
        "family_name",
        "familyname",
        "surname",
        "nachname",
    ),
    "full_name": ("full_name", "fullname"),
    "display_name": ("display_name", "displayname", "name", "user_name_display", "anzeigename"),
    "status": ("status", "state", "account_status", "user_status", "active", "enabled"),
    "role_level": (
        "role",
        "role_level",
        "permission",
        "permissions",
        "access_level",
        "access",
        "privilege",
        "level",
        "role_name",
        "rolle",
    ),
    "last_login": (
        "last_login",
        "last_active_at",
        "last_active",
        "last_seen",
        "last_sign_in",
        "lastlogin",
        "last_login_at",
        "letzter_login",
        "last_activity",
    ),
    "created": (
        "created",
        "added_on",
        "created_at",
        "date_added",
        "created_on",
        "added",
        "joined",
        "joined_on",
        "erstellt",
        "date_created",
    ),
    "department": ("department", "dept", "team", "abteilung"),
    "title": ("title", "job_title", "position"),
}
_FIELD_OF = {syn: fld for fld, syns in SYNONYMS.items() for syn in syns}
ROLE_TOKENS: dict[str, str] = {
    **dict.fromkeys(
        ("admin", "administrator", "administrateur", "owner", "super", "full"), "admin"
    ),
    **dict.fromkeys(
        ("member", "user", "standard", "basic", "viewer", "editor", "read", "write"), "member"
    ),
}
STATUS_TOKENS: dict[str, str] = {
    **dict.fromkeys(("active", "enabled", "yes"), "active"),
    **dict.fromkeys(("suspended", "disabled", "inactive", "locked"), "suspended"),
    **dict.fromkeys(("deprovisioned", "removed", "revoked", "deleted"), "deprovisioned"),
}
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
USERNAME_RE = re.compile(r"^[A-Za-z0-9._'-]{1,64}$")
_MONTHS = {
    name: i
    for i, names in enumerate(
        [
            ("jan", "january"),
            ("feb", "february"),
            ("mar", "march"),
            ("apr", "april"),
            ("may",),
            ("jun", "june"),
            ("jul", "july"),
            ("aug", "august"),
            ("sep", "sept", "september"),
            ("oct", "october"),
            ("nov", "november"),
            ("dec", "december"),
        ],
        1,
    )
    for name in names
}


@dataclass(frozen=True, slots=True)
class FileReport:
    file: str
    app_id: str
    encoding: str
    delimiter: str
    header_line: int  # 1-based; 0 when no header was found
    lines: int
    preamble: int
    footer: int
    blank: int
    duplicates: int
    accounts: int
    unreadable: int
    repeated_headers: int = 0
    continuation: int = 0  # extra physical lines of rows with a quoted newline
    unmapped_headers: tuple[str, ...] = ()  # header cells no field was found for
    unknown_labels: tuple[tuple[str, str], ...] = ()  # (field, label) left empty


@dataclass(frozen=True, slots=True)
class ExportsResult:
    accounts: tuple[Account, ...]
    unreadable: tuple[tuple[str, int, str], ...]  # (file, line number, reason)
    files: tuple[FileReport, ...]
    date_problems: tuple[tuple[str, int, str, str], ...]  # (file, line, column, reason)
    missing_apps: tuple[str, ...]  # non-SSO apps in the policy with no file
    unexpected_files: tuple[str, ...]  # files for no non-SSO app in the policy

    @property
    def duplicates(self) -> int:
        return sum(f.duplicates for f in self.files)

    @property
    def blank_rows(self) -> int:
        return sum(f.blank for f in self.files)


# ----------------------------------------------------------------------- decoding


def decode(data: bytes) -> tuple[str, str]:
    """(text, encoding name): a BOM decides; else utf-8, then cp1252, then latin-1."""
    if data.startswith((b"\xff\xfe\x00\x00", b"\x00\x00\xfe\xff")):
        return data.decode("utf-32"), "utf-32"
    if data.startswith((b"\xff\xfe", b"\xfe\xff")):
        return data.decode("utf-16"), "utf-16"
    if data.startswith(b"\xef\xbb\xbf"):
        return data.decode("utf-8-sig"), "utf-8-sig"
    if data[:200].count(b"\x00") > len(data[:200]) // 4 > 0:  # utf-16 without a BOM
        codec = "utf-16-le" if data[1:2] == b"\x00" else "utf-16-be"
        return data.decode(codec), codec
    for codec in ("utf-8", "cp1252"):
        try:
            return data.decode(codec), codec
        except UnicodeDecodeError:
            continue
    return data.decode("latin-1"), "latin-1"


def _fold(text: str) -> str:
    return unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")


def _header_key(cell: str) -> str:
    text = _fold(cell.replace("﻿", "")).strip().lower()
    return re.sub(r"[^a-z0-9]+", "_", text).strip("_")


def _split_lines(text: str) -> list[str]:
    lines = re.split(r"\r\n|\n|\r", text)
    if lines and lines[-1] == "":
        lines.pop()
    return lines


def _cells(line: str, delimiter: str) -> list[str]:
    try:
        rows = list(csv.reader([line], delimiter=delimiter))
    except csv.Error:
        return [line]
    return rows[0] if rows else []


def detect_delimiter(lines: list[str]) -> str:
    """csv.Sniffer proposes; the delimiter whose lines agree most on a column count wins,
    the Sniffer's pick breaking ties, then the order `, ; tab |`."""
    nonblank = [ln for ln in lines if ln.strip()]
    try:
        sniffed = (
            csv.Sniffer()
            .sniff("\n".join(nonblank[:200]), delimiters="".join(CANDIDATE_DELIMITERS))
            .delimiter
        )
    except csv.Error:
        sniffed = ""
    scores: dict[str, tuple[int, int]] = {}
    for d in CANDIDATE_DELIMITERS:
        counts = collections.Counter(len(_cells(ln, d)) for ln in nonblank)
        agreed = [(c, n) for n, c in counts.items() if n >= 2]
        scores[d] = max(agreed) if agreed else (0, 0)
    best = max(scores.values())
    if sniffed in scores and scores[sniffed] == best:
        return sniffed
    return next(d for d in CANDIDATE_DELIMITERS if scores[d] == best)


# ------------------------------------------------------------------------- dates


def _expand_year(y: int, digits: int) -> int:
    if digits > 2:
        return y
    return 2000 + y if y < 70 else 1900 + y


def _make(y: int, m: int, d: int) -> date | None:
    try:
        return date(y, m, d)
    except ValueError:
        return None


@dataclass(frozen=True, slots=True)
class _Scanned:
    """kind: "date" (value decided), "num" (a/b/year, order not known) or "bad"."""

    kind: str
    value: date | None = None
    a: int = 0
    b: int = 0
    year: int = 0


_BAD = _Scanned("bad")


# An optional time of day after a date: 09:00, 9:30:15.123, 9:30 PM, 09:00:00Z, +02:00.
_TIME = (
    r"(?:(?:\s+|T)\d{1,2}:\d{2}(?::\d{2}(?:[.,]\d+)?)?"
    r"(?:\s*[AaPp]\.?[Mm]\.?)?(?:\s*(?:Z|UTC|GMT|[+-]\d{2}:?\d{2}))?)?"
)


def scan_date(text: str) -> _Scanned:
    """Classify one cell: ISO, D Mon YYYY, an Excel serial, a Unix epoch, or a numeric
    day/month pair whose order the column has to decide. Every text format may be
    followed by a time of day."""
    s = text.strip()
    if not s:
        return _BAD
    if m := re.fullmatch(rf"(\d{{4}})[-/.](\d{{1,2}})[-/.](\d{{1,2}}){_TIME}", s):
        d = _make(int(m[1]), int(m[2]), int(m[3]))
        return _Scanned("date", d) if d else _BAD
    if m := re.fullmatch(
        rf"(\d{{1,2}})[ \-./]([A-Za-z]{{3,9}})\.?[ \-./,]+(\d{{2}}|\d{{4}}){_TIME}", s
    ):
        month = _MONTHS.get(m[2].lower())
        d = _make(_expand_year(int(m[3]), len(m[3])), month, int(m[1])) if month else None
        return _Scanned("date", d) if d else _BAD
    if m := re.fullmatch(rf"([A-Za-z]{{3,9}})\.? (\d{{1,2}}),? (\d{{4}}){_TIME}", s):
        month = _MONTHS.get(m[1].lower())
        d = _make(int(m[3]), month, int(m[2])) if month else None
        return _Scanned("date", d) if d else _BAD
    if m := re.fullmatch(rf"(\d{{1,2}})[/.\-](\d{{1,2}})[/.\-](\d{{2}}|\d{{4}}){_TIME}", s):
        return _Scanned("num", None, int(m[1]), int(m[2]), _expand_year(int(m[3]), len(m[3])))
    if m := re.fullmatch(r"(\d{4})(\d{2})(\d{2})(?:T?\d{4,6}Z?)?", s):
        d = _make(int(m[1]), int(m[2]), int(m[3]))
        if d:
            return _Scanned("date", d)
    if m := re.fullmatch(r"(\d+)(?:\.\d+)?", s):
        n = int(m[1])
        if 20000 <= n <= 80000:  # Excel serial: days since 1899-12-30
            return _Scanned("date", date(1899, 12, 30) + timedelta(days=n))
        if 10**8 <= n < 10**11:  # Unix seconds
            return _Scanned("date", date(1970, 1, 1) + timedelta(seconds=n))
        if 10**11 <= n < 10**14:  # Unix milliseconds
            return _Scanned("date", date(1970, 1, 1) + timedelta(seconds=n // 1000))
    return _BAD


def column_order(values: list[str]) -> tuple[str | None, str]:
    """For the numeric day/month cells of one column: ("dmy"|"mdy"|"any", "") when the
    column decides it, else (None, reason). "any" means every cell reads the same either way."""
    nums = [s for s in (scan_date(v) for v in values) if s.kind == "num"]
    if not nums:
        return "any", ""
    dmy = any(s.a > 12 for s in nums)
    mdy = any(s.b > 12 for s in nums)
    if dmy and mdy:
        return None, "mixed day/month order in one column"
    if dmy:
        return "dmy", ""
    if mdy:
        return "mdy", ""
    if all(s.a == s.b for s in nums):
        return "any", ""
    return None, "ambiguous: every value fits both DD/MM and MM/DD"


def to_iso(text: str, order: str) -> str | None:
    """ISO date for a cell, given the column's order; None if it can't be read."""
    s = scan_date(text)
    if s.kind == "date" and s.value:
        return s.value.isoformat()
    if s.kind == "num":
        d = _make(s.year, s.b, s.a) if order in ("dmy", "any") else _make(s.year, s.a, s.b)
        return d.isoformat() if d else None
    return None


# ------------------------------------------------------------------------- files


def _tokens(key: str) -> set[str]:
    return set(key.split("_")) - {""}


def _field_for(cell: str) -> str | None:
    """The field a header cell stands for: the synonym table, then a token fallback on short
    headings (so "E-mail (work)" and "Last seen at" are still understood)."""
    key = _header_key(cell)
    if not key:
        return None
    if key in _FIELD_OF:
        return _FIELD_OF[key]
    toks = _tokens(key)
    if len(toks) > 4:
        return None  # a sentence, not a column heading
    if any("email" in t or "mail" in t for t in toks):
        return "email"
    if "last" in toks and toks & {"login", "seen", "active", "activity", "signin"}:
        return "last_login"
    if "user" in toks and toks & {"name", "id", "login"}:
        return "username"
    if toks & {"created", "added", "since", "joined"}:
        return "created"
    if toks & {"surname"} or ("last" in toks and "name" in toks) or "family" in toks:
        return "last_name"
    if "first" in toks or "given" in toks:
        return "first_name"
    if "full" in toks and "name" in toks:
        return "full_name"
    if "display" in toks and "name" in toks:
        return "display_name"
    if toks & {"status", "state"}:
        return "status"
    if toks & {"role", "permission", "permissions", "privilege", "access", "level"}:
        return "role_level"
    if toks & {"department", "dept", "team"}:
        return "department"
    if toks & {"title", "position"}:
        return "title"
    return None


def _field_map(cells: list[str]) -> dict[int, str]:
    mapped: dict[int, str] = {}
    seen: set[str] = set()
    for i, cell in enumerate(cells):
        fld = _field_for(cell)
        if fld and fld not in seen:
            mapped[i] = fld
            seen.add(fld)
    return mapped


def _find_header(lines: list[str], delimiter: str) -> tuple[int, dict[int, str]] | None:
    for need in (2, 1):
        for i, line in enumerate(lines):
            if not line.strip():
                continue
            mapped = _field_map(_cells(line, delimiter))
            fields = set(mapped.values())
            if fields & {"email", "username"} and len(fields) >= need:
                return i, mapped
    return _header_by_content(lines, delimiter)


def _header_by_content(lines: list[str], delimiter: str) -> tuple[int, dict[int, str]] | None:
    """A header whose names we don't know: the line just above rows where one column is
    mostly email addresses. That column is the email."""
    for i, line in enumerate(lines[:-1]):
        cells = _cells(line, delimiter)
        if not line.strip() or "@" in line:
            continue
        rows = [_cells(ln, delimiter) for ln in lines[i + 1 : i + 8] if ln.strip()]
        rows = [r for r in rows if len(r) == len(cells)]
        for col in range(len(cells)):
            values = [r[col] for r in rows if r[col].strip()]
            if len(values) >= 2 and sum("@" in v for v in values) * 2 > len(values):
                return i, {col: "email"}
    return None


def _label(value: str, table: dict[str, str]) -> str | None:
    """The one value the label's tokens map to; None when no token or a mix is found."""
    found = {table[t] for t in re.findall(r"[a-z]+", _fold(value).lower()) if t in table}
    return found.pop() if len(found) == 1 else None


def _physical_lines(text: str) -> list[tuple[str, str]]:
    """(content, line terminator) pairs; the same line splitting as `_split_lines`."""
    out = [(m[1], m[2]) for m in re.finditer(r"([^\r\n]*)(\r\n|\n|\r|\Z)", text)]
    if out and out[-1] == ("", ""):
        out.pop()
    return out


def _looks_like_data(cells: list[str]) -> bool:
    return any(EMAIL_RE.match(c.strip()) or USERNAME_RE.match(c.strip()) for c in cells)


def parse_file(path: Path) -> tuple[list[Account], list[tuple[str, int, str]], FileReport, list]:
    """Parse one export. Returns accounts, unreadable lines, a report and date problems."""
    name = path.name
    app_id = path.stem
    text, encoding = decode(path.read_bytes())
    physical = _physical_lines(text)
    lines = [c for c, _ in physical]
    delimiter = detect_delimiter(lines)
    found = _find_header(lines, delimiter)
    unreadable: list[tuple[str, int, str]] = []
    if found is None:
        for n, line in enumerate(lines, 1):
            if line.strip():
                unreadable.append((name, n, "no header row found"))
        blank = sum(1 for ln in lines if not ln.strip())
        report = FileReport(
            name, app_id, encoding, delimiter, 0, len(lines), 0, 0, blank, 0, 0, len(unreadable)
        )
        return [], unreadable, report, []
    header_index, fmap = found
    header_cells = [c.strip() for c in _cells(lines[header_index], delimiter)]
    n_cols = len(header_cells)
    header_key = [c.casefold() for c in header_cells]
    unmapped = tuple(h for i, h in enumerate(header_cells) if i not in fmap and h)

    # The body, with csv.reader over all of it so a quoted newline stays inside its row.
    body_text = "".join(c + t for c, t in physical[header_index + 1 :])
    reader = csv.reader(io.StringIO(body_text, newline=""), delimiter=delimiter)
    records: list[tuple[int, int, list[str]]] = []  # (first line, last line, cells)
    previous = 0
    try:
        for cells in reader:
            records.append((previous + 1, reader.line_num, cells))
            previous = reader.line_num
    except csv.Error as exc:  # e.g. a NUL byte: the rest of the file is listed, not dropped
        for n, (content, _) in enumerate(physical[header_index + 1 + previous :], previous + 1):
            if content.strip():
                unreadable.append((name, header_index + 1 + n, f"unreadable text: {exc}"))
        previous = len(physical) - header_index - 1
    base = header_index + 1  # file line number of body line k is base + k
    is_blank = [not any(c.strip() for c in cells) for _, _, cells in records]
    # The footer: the trailing records (blank ones included) with no email- or
    # username-looking cell. Anything that does look like data is a row, or unreadable.
    tail = len(records)
    while tail > 0 and (is_blank[tail - 1] or not _looks_like_data(records[tail - 1][2])):
        tail -= 1
    footer = blank = duplicates = repeated = continuation = 0
    rows: list[tuple[int, list[str]]] = []
    seen: set[tuple[str, ...]] = set()
    for i, (first_line, last_line, cells) in enumerate(records):
        line_no = base + first_line
        continuation += last_line - first_line
        if is_blank[i]:
            blank += 1
            continue
        if i >= tail:
            footer += 1
            continue
        stripped = [c.strip() for c in cells]
        if [c.casefold() for c in stripped] == header_key:
            repeated += 1
            continue
        if len(cells) != n_cols:
            unreadable.append((name, line_no, f"{len(cells)} cells but the header has {n_cols}"))
            continue
        key = tuple(stripped)
        if key in seen:
            duplicates += 1
            continue
        seen.add(key)
        rows.append((line_no, stripped))

    by_field = {fld: i for i, fld in fmap.items()}
    # A header we only understood by its name may have missed the email column.
    if "email" not in by_field and "username" not in by_field:
        for col in range(n_cols):
            values = [r[col] for _, r in rows if r[col]]
            if col not in fmap and values and sum("@" in v for v in values) * 2 > len(values):
                by_field["email"] = col
                unmapped = tuple(h for h in unmapped if h != header_cells[col])
                break
    date_cols = [c for c in ("created", "last_login") if c in by_field]
    order: dict[str, str | None] = {}
    why: dict[str, str] = {}
    for col in date_cols:
        order[col], why[col] = column_order([r[by_field[col]] for _, r in rows])
    decided = [o for o in order.values() if o in ("dmy", "mdy")]
    for col in date_cols:
        if order[col] is None and decided and len(set(decided)) == 1:
            order[col], why[col] = decided[0], ""  # settled by the file's other date column

    accounts: list[Account] = []
    problems: list[tuple[str, int, str, str]] = []
    unknown: set[tuple[str, str]] = set()

    def cell(r: list[str], fld: str) -> str:
        return r[by_field[fld]] if fld in by_field else ""

    for line_no, r in rows:
        email_cell, user_cell = cell(r, "email"), cell(r, "username")
        identity = email_cell or user_cell
        if not identity:
            unreadable.append((name, line_no, "no email or username"))
            continue
        if not (EMAIL_RE.match(identity) or USERNAME_RE.match(identity)):
            unreadable.append((name, line_no, f"{identity[:40]!r} is not an email or a username"))
            continue
        email = email_cell.lower() if "@" in email_cell else ""
        dates: dict[str, str] = {}
        for col in ("created", "last_login"):
            raw = cell(r, col)
            if not raw:
                dates[col] = ""
            elif order.get(col) is None and scan_date(raw).kind == "num":
                dates[col] = ""
                problems.append((name, line_no, col, why[col]))
            else:
                iso = to_iso(raw, order.get(col) or "any")
                dates[col] = iso or ""
                if iso is None:
                    problems.append((name, line_no, col, f"unreadable date {raw!r}"))
        role_raw, status_raw = cell(r, "role_level"), cell(r, "status")
        role = _label(role_raw, ROLE_TOKENS) if role_raw else ""
        if role is None:
            role = ""
            unknown.add(("role_level", role_raw))
        if "status" not in by_field:
            state = "active"  # no status column: being in the export is all we know
        else:
            state = _label(status_raw, STATUS_TOKENS) if status_raw else ""
            if state is None:
                state = ""
                unknown.add(("status", status_raw))
        first, last = cell(r, "first_name"), cell(r, "last_name")
        accounts.append(
            Account(
                system=app_id,
                account=identity,
                email=email,
                given=first,
                family=last,
                display=cell(r, "display_name") or cell(r, "full_name"),
                state=state,  # type: ignore[arg-type]
                app_id=app_id,
                role=role,
                created=dates["created"],
                last_login=dates["last_login"],
                source_ref=f"{name}:{line_no}",
            )
        )
    report = FileReport(
        name,
        app_id,
        encoding,
        delimiter,
        header_index + 1,
        len(lines),
        header_index,
        footer,
        blank,
        duplicates,
        len(accounts),
        len(unreadable),
        repeated,
        continuation,
        unmapped,
        tuple(sorted(unknown)),
    )
    return accounts, unreadable, report, problems


def parse_exports(exports_dir: Path, policy: Policy) -> ExportsResult:
    """Read every `*.csv` in exports_dir. The policy says which apps are non-SSO."""
    accounts: list[Account] = []
    unreadable: list[tuple[str, int, str]] = []
    reports: list[FileReport] = []
    problems: list[tuple[str, int, str, str]] = []
    for path in sorted(Path(exports_dir).glob("*.csv")):
        a, u, report, p = parse_file(path)
        accounts += a
        unreadable += u
        reports.append(report)
        problems += p
    expected = {app.id for app in policy.apps.values() if not app.sso}
    have = {r.app_id for r in reports}
    return ExportsResult(
        accounts=tuple(accounts),
        unreadable=tuple(unreadable),
        files=tuple(reports),
        date_problems=tuple(problems),
        missing_apps=tuple(sorted(expected - have)),
        unexpected_files=tuple(sorted(f"{a}.csv" for a in have - expected)),
    )
