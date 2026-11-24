#!/usr/bin/env bash
# Check that the collector this chart deploys can read the config this chart renders.
#
# Renders the chart, takes the collector's config (the `relay` key of its ConfigMap)
# and the image of its DaemonSet, and runs that image's own `validate` on that config.
# A chart bump can rename a receiver or an option (0.183.0 renamed kubeletstats to
# kubelet_stats in the config it writes), and a collector that does not know the new
# name exits at start: the DaemonSet crash-loops and pod logs, audit logs and metrics
# stop reaching Splunk. The chart render diff cannot see that. This can.
#
# What it proves: every receiver, processor, exporter and extension in the config
# exists in this collector build and accepts its settings, and every pipeline names
# components that are defined.
# What it does not prove: that the collector starts on the node (host paths, RBAC),
# that the kubelet or the API server answer it, or that anything arrives in Splunk.
# After a merge, look at the DaemonSet rollout and that pod logs and index=metrics keep arriving.
#
# Needs helm, yq (mikefarah) and docker. No secrets, no cluster.
set -euo pipefail
cd "$(dirname "$0")"

work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT

helm template otel-collector . > "$work/render.yaml"

yq 'select(.kind == "ConfigMap" and .data.relay != null) | .data.relay' "$work/render.yaml" > "$work/relay.yaml"
image=$(yq 'select(.kind == "DaemonSet") | .spec.template.spec.containers[0].image' "$work/render.yaml")
binary=$(yq 'select(.kind == "DaemonSet") | .spec.template.spec.containers[0].command[0]' "$work/render.yaml")

if [ ! -s "$work/relay.yaml" ] || [ -z "$image" ] || [ "$image" = "null" ] || [ "$(printf '%s\n' "$image" | wc -l)" -ne 1 ]; then
  echo "expected one collector ConfigMap and one DaemonSet in the render, got image: '$image'" >&2
  exit 1
fi
[ -n "$binary" ] && [ "$binary" != "null" ] || binary=/otelcol-contrib

# The config reads these from the pod's environment. Any value will do to validate.
env_args=()
while read -r name; do
  [ -n "$name" ] || continue
  case "$name" in
    *_IP) env_args+=(-e "$name=127.0.0.1") ;;
    *) env_args+=(-e "$name=placeholder") ;;
  esac
done < <(grep -o '\${env:[A-Za-z_0-9]*' "$work/relay.yaml" | sed 's/^\${env://' | sort -u)

# file_storage refuses a config whose directory is missing. On the node it is a host
# path the chart mounts; here an empty one stands in.
dir_args=()
while read -r dir; do
  [ -n "$dir" ] && [ "$dir" != "null" ] && dir_args+=(--tmpfs "$dir")
done < <(yq '.extensions // {} | to_entries | .[] | select(.key | test("^file_storage")) | .value.directory' "$work/relay.yaml")

chmod 0644 "$work/relay.yaml"
chmod 0755 "$work"
echo "Validating the rendered config with $image"
docker run --rm "${env_args[@]}" "${dir_args[@]}" -v "$work/relay.yaml:/conf/relay.yaml:ro" \
  --entrypoint "$binary" "$image" validate --config=/conf/relay.yaml
echo "ok: $image accepts the rendered config"
