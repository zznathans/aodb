{{- define "aodb.mongoName" -}}
{{ .Release.Name }}-mongo
{{- end }}

{{- define "aodb.mongoUrl" -}}
{{- if .Values.aodbApi.mongoUrl -}}
{{ .Values.aodbApi.mongoUrl }}
{{- else if .Values.aodbApi.mongo.enabled -}}
mongodb://{{ include "aodb.mongoName" . }}.{{ .Release.Namespace }}.svc.cluster.local:27017/aodb
{{- else -}}
{{ fail "aodbApi.mongoUrl is required (or set aodbApi.mongo.enabled=true to deploy a bundled MongoDB)" }}
{{- end -}}
{{- end }}

{{- define "aodb.redisName" -}}
{{ .Release.Name }}-redis
{{- end }}

{{/*
Unlike aodb.mongoUrl, this deliberately never fails when unset - Redis is
an optional cache-aside layer (see app/store.py's _cached_json), not a
required datastore, so it's fine for this to render empty and have the
app run with no cache at all.
*/}}
{{- define "aodb.redisUrl" -}}
{{- if .Values.aodbApi.redisUrl -}}
{{ .Values.aodbApi.redisUrl }}
{{- else if .Values.aodbApi.redis.enabled -}}
redis://{{ include "aodb.redisName" . }}.{{ .Release.Namespace }}.svc.cluster.local:6379/0
{{- end -}}
{{- end }}
