#!/bin/sh
# Called from helm-release.yml with the released chart version as $1. Keeps
# chart/Chart.yaml's version/appVersion in lockstep with the chart-vX.Y.Z
# GitHub release tag - `helm package` names its artifact from Chart.yaml's
# version field, so without this every release after the first collides on
# the same unchanged artifact name.
set -eu

version="$1"
chart_file="chart/Chart.yaml"

sed -i \
  -e "s/^appVersion: .*/appVersion: ${version}/" \
  -e "s/^version: .*/version: ${version}/" \
  "$chart_file"
