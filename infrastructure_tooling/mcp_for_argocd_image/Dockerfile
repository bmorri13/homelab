# syntax=docker/dockerfile:1.10
# argoproj-labs/mcp-for-argocd, rebuilt on Chainguard's Node images.
#
# Upstream's image is node:20-slim (Debian 12, Node 20, which went end of life in
# April 2026). Trivy found 8 critical CVEs in it at v0.9.0: 6 in Debian packages,
# tar from the npm bundled in the base, and proxy-addr from express. This builds
# the same upstream commit, pins proxy-addr to the fixed 2.0.8, and runs it on a
# distroless Node runtime with no shell, no package manager and no npm.
#
# UPSTREAM_REF is the commit of the upstream tag (v0.9.0). To move to a new release:
# set it to that tag's commit, check its packageManager pnpm version, and drop the
# proxy-addr override once upstream's lock has 2.0.8 or later. The two Chainguard
# digests are bumped by Renovate, together.
ARG NODE_DEV=cgr.dev/chainguard/node:latest-dev@sha256:d0db506a46ecf9fca69413576d9621988d1e879956e8f9241c87d7fb5a200a68
ARG NODE_RUNTIME=cgr.dev/chainguard/node:latest@sha256:4ad7301975908b87d321af8b9ed41630dc69015976269965ee459c27541df252

FROM ${NODE_DEV} AS build
# v0.9.0
ARG UPSTREAM_REF=28d15ca69b0c31387cc6ec73d201fd13c5d22b6a
ARG PNPM_VERSION=10.34.1
USER root
ADD --keep-git-dir=false https://github.com/argoproj-labs/mcp-for-argocd.git#${UPSTREAM_REF} /src
WORKDIR /src
# Non-interactive pnpm (it aborts without a TTY when it needs to purge node_modules).
ENV CI=true
RUN npm install --global "pnpm@${PNPM_VERSION}" \
 && npm pkg set pnpm.overrides.proxy-addr=2.0.8 \
 && pnpm install --no-frozen-lockfile \
 && pnpm run build \
 && pnpm prune --prod

FROM ${NODE_RUNTIME}
WORKDIR /app
COPY --from=build /src/package.json /app/package.json
COPY --from=build /src/node_modules /app/node_modules
COPY --from=build /src/dist /app/dist
EXPOSE 3000
# As upstream: overriding the command replaces only the arguments.
ENTRYPOINT ["/usr/bin/node", "/app/dist/index.js"]
CMD ["http"]
# Upstream's user, which the MCPServer's securityContext (runAsUser 1000) expects.
USER 1000
