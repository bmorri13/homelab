{{- define "toolhive-mcp.labels" -}}
app.kubernetes.io/part-of: toolhive
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- end }}
