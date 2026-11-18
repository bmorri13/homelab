{{- define "software-factory.labels" -}}
app.kubernetes.io/name: software-factory
app.kubernetes.io/part-of: software-factory
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- end -}}
