# aodb

[![CI](https://github.com/zznathans/aodb/actions/workflows/ci.yml/badge.svg)](https://github.com/zznathans/aodb/actions/workflows/ci.yml)
[![OpenSSF Scorecard](https://api.securityscorecards.dev/projects/github.com/zznathans/aodb/badge)](https://scorecard.dev/viewer/?uri=github.com/zznathans/aodb)
[![License: GPL v3](https://img.shields.io/badge/License-GPLv3-blue.svg)](LICENSE)

Self-hosted replacement for the third-party "Central Item Database"
(`cidb.bebot.link`) that BeBot's `!items` command relies on, which has been
suffering Cloudflare 522 (origin timeout) outages. Implements the same
query-string contract and returns the same raw AOML text BeBot expects, so
it's a drop-in replacement via BeBot's `Items.CIDB` setting.

This repo holds both halves of the project:

- [`api/`](api/README.md) - the FastAPI application (JSON API, legacy AOML
  endpoint, and a server-rendered browse/search UI).
- [`chart/`](chart/README.md.gotmpl) - the Helm chart that deploys it.

They used to be two separate repos (`aodb-api` and `aodb-api-helm`), merged
here so a single PR can change the app and its chart together instead of
coordinating across repos.

## Releases

The app and chart version independently, since a change to one rarely
implies a change to the other:

- App releases are tagged `app-vX.Y.Z` and publish a container image to
  `ghcr.io/zznathans/aodb`.
- Chart releases are tagged `chart-vX.Y.Z`, attach the packaged `.tgz` as a
  release asset, and publish to a Helm chart index at
  https://aodb.ao.yeetbox.net/charts/ (`helm repo add aodb
  https://aodb.ao.yeetbox.net/charts`).

Both also keep `gh-pages` up to date: the app's README gets synced there as
a browsable doc site, and the chart index/packages live under `charts/` on
the same branch/domain.

An app release automatically opens a PR bumping the chart's default image
tag (`chart/values.yaml`) to match, so the chart stays deployable with the
latest image without a chart release being required just for that.

See `api/README.md` for the app's own docs (API endpoints, local dev,
data model) and `chart/README.md.gotmpl` for the chart's values reference.
