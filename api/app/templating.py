"""Single shared Jinja2Templates instance, split out from web.py so main.py
can import web.py's router without a circular import."""

from fastapi.templating import Jinja2Templates

templates = Jinja2Templates(directory="app/templates")
