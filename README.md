# aodb

| | |
|---|---|
| **CI** | [![Chart CI](https://github.com/zznathans/aodb/actions/workflows/chart-ci.yml/badge.svg)](https://github.com/zznathans/aodb/actions/workflows/chart-ci.yml) [![Docker CI](https://github.com/zznathans/aodb/actions/workflows/docker.yml/badge.svg)](https://github.com/zznathans/aodb/actions/workflows/docker.yml) |
| **Tests** | [![Coverage Status](https://coveralls.io/repos/github/zznathans/aodb/badge.svg?branch=main)](https://coveralls.io/github/zznathans/aodb?branch=main) |
| **Security** | [![OpenSSF Scorecard](https://api.securityscorecards.dev/projects/github.com/zznathans/aodb/badge)](https://scorecard.dev/viewer/?uri=github.com/zznathans/aodb) |
| **Version** | [![Release](https://github.com/zznathans/aodb/actions/workflows/release.yml/badge.svg)](https://github.com/zznathans/aodb/actions/workflows/release.yml) [![Latest](https://img.shields.io/github/v/tag/zznathans/aodb?sort=semver)](https://github.com/zznathans/aodb/releases) |
| **License** | [![License: GPL v3](https://img.shields.io/badge/License-GPLv3-blue.svg)](LICENSE) |

A FastAPI service that parses the official Anarchy Online item dump into
Redis and serves it back out as a JSON API, a legacy AOML-compatible
endpoint (raw chat-markup responses for game-chat clients that expect
that format), and a plain server-rendered browse/search UI - no build
step, works with JavaScript disabled. Ships with a Helm chart for
deploying it.

## What's here

- [`api/`](api/README.md) - the FastAPI application: JSON API, legacy AOML
  endpoint, browse UI, sitemap, and everything else that runs at request
  time. See its README for the full endpoint reference, data model, and
  local development instructions.
- [`chart/`](chart/README.md.gotmpl) - the Helm chart that deploys it to
  Kubernetes. See its README (values reference, generated via helm-docs)
  for every configurable setting.
- [`scripts/`](scripts/bump-chart-version.sh) - small shell helpers used
  by the release workflows below, not meant to be run by hand.

## Highlights

- **Primary JSON API** for items and nano programs - substring search,
  quality-level/school/profession filters, pagination, real HTTP status
  codes.
- **Legacy AOML endpoint** - raw chat-markup responses for game-chat
  clients that expect that format, rather than JSON.
- **Browse UI** (`/`) - a stats landing page plus searchable items/nanos
  pages (with category/profession breakdowns and detail views), entirely
  server-rendered. The JSON API and legacy endpoint live under `/api` so
  they never collide with these paths.
- **Search-engine discovery** - `/robots.txt` and a chunked `/sitemap.xml`
  covering the full catalog.
- **Redis-backed storage** shared across every pod, loaded once behind a
  distributed lock so a multi-pod rollout doesn't stampede the source dump
  on startup.
- **Hardened container image** - digest-pinned base image, unused OS
  packages stripped, every Python dependency hash-verified at install
  time.
- **Optional analytics** - an opt-in client-side snippet on the browse UI
  and `/api` (nothing ships by default), and an opt-in
  [api-analytics](https://github.com/tom-draper/api-analytics) middleware
  for request-level metrics (never enabled unless an API key is
  configured; client IPs are never sent).

## Releases

The app and chart share one version number - a fix/feat commit anywhere in
the repo cuts one `vX.Y.Z` tag/release covering both, rather than
versioning app/chart independently. Every release:

- Publishes a container image to `ghcr.io/zznathans/aodb`, tagged
  `X.Y.Z` and `latest` (via the `@semantic-release-plus/docker` plugin,
  run inline as part of the release job itself).
- Packages and publishes the chart two ways, once the version-bump commit
  above lands: as an OCI artifact at `oci://ghcr.io/zznathans/aodb/charts`
  (`helm install aodb oci://ghcr.io/zznathans/aodb/charts/aodb --version
  X.Y.Z`, no `helm repo add` needed - the recommended path) and to a
  classic Helm chart index at https://zznathans.github.io/aodb/charts/
  (`helm repo add aodb https://zznathans.github.io/aodb/charts`), kept
  around for third-party tooling that doesn't speak OCI registries yet.
  `chart/values.yaml`'s default image tag always matches the release
  version - the app and chart can never drift out of sync since they're
  the same number.

`gh-pages` hosts the app's README as a browsable doc site (synced whenever
`api/README.md` changes, not just on a release) alongside the chart index
above, at a different path on the same branch/domain.

## Development

See [`api/README.md`](api/README.md#local-development) for running the app
locally, and [`chart/README.md.gotmpl`](chart/README.md.gotmpl#development)
for linting/testing the chart. CI runs both independently and only when the
relevant half of the repo actually changed.
