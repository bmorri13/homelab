"""Build-time: copy the chosen Splunk agent skills into the agents' workspace.

    python select_skills.py <splunk-agent-skills checkout> <workspace>

Only the skills listed in SKILLS are copied, and each SKILL.md loses its
`allowed-tools` frontmatter: a skill must not pre-approve tools (its `shell` and
`web` entries), so the runner's own tool list (runner.py) is the only authority.
Skills that need a shell or write files (splunk-search via splunkctl, the
visualization and setup-page builders, Splunk Cloud admin) are left out.
"""
import pathlib
import re
import shutil
import sys

# Advisory and diagnostic skills that work from read-only evidence, which is all the
# agents have (Splunk searches and REST reads through the gateway). Only the skills
# some agent lists in app/config.py AGENTS; each agent's list is what it may invoke.
SKILLS = [
    "alerting-and-notable-workflows",
    "dashboard-report-alert-performance-advisor",
    "field-extraction-and-cim-mapping",
    "forwarder-and-data-ingest-doctor",
    "hec-setup-and-troubleshooting",
    "index-and-storage-management-advisor",
    "search-and-dashboard-troubleshooter",
    "search-performance-optimizer",
    "splunk-health-monitoring-and-diagnostic-collection",
]

src = pathlib.Path(sys.argv[1]) / "skills"
dst = pathlib.Path(sys.argv[2]) / ".claude" / "skills"
dst.mkdir(parents=True, exist_ok=True)
for name in SKILLS:
    skill = src / name
    if not (skill / "SKILL.md").is_file():
        sys.exit(f"skill {name} not found in the checkout")
    shutil.copytree(skill, dst / name, ignore=shutil.ignore_patterns("agents", "evals.json", "*.js", "*.py", "*.sh"))
    md = dst / name / "SKILL.md"
    text = md.read_text()
    front_end = text.index("\n---", 3)
    front = re.sub(r"(?m)^allowed-tools:\n(?:[ \t]+-.*\n)*", "", text[:front_end + 1])
    md.write_text(front + text[front_end + 1:])
    assert "allowed-tools" not in md.read_text().split("\n---", 1)[0]
print(f"{len(SKILLS)} skills in {dst}")
