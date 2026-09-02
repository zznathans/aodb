#!/usr/bin/env bash
# Packages chart/ and pushes it to the gh-pages branch's charts/ subdirectory
# as a traditional Helm chart repo (index.yaml + .tgz) - an additional
# install method alongside chart-publish.yml's OCI/GHCR publish, not a
# replacement (see README.md's Releases section). gh-pages also hosts the
# app README under a different path on the same branch, so this only ever
# touches charts/ and leaves the rest of the branch alone.
#
# Run from release.yml as @semantic-release/exec's publishCmd (see
# .releaserc.json) - GITHUB_TOKEN must already be set in the environment
# (the same App installation token used for the rest of the release job).
#
# Not delegated to @qiwi/semantic-release-gh-pages-plugin: its published npm
# version unconditionally builds a `https://<token>@github.com/...` push
# URL, which GitHub rejects ("Password authentication is not supported")
# regardless of plugin config - there's no way to make it use the
# `x-access-token:<token>@` form GitHub actually requires without also
# clobbering GITHUB_TOKEN for the other plugins in this same run that need a
# bare token (@semantic-release/github's own API auth). Doing the push
# ourselves sidesteps that entirely.
set -euo pipefail

version="$1"
repo_url="https://x-access-token:${GITHUB_TOKEN}@github.com/zznathans/aodb.git"
workdir="$(mktemp -d)"

git clone --branch gh-pages --single-branch --depth 1 "$repo_url" "$workdir"

mkdir -p "$workdir/charts"
helm package chart --version "$version" --app-version "$version" -d "$workdir/charts"

if [ -f "$workdir/charts/index.yaml" ]; then
  helm repo index "$workdir/charts" --url https://zznathans.github.io/aodb/charts/ --merge "$workdir/charts/index.yaml"
else
  helm repo index "$workdir/charts" --url https://zznathans.github.io/aodb/charts/
fi

cd "$workdir"
git config user.name "github-actions[bot]"
git config user.email "github-actions[bot]@users.noreply.github.com"
git add charts
git commit -m "aodb-chart ${version}"
git push origin HEAD:gh-pages
