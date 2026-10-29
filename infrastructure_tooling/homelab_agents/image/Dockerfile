# syntax=docker/dockerfile:1
# Homelab agents: the deterministic runner (app/) plus the Claude Agent SDK, which
# bundles its own Claude Code binary, plus a pinned selection of Splunk's agent
# skills. Nothing secret is built in: every credential is read at run time from
# Vault (or a projected ServiceAccount token).

# --- Splunk agent skills, pinned to one commit -------------------------------------
FROM python:3.14-slim@sha256:cd89c30eda26ee911a4611b18680086e0ea42e6f5f8cbe653d07fa7958c9cec8 AS skills
# https://github.com/splunk/splunk-agent-skills (Apache-2.0). Bump the commit on purpose
# and review the diff: these files become part of the agents' instructions.
ARG SKILLS_COMMIT=488d7c76d9f2d0804e8ec5ae13ff1f65ab717831
ADD --keep-git-dir=false https://github.com/splunk/splunk-agent-skills.git#${SKILLS_COMMIT} /src/splunk-agent-skills
COPY select_skills.py /select_skills.py
RUN python /select_skills.py /src/splunk-agent-skills /workspace \
 && cp /src/splunk-agent-skills/LICENSE /workspace/.claude/skills/LICENSE-splunk-agent-skills

# --- runtime -----------------------------------------------------------------------
FROM python:3.14-slim@sha256:cd89c30eda26ee911a4611b18680086e0ea42e6f5f8cbe653d07fa7958c9cec8
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1
COPY requirements.txt /tmp/requirements.txt
RUN pip install --require-hashes -r /tmp/requirements.txt && rm /tmp/requirements.txt \
 && useradd --uid 10001 --user-group --no-create-home --home-dir /home/agent agent
COPY app/ /app/
COPY --from=skills /workspace /app/workspace
USER 10001:10001
WORKDIR /app
ENTRYPOINT ["python", "/app/runner.py"]
