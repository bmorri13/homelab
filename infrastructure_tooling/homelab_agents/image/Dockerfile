# syntax=docker/dockerfile:1
# Homelab agents: the deterministic runner (app/), the Claude Agent SDK (which bundles
# its own Claude Code binary) and a pinned selection of Splunk's agent skills.
#
# Layered build on Chainguard images (Wolfi, glibc):
#   builder  python:latest-dev  pip + shell: builds a virtualenv from the hash-locked
#                               requirements
#   skills   python:latest-dev  fetches the Splunk skills at a pinned commit and selects
#                               the read-only ones
#   runtime  python:latest      minimal: no shell, no package manager, no pip, runs as
#                               nonroot (65532). Gets only the venv, the app and the skills.
# Both images are pinned by digest and must stay the same Chainguard build (same
# Python): bump them together. Nothing secret is built in; credentials are read at run
# time from Vault and projected ServiceAccount tokens.

ARG PYTHON_DEV=cgr.dev/chainguard/python:latest-dev@sha256:c0328ed1997b6826d7d2ae4ea972345d3487a80f15ceb1d26f211f7c02f8c177
ARG PYTHON_RUNTIME=cgr.dev/chainguard/python:latest@sha256:4b2cec05cea94a58e635f21ca5d7c642d24ec58258008bf9341a0c062de5d99d

# --- builder: virtualenv with the pinned, hash-checked dependencies -------------------
FROM ${PYTHON_DEV} AS builder
USER root
WORKDIR /app
ENV PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
RUN python --version && python -m venv /app/venv
COPY requirements.txt /tmp/requirements.txt
RUN /app/venv/bin/pip install --require-hashes --only-binary=:all: -r /tmp/requirements.txt \
 # pip has no job at run time; don't ship it into the runtime image.
 && /app/venv/bin/python -m pip uninstall --yes pip \
 && find /app/venv -name __pycache__ -prune -exec rm -rf {} +

# --- skills: Splunk agent skills, pinned to one commit -----------------------------------
FROM ${PYTHON_DEV} AS skills
USER root
# https://github.com/splunk/splunk-agent-skills (Apache-2.0). Bump the commit on purpose
# and review the diff: these files become part of the agents' instructions.
ARG SKILLS_COMMIT=488d7c76d9f2d0804e8ec5ae13ff1f65ab717831
ADD --keep-git-dir=false https://github.com/splunk/splunk-agent-skills.git#${SKILLS_COMMIT} /src/splunk-agent-skills
COPY select_skills.py /select_skills.py
RUN python /select_skills.py /src/splunk-agent-skills /workspace \
 && cp /src/splunk-agent-skills/LICENSE /workspace/.claude/skills/LICENSE-splunk-agent-skills

# --- runtime: minimal, nonroot, read-only content ------------------------------------------
FROM ${PYTHON_RUNTIME}
ENV PATH="/app/venv/bin:${PATH}" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1
WORKDIR /app
# Owned by root and not writable by the runtime user: the agent can't change its own code.
COPY --from=builder /app/venv /app/venv
COPY app/ /app/
COPY --from=skills /workspace /app/workspace
# Our own skills (skills/): retro, which the end-of-run retro call invokes. The triage
# agents don't list it (app/config.py AGENTS), so they can't.
COPY skills/ /app/workspace/.claude/skills/
USER 65532:65532
ENTRYPOINT ["python", "/app/runner.py"]
