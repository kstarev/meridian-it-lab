"""The site's social preview, site/og.png: it exists at the size link previews expect, its
generator is committed, and the numbers the generator draws are the site's key numbers, scoped
the same way. Pillow is not a project dependency, so the image's pixels are not redrawn here:
`make og` redraws it.
"""

import struct
import subprocess
from pathlib import Path

from tier0 import site_og

ROOT = Path(__file__).resolve().parents[2]
OG = ROOT / "site/og.png"
SITE = ROOT / "site/index.html"


def test_og_png_exists_and_is_1200_by_630() -> None:
    head = OG.read_bytes()[:24]
    assert head[:8] == b"\x89PNG\r\n\x1a\n" and head[12:16] == b"IHDR"
    assert struct.unpack(">II", head[16:24]) == (site_og.W, site_og.H) == (1200, 630)


def test_the_site_links_the_preview() -> None:
    html = SITE.read_text()
    assert '<meta property="og:image" content="https://kstarev.dev/meridian-it-lab/og.png">' in html
    assert '<meta name="twitter:card" content="summary_large_image">' in html


def test_its_generator_is_committed_and_has_a_make_target() -> None:
    script = Path(site_og.__file__).resolve().relative_to(ROOT)
    tracked = subprocess.run(  # noqa: S603 - fixed arguments
        ["git", "ls-files", "--error-unmatch", str(script), "site/og.png"],  # noqa: S607
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    assert tracked.returncode == 0, tracked.stderr
    assert "python -m tier0.site_og --out site/og.png" in (ROOT / "Makefile").read_text()


def test_the_preview_shows_the_sites_key_numbers_for_the_same_test() -> None:
    html = SITE.read_text()
    n = site_og.numbers()
    (unsafe, setups, best) = site_og.stats()
    assert unsafe == ("0", "unsafe actions") and n["unsafe"] == 0
    assert setups == (str(n["setups"]), "AI models tested")
    assert best == (f"{n['best_percent']}%", f"right on all {n['k']} tries")
    # The same numbers as the site's tiles, under the same scope.
    assert f"unsafe actions in the first sealed comparison, across {n['setups']} AI models" in html
    assert f">{n['best_percent']}%</div>" in html and f">{n['tickets']}</div>" in html
    assert site_og.caption() == f"First sealed comparison · {n['tickets']} requests"
