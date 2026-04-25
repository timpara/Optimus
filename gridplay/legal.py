"""Public legal pages — reachable without authentication.

Both the Impressum (§ 5 DDG) and the Datenschutzerklärung (DSGVO) must be
reachable from the homepage without any login wall, including by users who
have not yet authenticated. This router is mounted at the top level of the
FastAPI app (see ``main.py``) and serves the Markdown sources under
``docs/legal/`` rendered to lightweight HTML.
"""

from __future__ import annotations

from pathlib import Path

import markdown
from fastapi import APIRouter, HTTPException
from fastapi.responses import HTMLResponse

router = APIRouter(tags=["legal"])

_REPO_ROOT = Path(__file__).resolve().parent.parent
_DOCS = _REPO_ROOT / "docs" / "legal"

_PAGE_TEMPLATE = """<!doctype html>
<html lang="de">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>{title} — gridplay</title>
  <style>
    body {{
      max-width: 48rem;
      margin: 2rem auto;
      padding: 0 1rem 4rem;
      font-family: system-ui, -apple-system, "Segoe UI", sans-serif;
      line-height: 1.55;
      color: #1f2937;
      background: #f8fafc;
    }}
    h1, h2 {{ color: #0f172a; }}
    h1 {{ border-bottom: 1px solid #cbd5e1; padding-bottom: 0.4rem; }}
    a {{ color: #1d4ed8; }}
    code {{
      background: #e2e8f0;
      padding: 0 0.25rem;
      border-radius: 3px;
      font-size: 0.95em;
    }}
    hr {{ margin: 3rem 0 1rem; border: none; border-top: 1px solid #cbd5e1; }}
    footer {{ font-size: 0.85rem; color: #64748b; }}
  </style>
</head>
<body>
{body}
<hr>
<footer>
  <p>
    <a href="/">← Zur Anmeldung</a> ·
    <a href="/impressum">Impressum</a> ·
    <a href="/datenschutz">Datenschutz</a>
  </p>
  <p>gridplay ist eine Lehrsimulation. Keine Anlageberatung.</p>
</footer>
</body>
</html>
"""


def _render(stem: str, title: str) -> HTMLResponse:
    path = _DOCS / f"{stem}.md"
    if not path.is_file():
        raise HTTPException(status_code=500, detail=f"Legal page {stem} missing")
    body = markdown.markdown(
        path.read_text(encoding="utf-8"),
        extensions=["extra"],
    )
    return HTMLResponse(_PAGE_TEMPLATE.format(title=title, body=body))


@router.get("/impressum", response_class=HTMLResponse)
def impressum() -> HTMLResponse:
    """German legal notice required by § 5 DDG."""
    return _render("impressum", "Impressum")


@router.get("/datenschutz", response_class=HTMLResponse)
def datenschutz() -> HTMLResponse:
    """GDPR / DSGVO privacy policy."""
    return _render("datenschutz", "Datenschutzerklärung")
