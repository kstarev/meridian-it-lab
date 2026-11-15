"""The CSV export parser: encodings, delimiters, preambles, dates, and never dropping a row."""

from datetime import date, timedelta

import pytest

from meridian_access.exports import (
    column_order,
    decode,
    parse_exports,
    parse_file,
    scan_date,
    to_iso,
)

# ------------------------------------------------------------------ the tuning world


@pytest.fixture(scope="module")
def parsed(world_dir, policy):
    return parse_exports(world_dir / "exports", policy)


def test_the_tuning_exports_have_nothing_unreadable(parsed):
    assert parsed.unreadable == ()
    assert len(parsed.files) == 95
    assert parsed.missing_apps == () and parsed.unexpected_files == ()


def test_every_file_row_count_matches_the_generator(world, parsed):
    reports = {f.file: f for f in parsed.files}
    for export in world.exports:
        report = reports[export.file_name]
        wanted = {
            tuple(c.strip() for c in row) for row in export.rows if any(c.strip() for c in row)
        }
        assert report.accounts == len(wanted), export.file_name
        assert report.preamble == len(export.preamble) and report.footer == len(export.footer)
        assert report.header_line == len(export.preamble) + 1
        assert report.encoding.replace("-sig", "") in (export.encoding.replace("-sig", ""), "utf-8")


# The known ambiguous cells: five `created` values in one file where every value of the
# column fits both DD/MM and MM/DD, so the parser reports them and leaves them empty.
KNOWN_AMBIGUOUS_DATE_CELLS = 5


def test_the_only_date_problems_are_the_known_ambiguous_cells(parsed):
    assert len(parsed.date_problems) == KNOWN_AMBIGUOUS_DATE_CELLS
    assert {(col, why[:9]) for _, _, col, why in parsed.date_problems} == {("created", "ambiguous")}
    cells = {(f, n) for f, n, _, _ in parsed.date_problems}
    guessed = [
        a
        for a in parsed.accounts
        if (a.source_ref.split(":")[0], int(a.source_ref.split(":")[1])) in cells
    ]
    assert len(guessed) == KNOWN_AMBIGUOUS_DATE_CELLS
    assert all(a.created == "" for a in guessed)  # reported, never guessed


def test_the_tuning_exports_use_only_known_labels_and_headers(parsed):
    assert all(not f.unknown_labels and not f.unmapped_headers for f in parsed.files)
    assert all(f.repeated_headers == 0 and f.continuation == 0 for f in parsed.files)


def test_every_line_is_accounted_for(parsed):
    for f in parsed.files:
        assert f.lines == (
            f.preamble
            + 1
            + f.footer
            + f.blank
            + f.duplicates
            + f.repeated_headers
            + f.accounts
            + f.unreadable
            + f.continuation
        )
    assert len(parsed.accounts) == sum(f.accounts for f in parsed.files)
    assert parsed.blank_rows == sum(f.blank for f in parsed.files)


def test_the_encodings_and_delimiters_are_detected(world, parsed):
    by_file = {f.file: f for f in parsed.files}
    assert {f.encoding for f in parsed.files} == {"utf-8", "utf-8-sig", "cp1252"}
    for export in world.exports:
        assert by_file[export.file_name].delimiter == export.delimiter


def test_accounts_come_from_the_file_as_written(world, parsed):
    mixed = [a for a in parsed.accounts if a.email and a.account != a.account.lower()]
    assert mixed and all(a.email == a.account.lower() for a in mixed)
    assert all(a.account == a.account.strip() and a.app_id == a.system for a in parsed.accounts)
    assert {a.role for a in parsed.accounts} <= {"", "member", "admin"}
    assert all(a.created == "" or len(a.created) == 10 for a in parsed.accounts)


# ----------------------------------------------------------------------------- dates


@pytest.mark.parametrize(
    "text,expected",
    [
        ("2026-11-03", "2026-11-03"),
        ("2026-11-03T09:00:00.000Z", "2026-11-03"),
        ("3 Nov 2026", "2026-11-03"),
        ("03 November 2026", "2026-11-03"),
        ("Nov 3, 2026", "2026-11-03"),
        ("20261103", "2026-11-03"),
        ("46329", (date(1899, 12, 30) + timedelta(days=46329)).isoformat()),
        ("46329.0", (date(1899, 12, 30) + timedelta(days=46329)).isoformat()),
        ("1793664000", "2026-11-03"),
        ("1793664000000", "2026-11-03"),
        # a time of day after any text format
        ("2026-11-03 09:15", "2026-11-03"),
        ("2026-11-03 9:15:30 PM", "2026-11-03"),
        ("2026-11-03T09:00:00+02:00", "2026-11-03"),
        ("3 Nov 2026 14:05", "2026-11-03"),
        ("Nov 3, 2026 2:05 pm", "2026-11-03"),
        ("20261103T090000Z", "2026-11-03"),
        ("03.11.2026", None),  # DD.MM.YYYY: day first only once the column says so
    ],
)
def test_unambiguous_date_formats(text, expected):
    if expected is None:
        assert scan_date(text).kind == "num"
        return
    assert scan_date(text).kind == "date"
    assert to_iso(text, "any") == expected


@pytest.mark.parametrize(
    "text,order,expected",
    [
        ("31.12.2026", "dmy", "2026-12-31"),
        ("03.11.2026", "dmy", "2026-11-03"),
        ("03.11.2026 09:15", "dmy", "2026-11-03"),
        ("11/03/2026 9:15 AM", "mdy", "2026-11-03"),
        ("03/11/2026 09:15:00", "dmy", "2026-11-03"),
        ("03-11-2026T09:15:00Z", "dmy", "2026-11-03"),
    ],
)
def test_numeric_dates_with_a_dot_or_a_time(text, order, expected):
    assert to_iso(text, order) == expected


def test_dmy_is_chosen_when_a_first_part_is_over_12():
    assert column_order(["13/01/2026", "02/03/2026"]) == ("dmy", "")
    assert to_iso("02/03/2026", "dmy") == "2026-03-02"


def test_mdy_is_chosen_when_a_second_part_is_over_12():
    assert column_order(["01/13/2026", "02/03/2026"]) == ("mdy", "")
    assert to_iso("02/03/2026", "mdy") == "2026-02-03"


def test_mixed_orders_and_all_ambiguous_columns_are_reported():
    order, why = column_order(["13/01/2026", "01/13/2026"])
    assert order is None and "mixed" in why
    order, why = column_order(["01/02/2026", "03/04/2026"])
    assert order is None and "ambiguous" in why
    assert column_order(["05/05/2026", "11/11/2026"])[0] == "any"  # same either way
    assert column_order([""])[0] == "any"


def test_unreadable_dates_are_bad():
    for text in ("", "soon", "31/31/2026", "13/13/2026x"):
        assert to_iso(text, "dmy") is None


def _write(tmp_path, name, content, encoding="utf-8"):
    path = tmp_path / name
    path.write_bytes(content.encode(encoding))
    return path


def test_an_ambiguous_column_is_settled_by_the_files_other_date_column(tmp_path):
    path = _write(
        tmp_path,
        "app-one.csv",
        "Email,Created,Last Login\r\n"
        "a@x.example,01/02/2026,25/02/2026\r\n"
        "b@x.example,03/04/2026,10/02/2026\r\n",
    )
    accounts, unreadable, _, problems = parse_file(path)
    assert not unreadable and not problems
    assert [(a.created, a.last_login) for a in accounts] == [
        ("2026-02-01", "2026-02-25"),
        ("2026-04-03", "2026-02-10"),
    ]


def test_an_ambiguous_file_is_reported_and_keeps_its_rows(tmp_path):
    path = _write(
        tmp_path,
        "app-one.csv",
        "Email,Created\r\na@x.example,01/02/2026\r\nb@x.example,03/04/2026\r\n",
    )
    accounts, unreadable, report, problems = parse_file(path)
    assert len(accounts) == 2 and not unreadable and report.accounts == 2
    assert [p[2] for p in problems] == ["created", "created"]
    assert all(a.created == "" for a in accounts)


# ----------------------------------------------------------------------- messy files


def test_decode_detects_boms_and_falls_back():
    assert decode("a;b\n".encode("utf-8-sig")) == ("a;b\n", "utf-8-sig")
    assert decode("Müller".encode("utf-16"))[1] == "utf-16"
    assert decode("Müller".encode("utf-16"))[0] == "Müller"
    assert decode("Müller".encode("cp1252")) == ("Müller", "cp1252")
    assert decode("Müller".encode()) == ("Müller", "utf-8")
    assert decode(b"caf\x81")[1] == "latin-1"  # 0x81 is undefined in cp1252
    assert decode("Müller".encode("utf-16-le"))[1] == "utf-16-le"


@pytest.mark.parametrize("delimiter", [",", ";", "\t", "|"])
def test_every_candidate_delimiter(tmp_path, delimiter):
    rows = [
        ["Email", "Name", "Role"],
        ["a@x.example", "Ann Lee", "Admin"],
        ["b@x.example", "Bo Li", "Member"],
    ]
    text = "".join(delimiter.join(r) + "\r\n" for r in rows)
    accounts, unreadable, report, _ = parse_file(_write(tmp_path, "app-one.csv", text))
    assert report.delimiter == delimiter and not unreadable
    assert [(a.email, a.role) for a in accounts] == [
        ("a@x.example", "admin"),
        ("b@x.example", "member"),
    ]


def test_preamble_footer_blank_rows_duplicates_and_encoding(tmp_path):
    text = (
        "Export by the vendor, version 3\r\n"
        "Report: all users\r\n"
        "\r\n"
        "E-Mail;Vorname;Nachname;Status;Letzter Login\r\n"
        "  Ann.Lee@X.example ;Ann;Lee;Active;3 Nov 2026\r\n"
        ";;;;\r\n"
        "Bo.Li@x.example;Bo;Li;Locked;\r\n"
        "Ann.Lee@X.example;Ann;Lee;Active;3 Nov 2026\r\n"
        "Zoë.Müller@x.example;Zoë;Müller;Active;4 Nov 2026\r\n"
        "\r\n"
        "End of report\r\n"
    )
    accounts, unreadable, report, _ = parse_file(_write(tmp_path, "app-one.csv", text, "utf-16"))
    assert report.encoding == "utf-16" and report.delimiter == ";"
    assert (report.preamble, report.footer, report.duplicates) == (3, 1, 1)
    assert report.blank == 2 and not unreadable
    assert [a.email for a in accounts] == [
        "ann.lee@x.example",
        "bo.li@x.example",
        "zoë.müller@x.example",
    ]
    assert accounts[0].account == "Ann.Lee@X.example"
    assert [a.state for a in accounts] == ["active", "suspended", "active"]
    assert accounts[0].last_login == "2026-11-03"
    assert report.lines == 3 + 1 + 1 + 2 + 1 + 3 + 0


def test_a_bad_row_is_listed_never_dropped(tmp_path):
    text = (
        "Username,First,Last\r\n"
        "asmith,Ann,Smith\r\n"
        "broken,row\r\n"
        ",Nobody,Here\r\n"
        "bjones,Bo,Jones\r\n"
    )
    accounts, unreadable, report, _ = parse_file(_write(tmp_path, "app-one.csv", text))
    assert [a.account for a in accounts] == ["asmith", "bjones"]
    assert [(u[1], u[2][:9]) for u in unreadable] == [(3, "2 cells b"), (4, "no email ")]
    assert report.lines == 1 + report.accounts + report.unreadable


def test_a_file_without_a_header_is_all_unreadable(tmp_path):
    accounts, unreadable, report, _ = parse_file(
        _write(tmp_path, "app-one.csv", "hello\r\nworld\r\n")
    )
    assert not accounts and len(unreadable) == 2 and report.header_line == 0


def test_the_synonym_table_reads_other_headings(tmp_path):
    text = (
        "Work Email,Given Name,Surname,Access Level,Date Added\r\n"
        "a@x.example,Ann,Lee,Administrator,2026-11-01\r\n"
    )
    (a,), _, _, _ = parse_file(_write(tmp_path, "app-one.csv", text))
    assert (a.given, a.family, a.role, a.created) == ("Ann", "Lee", "admin", "2026-11-01")


def test_missing_and_unexpected_apps_are_reported(tmp_path, policy):
    _write(tmp_path, "not-an-app.csv", "Email\r\na@x.example\r\n")
    result = parse_exports(tmp_path, policy)
    assert result.unexpected_files == ("not-an-app.csv",)
    assert len(result.missing_apps) == 95


# ------------------------------------------------------- rows, footers, labels, headers


def test_a_quoted_newline_stays_inside_its_row_and_line_numbers_follow(tmp_path):
    text = (
        "Email,Name,Role\r\n"
        'a@x.example,"Ann\r\nLee",Admin\r\n'
        "b@x.example,Bo Li,Member\r\n"
        "broken,row\r\n"
    )
    accounts, unreadable, report, _ = parse_file(_write(tmp_path, "app-one.csv", text))
    assert [(a.email, a.display) for a in accounts] == [
        ("a@x.example", "Ann\r\nLee"),
        ("b@x.example", "Bo Li"),
    ]
    assert [a.source_ref for a in accounts] == ["app-one.csv:2", "app-one.csv:4"]
    assert [(u[1], u[2][:7]) for u in unreadable] == [(5, "2 cells")]
    assert report.continuation == 1
    assert report.lines == 1 + report.accounts + report.unreadable + report.continuation


def test_a_trailing_line_with_an_email_or_username_is_unreadable_not_a_footer(tmp_path):
    text = (
        "Username,First,Last\r\n"
        "asmith,Ann,Smith\r\n"
        "bjones,Bo\r\n"  # a truncated last row
        "Total: 2\r\n"
    )
    accounts, unreadable, report, _ = parse_file(_write(tmp_path, "app-one.csv", text))
    assert [a.account for a in accounts] == ["asmith"]
    assert [(u[1], u[2][:7]) for u in unreadable] == [(3, "2 cells")]
    assert report.footer == 1
    text = "Email,Name\r\na@x.example,Ann\r\nb@x.example\r\n"  # a bare address as the last line
    _, unreadable, report, _ = parse_file(_write(tmp_path, "app-two.csv", text))
    assert len(unreadable) == 1 and report.footer == 0


def test_a_one_column_file_does_not_turn_footer_lines_into_accounts(tmp_path):
    text = "Email\r\na@x.example\r\nb@x.example\r\n\r\nTotal: 3\r\nEnd of report\r\n"
    accounts, unreadable, report, _ = parse_file(_write(tmp_path, "app-one.csv", text))
    assert [a.account for a in accounts] == ["a@x.example", "b@x.example"]
    assert not unreadable and report.footer == 2 and report.blank == 1
    # in the middle of the file such a line is not an account either
    text = "Email\r\na@x.example\r\nTotal: 3\r\nb@x.example\r\n"
    accounts, unreadable, _, _ = parse_file(_write(tmp_path, "app-two.csv", text))
    assert [a.account for a in accounts] == ["a@x.example", "b@x.example"]
    assert [(u[1], "not an email" in u[2]) for u in unreadable] == [(3, True)]


def test_a_repeated_header_is_skipped_and_counted(tmp_path):
    text = (
        "Email,Name\r\na@x.example,Ann\r\nEmail,Name\r\nb@x.example,Bo\r\n"
        "EMAIL,name\r\nc@x.example,Cy\r\n"
    )
    accounts, unreadable, report, _ = parse_file(_write(tmp_path, "app-one.csv", text))
    assert [a.account for a in accounts] == ["a@x.example", "b@x.example", "c@x.example"]
    assert report.repeated_headers == 2 and not unreadable


def test_the_identity_cell_must_look_like_an_email_or_a_username(tmp_path):
    text = (
        "Email,Name\r\n"
        "a@x.example,Ann\r\n"
        "N/A,Nobody\r\n"
        "Ann Lee,Ann\r\n"
        "a@b,Short\r\n"
        "O'Neil.x,Pat\r\n"
        "two@@x.example,Bad\r\n"
    )
    accounts, unreadable, _, _ = parse_file(_write(tmp_path, "app-one.csv", text))
    assert [a.account for a in accounts] == ["a@x.example", "O'Neil.x"]
    assert [u[1] for u in unreadable] == [3, 4, 5, 7]


def test_labels_are_matched_by_token_and_unknown_ones_are_listed(tmp_path):
    text = (
        "Email,Role,Status\r\n"
        "a@x.example,Super Admin,Active\r\n"
        "b@x.example,Standard user,Account disabled\r\n"
        "c@x.example,Full access,Removed\r\n"
        "d@x.example,Gast,pending\r\n"
        "e@x.example,Admin member,Enabled\r\n"
        "f@x.example,Viewer,Locked\r\n"
        "g@x.example,,\r\n"
    )
    accounts, unreadable, report, _ = parse_file(_write(tmp_path, "app-one.csv", text))
    assert not unreadable
    assert [(a.role, a.state) for a in accounts] == [
        ("admin", "active"),
        ("member", "suspended"),
        ("admin", "deprovisioned"),
        ("", ""),  # unknown labels: left empty, never defaulted
        ("", "active"),  # "Admin member" mixes both: unknown role
        ("member", "suspended"),
        ("", ""),
    ]
    assert report.unknown_labels == (
        ("role_level", "Admin member"),
        ("role_level", "Gast"),
        ("status", "pending"),
    )


def test_headers_are_understood_by_token_and_by_content(tmp_path):
    text = (
        "Employee E-mail (work),Given,Family Name,Last seen at,Member since,Cost centre\r\n"
        "a@x.example,Ann,Lee,2026-11-03,2026-01-02,C1\r\n"
    )
    (a,), _, report, _ = parse_file(_write(tmp_path, "app-one.csv", text))
    assert (a.email, a.given, a.family, a.last_login, a.created) == (
        "a@x.example",
        "Ann",
        "Lee",
        "2026-11-03",
        "2026-01-02",
    )
    assert report.unmapped_headers == ("Cost centre",)
    # nothing in the header says "email", but the column is full of addresses
    text = "Zugang,Kuerzel\r\nann@x.example,AL\r\nbo@x.example,BL\r\n"
    accounts, unreadable, report, _ = parse_file(_write(tmp_path, "app-two.csv", text))
    assert [a.email for a in accounts] == ["ann@x.example", "bo@x.example"] and not unreadable
    assert report.header_line == 1 and report.unmapped_headers == ("Kuerzel",)
    # ...and a header found by name, whose email column has an unknown name
    text = "Name,Kuerzel,Zugang\r\nAnn,AL,ann@x.example\r\nBo,BL,bo@x.example\r\n"
    accounts, _, report, _ = parse_file(_write(tmp_path, "app-three.csv", text))
    assert [a.email for a in accounts] == ["ann@x.example", "bo@x.example"]


# ------------------------------------------------------------------------------ fuzz


def _variants(rng):
    return {
        "delimiter": rng.choice([",", ";", "\t", "|"]),
        "encoding": rng.choice(["utf-8", "utf-8-sig", "utf-16", "cp1252"]),
        "quote_all": rng.random() < 0.5,
        "preamble": rng.sample(
            ["Generated by the vendor", "Report: all accounts, v3", "Confidential", ""],
            rng.randint(0, 3),
        ),
        "footer": rng.sample(["Total: 3", "End of report", "-- generated --"], rng.randint(0, 2)),
        "repeat_header": rng.random() < 0.5,
        "bad_last_row": rng.random() < 0.5,
        "one_column": rng.random() < 0.25,
    }


def _render(header, rows, v, identity):
    import csv
    import io

    if v["one_column"]:
        header, rows = [header[identity]], [[r[identity]] for r in rows]
    out = io.StringIO()
    w = csv.writer(
        out,
        delimiter=v["delimiter"],
        quoting=csv.QUOTE_ALL if v["quote_all"] else csv.QUOTE_MINIMAL,
        lineterminator="\r\n",
    )
    out.write("".join(f"{p}\r\n" for p in v["preamble"]))
    w.writerow(header)
    for i, row in enumerate(rows):
        if v["repeat_header"] and i == len(rows) // 2:
            w.writerow(header)
        w.writerow(row)
    bad = ""
    if v["bad_last_row"] and not v["one_column"]:
        bad = "truncated.row@x.example"
        out.write(bad + "\r\n")
    out.write("".join(f"{f}\r\n" for f in v["footer"]))
    text = out.getvalue()
    encoding = v["encoding"]
    try:
        return text.encode(encoding), bad
    except UnicodeEncodeError:
        return text.encode("utf-8"), bad


def test_fuzzed_rewrites_of_tuning_exports_give_the_same_accounts(world, tmp_path):
    import random

    rng = random.Random(6108)  # noqa: S311 - a seeded test shuffle
    # five tuning exports, one of each kind of identity column where possible
    chosen = rng.sample(sorted(world.exports, key=lambda e: e.file_name), 5)
    for n, export in enumerate(chosen):
        header = list(export.header)
        rows = [list(r) for r in export.rows]
        identity = next(
            i
            for i, h in enumerate(header)
            if h.lower() in ("email", "user_email", "login", "username")
        )
        base_dir = tmp_path / f"base{n}"
        base_dir.mkdir()
        plain_variant = {
            "delimiter": ",",
            "encoding": "utf-8",
            "quote_all": False,
            "preamble": [],
            "footer": [],
            "repeat_header": False,
            "bad_last_row": False,
            "one_column": False,
        }
        plain, _ = _render(header, rows, plain_variant, identity)
        (base_dir / f"{export.app_id}.csv").write_bytes(plain)
        want, base_unreadable, want_report, _ = parse_file(base_dir / f"{export.app_id}.csv")
        assert want and not base_unreadable
        for k in range(8):
            v = _variants(rng)
            data, bad = _render(header, rows, v, identity)
            path = tmp_path / f"v{n}-{k}" / f"{export.app_id}.csv"
            path.parent.mkdir()
            path.write_bytes(data)
            got, unreadable, report, _ = parse_file(path)
            if v["one_column"]:
                assert [a.account for a in got] == [a.account for a in want], v
            else:
                strip = lambda accs: [  # noqa: E731
                    (
                        a.account,
                        a.email,
                        a.given,
                        a.family,
                        a.display,
                        a.role,
                        a.state,
                        a.created,
                        a.last_login,
                    )
                    for a in accs
                ]
                assert strip(got) == strip(want), v
            # nothing silently dropped: every line has a place, and the bad row is reported
            assert report.lines == (
                report.preamble
                + 1
                + report.footer
                + report.blank
                + report.duplicates
                + report.repeated_headers
                + report.accounts
                + report.unreadable
                + report.continuation
            ), v
            assert report.footer == len(v["footer"]), v
            assert report.repeated_headers == int(v["repeat_header"]), v
            assert report.duplicates == want_report.duplicates, v
            assert [u[2][:7] for u in unreadable] == (["1 cells"] if bad else []), v
