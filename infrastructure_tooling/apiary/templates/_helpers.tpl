{{- define "apiary.labels" -}}
app.kubernetes.io/name: apiary
app.kubernetes.io/part-of: toolhive
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- end }}

{{- define "apiary.selector" -}}
app.kubernetes.io/name: apiary
{{- end }}

{{- define "apiary.image" -}}
{{- if .Values.image.digest -}}
{{ .Values.image.repository }}{{ with .Values.image.tag }}:{{ . }}{{ end }}@{{ .Values.image.digest }}
{{- else -}}
{{ required "image.digest (or image.tag) is required" .Values.image.tag | printf "%s:%s" .Values.image.repository }}
{{- end -}}
{{- end }}

{{- /* ESO defaults spelled out so Argo CD doesn't report drift on them. */}}
{{- define "apiary.esDefaults" }}
        conversionStrategy: Default
        decodingStrategy: None
        metadataPolicy: None
{{- end }}
