"""Frontend structural tests for ``index.html``.

These tests guard three user-facing properties that are easy to accidentally
regress because ``index.html`` is a single, large, hand-written file:

1. The tutorial overlay always renders above the Leaflet map (the whole point
   of the ``fix/tutorial-zindex-and-disclaimer`` change).
2. The full-tutorial "read the whole thing on one page" view exists alongside
   the step-by-step view, with all eight canonical steps intact.
3. The game ships an educational/beta disclaimer and a GitHub repository link
   in the places a first-time visitor will actually see them.

The tests operate purely on the file contents — no browser is required — so
they are cheap to run in CI and do not pull in new dependencies.
"""

from __future__ import annotations

import re
from html.parser import HTMLParser
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
INDEX_PATH = REPO_ROOT / "index.html"
GITHUB_URL = "https://github.com/timpara/Optimus"

# Leaflet's internal stacking caps out around 1000 for controls. Anything the
# tutorial layers itself at must be strictly greater than this to guarantee it
# renders above the map on every browser.
LEAFLET_MAX_Z_INDEX = 1000


@pytest.fixture(scope="module")
def html_text() -> str:
    """Return the full text of ``index.html`` (cached for the module)."""
    assert INDEX_PATH.exists(), f"index.html missing at {INDEX_PATH}"
    return INDEX_PATH.read_text(encoding="utf-8")


# ──────────────────────────────────────────────────────────────────────────
# Tiny HTML parser that lets us locate specific elements by id/class without
# pulling in BeautifulSoup. We only need attribute lookups, so a 30-line
# subclass of html.parser.HTMLParser is plenty.
# ──────────────────────────────────────────────────────────────────────────


class _ElementCollector(HTMLParser):
    """Collect every start-tag's attributes keyed by element id."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        # id -> list of (tag, attrs_dict). A list because the DOM technically
        # allows duplicate ids; we still want to see all of them.
        self.by_id: dict[str, list[tuple[str, dict[str, str]]]] = {}
        # All start tags in order, for link scanning.
        self.all_tags: list[tuple[str, dict[str, str]]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attrs_dict = {k: (v or "") for k, v in attrs}
        self.all_tags.append((tag, attrs_dict))
        el_id = attrs_dict.get("id")
        if el_id:
            self.by_id.setdefault(el_id, []).append((tag, attrs_dict))


@pytest.fixture(scope="module")
def parsed(html_text: str) -> _ElementCollector:
    collector = _ElementCollector()
    collector.feed(html_text)
    return collector


def _z_index_from_class(class_attr: str) -> int | None:
    """Extract the z-index value from a Tailwind class attribute.

    Supports both the scale utility (``z-40``) and the arbitrary-value form
    (``z-[9999]``). Returns ``None`` if no z-utility is present.
    """
    # Arbitrary value wins if present (more specific).
    m = re.search(r"z-\[(\d+)\]", class_attr)
    if m:
        return int(m.group(1))
    m = re.search(r"(?:^|\s)z-(\d+)(?:\s|$)", class_attr)
    if m:
        return int(m.group(1))
    return None


# ──────────────────────────────────────────────────────────────────────────
# 1. Tutorial z-index regression guards
# ──────────────────────────────────────────────────────────────────────────


def test_tutorial_overlay_z_index_beats_leaflet(parsed: _ElementCollector) -> None:
    """The overlay must sit above Leaflet's maximum internal z-index (~1000)."""
    elements = parsed.by_id.get("tutorialOverlay")
    assert elements, "#tutorialOverlay element missing from index.html"
    class_attr = elements[0][1].get("class", "")
    z = _z_index_from_class(class_attr)
    assert z is not None, f"#tutorialOverlay must declare a z-index utility, got {class_attr!r}"
    assert z > LEAFLET_MAX_Z_INDEX, (
        f"#tutorialOverlay z-index is {z}; must be > {LEAFLET_MAX_Z_INDEX} "
        "to stay above the Leaflet map"
    )


def test_tutorial_card_z_index_above_overlay(parsed: _ElementCollector) -> None:
    """The card must stack above the overlay backdrop."""
    overlay = parsed.by_id["tutorialOverlay"][0][1].get("class", "")
    card = parsed.by_id["tutorialCard"][0][1].get("class", "")
    overlay_z = _z_index_from_class(overlay)
    card_z = _z_index_from_class(card)
    assert overlay_z is not None and card_z is not None
    assert card_z >= overlay_z, (
        f"#tutorialCard z-index ({card_z}) must be >= #tutorialOverlay ({overlay_z})"
    )


def test_leaflet_pane_cap_present(html_text: str) -> None:
    """A defensive CSS rule should cap Leaflet's pane stacking contexts."""
    # Any one of these patterns is enough — we just want proof that the cap exists.
    patterns = [
        r"\.leaflet-pane[^{]*\{[^}]*z-index\s*:\s*400",
        r"#tutorialOverlay[^{]*\{[^}]*z-index\s*:\s*9999",
    ]
    for pattern in patterns:
        assert re.search(pattern, html_text), (
            f"Expected defensive Leaflet/tutorial CSS matching {pattern!r}"
        )


# ──────────────────────────────────────────────────────────────────────────
# 2. Tutorial full-list ("read the whole thing") view
# ──────────────────────────────────────────────────────────────────────────


def test_tutorial_full_list_container_exists(parsed: _ElementCollector) -> None:
    assert "tutorialFullList" in parsed.by_id, (
        "Expected a #tutorialFullList element that holds the read-all-steps view"
    )
    assert "tutorialFullContent" in parsed.by_id, (
        "Expected a #tutorialFullContent element that receives rendered steps"
    )


def test_tutorial_toggle_functions_present(html_text: str) -> None:
    """Both the open-full and return-to-stepped helpers must exist."""
    for fn in ("tutorialShowAll", "tutorialShowStepped", "renderTutorialFullList"):
        assert f"function {fn}" in html_text, f"Expected JS function {fn}() in index.html"


def test_view_all_button_wired_up(html_text: str) -> None:
    """The card must expose a button that opens the full view."""
    assert re.search(r'onclick="tutorialShowAll\(\)"', html_text), (
        "Expected a button/trigger wired to tutorialShowAll()"
    )
    assert re.search(r'onclick="tutorialShowStepped\(\)"', html_text), (
        "Expected a trigger to return to the stepped tutorial"
    )


def test_tutorial_steps_count_preserved(html_text: str) -> None:
    """Regression: the canonical 8-step tutorial must stay at 8 steps."""
    # Count the step object literals inside the TUTORIAL_STEPS array.
    match = re.search(r"const TUTORIAL_STEPS\s*=\s*\[(.*?)\];", html_text, re.DOTALL)
    assert match, "TUTORIAL_STEPS array not found"
    body = match.group(1)
    # Each step starts with a `title:` key.
    step_count = len(re.findall(r"\btitle\s*:", body))
    assert step_count == 8, f"Expected 8 tutorial steps, found {step_count}"


# ──────────────────────────────────────────────────────────────────────────
# 3. GitHub link + educational / beta disclaimer
# ──────────────────────────────────────────────────────────────────────────


def test_github_repo_link_appears_in_header_and_login(parsed: _ElementCollector) -> None:
    """Both the header and the login overlay must link to the GitHub repo."""
    assert "headerGithubLink" in parsed.by_id, "Header should contain a GitHub link"
    assert "loginGithubLink" in parsed.by_id, "Login overlay should contain a GitHub link"
    for element_id in ("headerGithubLink", "loginGithubLink"):
        attrs = parsed.by_id[element_id][0][1]
        assert attrs.get("href", "").startswith(GITHUB_URL), (
            f"#{element_id} must link to {GITHUB_URL}, got {attrs.get('href')!r}"
        )


def test_external_links_are_safe(parsed: _ElementCollector) -> None:
    """Any ``target=_blank`` anchor must set ``rel`` to prevent tab-nabbing."""
    offenders: list[dict[str, str]] = []
    for tag, attrs in parsed.all_tags:
        if tag != "a":
            continue
        if attrs.get("target") != "_blank":
            continue
        rel = attrs.get("rel", "")
        if "noopener" not in rel:
            offenders.append(attrs)
    assert not offenders, (
        "All target=_blank links must include rel=noopener; offenders: "
        f"{[a.get('href') for a in offenders]}"
    )


def test_header_beta_badge_present(parsed: _ElementCollector) -> None:
    assert "headerBetaBadge" in parsed.by_id, (
        "Header should display a BETA / educational badge for persistent visibility"
    )


def test_login_beta_notice_present(parsed: _ElementCollector, html_text: str) -> None:
    assert "loginBetaNotice" in parsed.by_id, "Login overlay should carry a beta/edu notice"
    # The visible copy must mention both 'educational' and 'beta'.
    assert re.search(r"educational", html_text, re.IGNORECASE)
    assert re.search(r"\bbeta\b", html_text, re.IGNORECASE)


def test_tutorial_step_one_includes_disclaimer(html_text: str) -> None:
    """Step 1 must reinforce the educational-beta framing."""
    # The JS string uses \u26A0 (warning sign) to keep the file pure ASCII.
    match = re.search(
        r"title:\s*\"Welcome to Battery Trader Sim!\",\s*text:\s*\"([^\"]+)\"",
        html_text,
    )
    assert match, "Could not locate tutorial step 1 text"
    step_text = match.group(1)
    assert "Educational beta" in step_text or "educational beta" in step_text.lower(), (
        f"Step 1 should mention the educational beta disclaimer; got: {step_text!r}"
    )


# ──────────────────────────────────────────────────────────────────────────
# 4. Lightweight sanity checks
# ──────────────────────────────────────────────────────────────────────────


def test_toast_container_below_tutorial(parsed: _ElementCollector) -> None:
    """Toasts should never render above the tutorial overlay."""
    toast_class = parsed.by_id["toastContainer"][0][1].get("class", "")
    overlay_class = parsed.by_id["tutorialOverlay"][0][1].get("class", "")
    toast_z = _z_index_from_class(toast_class) or 0
    overlay_z = _z_index_from_class(overlay_class) or 0
    assert toast_z < overlay_z, (
        f"toast z-index ({toast_z}) must be below tutorial overlay ({overlay_z})"
    )


def test_tutorial_overlay_is_hidden_by_default(parsed: _ElementCollector) -> None:
    """The overlay must start hidden so it does not block the login screen."""
    overlay_class = parsed.by_id["tutorialOverlay"][0][1].get("class", "")
    assert "hidden" in overlay_class.split(), (
        "#tutorialOverlay must include the `hidden` class by default"
    )
