# aodb

[![CI](https://github.com/zznathans/aodb/actions/workflows/ci.yml/badge.svg)](https://github.com/zznathans/aodb/actions/workflows/ci.yml)
[![Coverage Status](https://coveralls.io/repos/github/zznathans/aodb/badge.svg?branch=main)](https://coveralls.io/github/zznathans/aodb?branch=main)
[![OpenSSF Scorecard](https://api.securityscorecards.dev/projects/github.com/zznathans/aodb/badge)](https://scorecard.dev/viewer/?uri=github.com/zznathans/aodb)
[![License: GPL v3](https://img.shields.io/badge/License-GPLv3-blue.svg)](LICENSE)

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

- **Primary JSON API** for items and nano programs - prefix search,
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
  and `/api/docs` (nothing ships by default), and an opt-in
  [api-analytics](https://github.com/tom-draper/api-analytics) middleware
  for request-level metrics (never enabled unless an API key is
  configured; client IPs are never sent).

## Releases

The app and chart version independently, since a change to one rarely
implies a change to the other:

- App releases are tagged `app@X.Y.Z` and publish a multi-arch (amd64 +
  arm64) container image to `ghcr.io/zznathans/aodb`.
- Chart releases are tagged `chart@X.Y.Z`, attach the packaged `.tgz` as a
  release asset, and publish it two ways: as an OCI artifact at
  `oci://ghcr.io/zznathans/aodb/charts` (`helm install aodb
  oci://ghcr.io/zznathans/aodb/charts/aodb --version X.Y.Z`, no `helm repo
  add` needed - the recommended path) and to a classic Helm chart index at
  https://aodb.ao.yeetbox.net/charts/ (`helm repo add aodb
  https://aodb.ao.yeetbox.net/charts`), kept around for third-party tooling
  that doesn't speak OCI registries yet.

An app release automatically opens a PR bumping the chart's default image
tag (`chart/values.yaml`) to match, so the chart stays deployable with the
latest image without a chart release being required just for that.

`gh-pages` hosts the app's README as a browsable doc site (synced on every
app release) alongside the chart index above, at a different path on the
same branch/domain.

## Development

See [`api/README.md`](api/README.md#local-development) for running the app
locally, and [`chart/README.md.gotmpl`](chart/README.md.gotmpl#development)
for linting/testing the chart. CI runs both independently and only when the
relevant half of the repo actually changed.
