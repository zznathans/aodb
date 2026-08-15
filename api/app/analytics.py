"""Optional analytics snippet - never committed to this repo (see
templates/_analytics.html.example). If app/templates/_analytics.html
exists, its raw contents get included on /docs and every /browse/* page -
entirely up to whoever deploys this to supply their own (a Cloudflare Web
Analytics beacon, a Google Analytics tag, anything else). A deployment
from this repo as-is ships with no analytics of any kind - this repo used
to hardcode real tracking IDs here, which meant anyone else deploying it
unmodified silently sent their own visitors' traffic into the original
owner's dashboards.
"""

from pathlib import Path

_ANALYTICS_PARTIAL = Path(__file__).parent / "templates" / "_analytics.html"


def analytics_snippet() -> str:
    return _ANALYTICS_PARTIAL.read_text() if _ANALYTICS_PARTIAL.exists() else ""
