"""The scope explainer: one plain-English sentence per scope URL; unknown scopes say so."""

import pytest

from meridian_dataset.oauth.apps import ALL_APPS
from meridian_oauth_radar.scopes import (
    CLASSES,
    RESTRICTED,
    SCOPES,
    UNKNOWN,
    can,
    explain,
    google_class,
    is_restricted,
    known,
)

G = "https://www.googleapis.com/auth/"


def test_the_catalogue_examples() -> None:
    assert explain("https://mail.google.com/") == (
        "This app can read, send and delete all your email, and change your mail settings."
    )
    assert explain(G + "drive.readonly") == "This app can see and download all your Drive files."
    assert explain(G + "drive.file") == (
        "This app can see and change only the Drive files you open or create with this app."
    )


def test_an_unknown_scope_says_so_plainly() -> None:
    url = G + "made.up.scope"
    assert not known(url)
    assert explain(url) == (
        f"Unknown scope: the radar has no plain-English description of {url}, "
        "so a person in IT should check what it allows."
    )
    assert google_class(url) == UNKNOWN
    assert url in can(url)
    assert not is_restricted(url)


@pytest.mark.parametrize("url", sorted(SCOPES))
def test_every_entry_is_one_plain_sentence(url: str) -> None:
    scope = SCOPES[url]
    assert scope.can and scope.can[0].islower(), url
    assert not scope.can.endswith("."), url
    assert ". " not in scope.can, url
    assert scope.google_class in CLASSES, url
    sentence = explain(url)
    assert sentence.startswith("This app can ") and sentence.endswith("."), url
    assert len(sentence) <= 120, url


def test_every_scope_the_tuning_world_holds_is_known() -> None:
    held = {scope for app in ALL_APPS for scope in app.scopes}
    assert sorted(s for s in held if not known(s)) == []


def test_full_mail_and_full_drive_are_restricted_and_sign_in_is_not() -> None:
    for url in (
        "https://mail.google.com/",
        G + "gmail.readonly",
        G + "drive",
        G + "drive.readonly",
    ):
        assert google_class(url) == RESTRICTED
    for url in ("openid", G + "userinfo.email", G + "drive.file", G + "calendar.readonly"):
        assert not is_restricted(url)
