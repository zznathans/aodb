{{- define "aodb.redisName" -}}
{{ .Release.Name }}-redis
{{- end }}

{{- define "aodb.redisUrl" -}}
{{- if .Values.aodbApi.redisUrl -}}
{{ .Values.aodbApi.redisUrl }}
{{- else if .Values.aodbApi.redis.enabled -}}
redis://{{ include "aodb.redisName" . }}.{{ .Release.Namespace }}.svc.cluster.local:6379/0
{{- else -}}
{{ fail "aodbApi.redisUrl is required (or set aodbApi.redis.enabled=true to deploy a bundled Redis)" }}
{{- end -}}
{{- end }}
