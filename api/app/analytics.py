"""Analytics beacons - not secrets, they're meant to be embedded client-side.
Centralized here so both the raw-HTML /docs page (main.py, which doesn't go
through Jinja) and the templated browse UI (base.html, via templating.py's
Jinja globals) share the exact same snippets rather than risking them
drifting apart - see the analytics.py history for a past bug where /docs
had a beacon but the actual /browse/* pages visitors land on didn't.
"""

CLOUDFLARE_BEACON = (
    "<script type='module' src='https://static.cloudflareinsights.com/beacon.min.js' "
    'data-cf-beacon=\'{"token": "33b5013592484d7eae241203a47df919"}\'></script>'
)

GOOGLE_ANALYTICS_TAG = """<script async src="https://www.googletagmanager.com/gtag/js?id=G-6SJKPHMGR3"></script>
<script>
  window.dataLayer = window.dataLayer || [];
  function gtag(){dataLayer.push(arguments);}
  gtag('js', new Date());

  gtag('config', 'G-6SJKPHMGR3');
</script>"""
