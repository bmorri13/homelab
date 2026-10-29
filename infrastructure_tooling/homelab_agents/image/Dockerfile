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

ARG PYTHON_DEV=cgr.dev/chainguard/python:latest-dev@sha256:01280a9ffd0c9c427d3127540203af6707ebf35cdb0481182aef303c03db239c
ARG PYTHON_RUNTIME=cgr.dev/chainguard/python:latest@sha256:0f47468835f6c443553040eebccc20b0762d9608251be085786007e2e93b2285

# --- builder: virtualenv with the pinned, hash-checked dependencies -------------------
FROM ${PYTHON_DEV} AS builder
USER root
WORKDIR /app
ENV PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
RUN python --version && python -m venv /app/venv
COPY requirements.txt /tmp/requirements.txt
RUN /app/venv/bin/pip install --require-hashes --only-binary=:all: -r /tmp/requirements.txt

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
USER 65532:65532
ENTRYPOINT ["python", "/app/runner.py"]
