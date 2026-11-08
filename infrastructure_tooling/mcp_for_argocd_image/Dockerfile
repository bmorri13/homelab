# syntax=docker/dockerfile:1.28
# argoproj-labs/mcp-for-argocd, rebuilt on Chainguard's Node images.
#
# Upstream's image is node:20-slim (Debian 12, Node 20, which went end of life in
# April 2026). Trivy found 8 critical CVEs in it at v0.9.0: 6 in Debian packages,
# tar from the npm bundled in the base, and proxy-addr from express. This builds
# the same upstream commit, raises the vulnerable npm dependencies to fixed
# versions (overrides.json, pnpm overrides within the same major version), and runs
# it on a distroless Node runtime with no shell, no package manager and no npm.
#
# UPSTREAM_REF is the commit of the upstream tag (v0.9.0). To move to a new release:
# set it to that tag's commit, check its packageManager pnpm version, and drop each
# override in overrides.json once upstream's lock has that version or later. The
# two Chainguard digests are bumped by Renovate, together.
ARG NODE_DEV=cgr.dev/chainguard/node:latest-dev@sha256:81d77836dacab0c421ab4fbbf1cc2ffe56f9bc64793c1b077bbc1a30d61e1c05
ARG NODE_RUNTIME=cgr.dev/chainguard/node:latest@sha256:6a066f8decea77c67ef2772a78fad81df7a78a346d0b22a05fc9dac14537f2b0

FROM ${NODE_DEV} AS build
# v0.9.0
ARG UPSTREAM_REF=28d15ca69b0c31387cc6ec73d201fd13c5d22b6a
ARG PNPM_VERSION=10.34.1
USER root
ADD --keep-git-dir=false https://github.com/argoproj-labs/mcp-for-argocd.git#${UPSTREAM_REF} /src
WORKDIR /src
# Non-interactive pnpm (it aborts without a TTY when it needs to purge node_modules).
ENV CI=true
COPY overrides.json /tmp/overrides.json
RUN npm install --global "pnpm@${PNPM_VERSION}" \
 && node -e 'const fs=require("fs"); const p=JSON.parse(fs.readFileSync("package.json")); p.pnpm={...(p.pnpm||{}), overrides:{...((p.pnpm||{}).overrides||{}), ...JSON.parse(fs.readFileSync("/tmp/overrides.json"))}}; fs.writeFileSync("package.json", JSON.stringify(p, null, 2))' \
 && pnpm install --no-frozen-lockfile \
 && for pkg in $(node -p 'Object.keys(require("/tmp/overrides.json")).join(" ")'); do pnpm why --prod "$pkg" | grep -E "^${pkg} " || true; done \
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
