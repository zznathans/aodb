# aodb chart

Helm chart for [`aodb`](https://github.com/zznathans/aodb)'s API component,
a self-hosted Anarchy Online item/nano database API. Lives at `chart/` in
the [`zznathans/aodb`](https://github.com/zznathans/aodb) monorepo,
alongside the API's own source under `api/` - see the
[repo README](../README.md) for the full picture.

Deploys a FastAPI Deployment + Service. No chart-owned Ingress: the item
dump is downloaded fresh into memory from a public HTTPS URL
(`aodbApi.dumpUrl`) on every pod start, and the Service is meant to sit
behind whatever externally-managed ingress/traffic routing your cluster
already uses.

The app stores the loaded dump and its search index in Redis
(`aodbApi.redisUrl`) - by default this chart doesn't deploy Redis itself,
point it at whatever instance you've already got running. Alternatively,
set `aodbApi.redis.enabled=true` to have the chart deploy its own
dedicated Redis instance instead (via the
[redis-operator](https://github.com/OT-CONTAINER-KIT/redis-operator)
`Redis` custom resource, standalone mode - the operator's CRDs must
already be installed in the target cluster). `aodbApi.redisUrl` always
takes precedence when set.

Client-side analytics (e.g. a Cloudflare Web Analytics beacon or Google
Analytics tag - see the app's own README) aren't in the published image at
all, since the file that carries them is gitignored. Set
`aodbApi.analyticsHtml` to that same raw HTML and the chart renders it into
a ConfigMap and mounts it into the pod for you - no image rebuild needed.

## Values

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| aodbApi.analyticsHtml | string | `""` | Raw HTML for client-side analytics (e.g. a Cloudflare beacon or Google Analytics snippet), included verbatim on /api and every browse UI page - see the app's own README for app/templates/_analytics.html. That file is gitignored and never part of the published image, so it can't just be baked in; setting this instead renders it into a ConfigMap and mounts it over that path in the pod. Leave unset (the default) for no client-side analytics at all - this is unrelated to aodbApi.extraEnv's API_ANALYTICS_KEY, which is server-side request analytics, not a page script. |
| aodbApi.dumpUrl | string | `""` | Public HTTPS URL to the item dump zip (a zipped <aodb><item aoid="..." .../></aodb> XML file). Downloaded fresh into memory on every pod start - no database, no credentials. |
| aodbApi.extraEnv | list | `[]` | Extra environment variables to set on the app container, in addition to DUMP_URL and REDIS_URL. Each entry is a raw Kubernetes EnvVar (supports valueFrom, e.g. secretKeyRef) - e.g. for API_ANALYTICS_KEY (see the app's own README). |
| aodbApi.extraObjects | list | `[]` | Raw Kubernetes objects to render alongside chart-managed resources. |
| aodbApi.imagePullSecrets | list | `[]` | List of image pull secret names to attach to the ServiceAccount. Leave empty if the registry is public. |
| aodbApi.imageRepository | string | `"ghcr.io/zznathans/aodb"` | Container image registry and repository for the aodb-api image. |
| aodbApi.imageTag | string | `"1.5.3"` | Image tag to deploy. |
| aodbApi.podAnnotations | object | `{}` | Extra annotations to add to the pod template (e.g. for a service mesh sidecar injector or a config-reload trigger). |
| aodbApi.podLabels | object | `{}` | Extra labels to add to the pod template, in addition to the chart-managed `app` label. |
| aodbApi.redis.enabled | bool | `false` | Deploy a bundled Redis instance (via the OT-CONTAINER-KIT/redis-operator `Redis` custom resource, standalone mode only) alongside this app, instead of requiring an externally-provisioned aodbApi.redisUrl. Requires the redis-operator CRDs to already be installed in the target cluster. Off by default: enabling this for an existing installation that already points aodbApi.redisUrl at its own Redis would otherwise start deploying an unwanted, unused second Redis instance. |
| aodbApi.redis.exporter.image.pullPolicy | string | `"Always"` | Image pull policy for the redis-exporter sidecar. |
| aodbApi.redis.exporter.image.repository | string | `"quay.io/opstree/redis-exporter"` | Container image repository for the redis-exporter sidecar. |
| aodbApi.redis.exporter.image.tag | string | `"v1.84.0"` | Container image tag for the redis-exporter sidecar. |
| aodbApi.redis.exporter.resources | object | `{"limits":{"cpu":"100m","memory":"128Mi"},"requests":{"cpu":"100m","memory":"128Mi"}}` | Resource requests and limits for the redis-exporter sidecar container. |
| aodbApi.redis.image.pullPolicy | string | `"IfNotPresent"` | Image pull policy for the bundled Redis instance. |
| aodbApi.redis.image.repository | string | `"quay.io/opstree/redis"` | Container image repository for the bundled Redis instance. |
| aodbApi.redis.image.tag | string | `"v8.6.1"` | Container image tag for the bundled Redis instance. |
| aodbApi.redis.maxmemory | string | `"500mb"` | maxmemory directive for the bundled Redis instance. Needs headroom below aodbApi.redis.resources.limits.memory - too tight a gap caused real OOMKills in production (the memory limit alone isn't enough; Redis needs to know its own budget to evict rather than exceed the container limit). |
| aodbApi.redis.maxmemoryPolicy | string | `"allkeys-lru"` | maxmemory-policy directive for the bundled Redis instance. allkeys-lru is safe here since this instance is dedicated to this app - evicting under memory pressure beats refusing writes. |
| aodbApi.redis.resources | object | `{"limits":{"cpu":"500m","memory":"512Mi"},"requests":{"cpu":"101m","memory":"128Mi"}}` | Resource requests and limits for the bundled Redis instance's main container. Keep the memory limit comfortably above aodbApi.redis.maxmemory above. The CPU limit needs real burst headroom above the steady-state request - a full item/nano dump load builds the name-substring trigram index (app/store.py) via ~3.3M SADD calls, measured at ~32s of Redis-side command time for the SADD calls alone; a limit as tight as the request throttled that burst badly enough to push the whole load past the app's liveness probe window (chart/templates/deployment.yaml), confirmed live in a real crash loop. |
| aodbApi.redis.serviceMonitor.enabled | bool | `false` | Create a Prometheus Operator ServiceMonitor for the bundled Redis instance's exporter metrics. Separate toggle from aodbApi.redis.enabled since it depends on the prometheus-operator CRDs being installed, which isn't guaranteed just because Redis itself is being deployed. |
| aodbApi.redis.serviceMonitor.interval | string | `"30s"` | Scrape interval for the Redis metrics ServiceMonitor. |
| aodbApi.redis.storage.size | string | `"1Gi"` | Size of the PersistentVolumeClaim provisioned for the bundled Redis instance. |
| aodbApi.redisUrl | string | `""` | Connection URL for the Redis instance this app uses to store the loaded item dump and back its search index (see the app's own README) - e.g. redis://my-redis:6379/0, or redis://:password@my-redis:6379/0 if auth is enabled. Takes precedence over aodbApi.redis.enabled if both are set. Leave unset (and set aodbApi.redis.enabled=true) to use the chart's own bundled Redis instead of pointing at an externally-provisioned one. |
| aodbApi.replicaCount | int | `1` | Number of pod replicas. Safe to run more than one - each replica independently loads its own in-memory copy of the dump from dumpUrl on startup, no shared state between them. |
| aodbApi.resources | object | `{"limits":{"cpu":"250m","memory":"512Mi"},"requests":{"cpu":"50m","memory":"128Mi"}}` | Resource requests and limits for the app container. The 256Mi memory limit was too tight for the dump-load step and OOMKilled pods mid-load in a real cluster (confirmed reproducible, not a one-off) - 512Mi gives real headroom above the ~65MB peak RSS the dump parser alone was measured at (app/dump_loader.py), which doesn't account for the rest of the process (interpreter, FastAPI/uvicorn, the Redis client, the raw HTTP response buffer) or growth in the dump/item schema over time. |
| aodbApi.service.port | int | `80` | Port the Service listens on and forwards to the container's 8000. |

## Development

```
helm lint chart --strict \
  --set aodbApi.dumpUrl=https://example.invalid/dump.xml.zip \
  --set aodbApi.redisUrl=redis://example-redis:6379/0
helm unittest chart
```

Every `vX.Y.Z` release (shared with the app - see the top-level README's
"Releases" section) gets the packaged `.tgz` attached as a release asset
and published two ways (see `.github/workflows/release.yml`'s
`publish-chart` job):

As an OCI artifact at `oci://ghcr.io/zznathans/aodb/charts` - same
registry/namespace as the app image, no `helm repo add` needed, the
recommended path:

```
helm install aodb oci://ghcr.io/zznathans/aodb/charts/aodb --version X.Y.Z
```

And, for third-party tooling that doesn't speak OCI registries yet, to a
classic Helm chart index at https://zznathans.github.io/aodb/charts/:

```
helm repo add aodb https://zznathans.github.io/aodb/charts
helm repo update
helm install aodb aodb/aodb --version X.Y.Z
```
