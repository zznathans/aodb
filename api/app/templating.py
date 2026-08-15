"""Single shared Jinja2Templates instance, split out from web.py so main.py
can import web.py's router without a circular import."""

from fastapi.templating import Jinja2Templates

from .analytics import CLOUDFLARE_BEACON, GOOGLE_ANALYTICS_TAG

templates = Jinja2Templates(directory="app/templates")
templates.env.globals["cloudflare_beacon"] = CLOUDFLARE_BEACON
templates.env.globals["google_analytics_tag"] = GOOGLE_ANALYTICS_TAG
