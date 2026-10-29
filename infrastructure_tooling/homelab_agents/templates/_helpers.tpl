{{- define "homelab-agents.labels" -}}
app.kubernetes.io/name: homelab-agents
app.kubernetes.io/part-of: homelab-agents
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- end -}}
