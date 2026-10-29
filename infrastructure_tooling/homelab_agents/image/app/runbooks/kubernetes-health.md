# Kubernetes workload health (not available, restarting, stuck pods)

Check the Argo CD application that owns the namespace (resource tree, events, workload logs), Kubernetes events in `index=k8s` for the namespace (FailedScheduling, BackOff, OOMKilled, image pull errors), and the pod's own logs just before restarts. Note when it started and whether it lines up with a sync or an image update (Renovate merges bump images often). Single-node k3s: resource pressure on k3s-01 (memory, disk) affects everything at once.
