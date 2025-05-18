{{- define "uptime-kuma.name" -}}
{{- default .Chart.Name .Values.nameOverride -}}
{{- end -}}

{{- define "uptime-kuma.fullname" -}}
{{- if .Values.fullnameOverride }}
{{- .Values.fullnameOverride }}
{{- else }}
{{- printf "%s-%s" .Release.Name .Chart.Name }}
{{- end }}
{{- end -}}

{{- define "uptime-kuma.chart" -}}
{{- printf "%s-%s" .Chart.Name .Chart.Version }}
{{- end -}}
