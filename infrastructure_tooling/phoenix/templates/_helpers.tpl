{{- define "phoenix.labels" -}}
app.kubernetes.io/name: phoenix
app.kubernetes.io/part-of: phoenix
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- end -}}
