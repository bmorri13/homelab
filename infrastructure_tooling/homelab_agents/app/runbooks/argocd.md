# Argo CD application status

Most apps have manual sync, so OutOfSync after a merge to main means the change hasn't been deployed yet; normal for a short while, a missed deploy if it lasts. Degraded/Missing means a resource is unhealthy or absent. Use the Argo CD tools: application status and revision, resource tree (which resource is unhealthy), events and workload logs. Say which resource is out of sync or unhealthy and why, and whether syncing would fix it.
