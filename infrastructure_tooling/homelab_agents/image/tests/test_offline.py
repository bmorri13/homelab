"""Offline checks of the runner: the trace built from SDK messages, the retro findings
store, and whole runs with Vault, the gateway, Slack, HEC and the model replaced by
fakes. No network, no credentials, nothing posted. From image/:

    uv run --with-requirements requirements.in python tests/test_offline.py

It prints "all ok" and exits 0, or stops at the first failed assertion.
"""
import asyncio, json, os, pathlib, sys, tempfile
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "app"))
tmp = tempfile.mkdtemp()
os.environ["STATE_DIRECTORY"] = tmp
from claude_agent_sdk import AssistantMessage, UserMessage, ToolUseBlock, ToolResultBlock, TextBlock, ResultMessage, SystemMessage
import config, runner, retro, clients, queues
from tracing import Trace
from state import State

def amsg(*blocks): return AssistantMessage(content=list(blocks), model="m")
def umsg(*blocks): return UserMessage(content=list(blocks))

t = Trace()
t.add(amsg(TextBlock("checking the audit log"), ToolUseBlock("a", "mcp__homelab__splunk-agent_splunk_run_query", {"query": "search index=audit", "earliest_time": "-1h"})))
t.add(umsg(ToolResultBlock("a", [{"type": "text", "text": "x" * 5000}], False)))
t.add(amsg(ToolUseBlock("b", "mcp__homelab__splunk-agent_splunk_run_query", {"earliest_time": "-1h", "query": "search index=audit"})))
t.add(umsg(ToolResultBlock("b", "Error in 'search' command", True)))
t.add(amsg(ToolUseBlock("c", "Skill", {"skill": "search-and-dashboard-troubleshooter"})))
t.add(amsg(ToolUseBlock("d", "WebFetch", {"url": "https://example.com"})))   # never answered (timeout)
m = t.metrics()
assert m == {"steps": 4, "tool_errors": 1, "repeat_calls": 1, "result_chars": 5000 + 25, "max_result_chars": 5000}, m
assert t.tools_used() == {"splunk-agent_splunk_run_query": 2, "Skill:search-and-dashboard-troubleshooter": 1, "WebFetch": 1}
h = t.hec_steps()
assert "preview" not in json.dumps(h) and "xxxx" not in json.dumps(h)          # no tool output to Splunk
assert h[1]["error"] == "Error in 'search' command" and "error" not in h[0]
assert h[3]["is_error"] is None and h[3]["result_chars"] is None
r = t.for_retro()
assert r[0]["agent_said"] == "checking the audit log" and len(r[0]["result_preview"]) == 300
assert "result_preview" not in t.for_retro(previews=False)[0] and "result_preview" in t.for_retro(previews=False)[1]
json.dumps(h); json.dumps(r)
print("trace ok")

s = State("sec")
f = {"repeat_of": 0, "category": "context_docs", "target": "runbooks/kubernetes.md", "title": "T1", "proposal": "P"}
a = s.save_finding(f); assert a == {"id": 1, "category": "context_docs", "target": "runbooks/kubernetes.md", "title": "T1", "times_seen": 1}, a
b = s.save_finding({**f, "repeat_of": 1, "title": "reworded"}); assert b["id"] == 1 and b["times_seen"] == 2 and b["title"] == "T1"
assert s.db.execute("select observation from retro_findings where id=1").fetchone()[0] is None
s.save_finding({**f, "repeat_of": 1, "observation": "seen again"})
assert s.db.execute("select observation, times_seen from retro_findings where id=1").fetchone()[:] == ("seen again", 3)
c = s.save_finding({**f, "repeat_of": 999, "title": "T2"}); assert c["id"] == 2 and c["times_seen"] == 1     # unknown id = new
assert State("ops").save_finding({**f, "repeat_of": 1})["times_seen"] == 1                                       # other agent's id = new
k = s.known_findings(); assert [x["id"] for x in k] == [1, 2] and k[0]["times_seen"] == 3, k
assert s.known_findings(now=__import__("time").time() + 15 * 86400) == []
print("state ok")

# ---- whole run with fakes -------------------------------------------------------------
ITEMS = [{"key": f"k{i}", "source": "detection", "title": f"HL - Kubernetes thing {i}", "severity": "medium",
          "host": "k3s-01", "facts": {"user": "system:admin"}, "runbook": "kubernetes.md"} for i in (1, 2)]
class FakeVault:
    def kv(self, path): return {"webhook_url": "https://hooks.slack.com/x", "token": "hec", "claude_oauth_token": "tok"}
    def close(self): pass
clients.Vault = FakeVault
clients.read_token = lambda p: "gw"
clients.Gateway = lambda tok: object()
queues.COLLECTORS["sec"] = lambda gw: [dict(i) for i in ITEMS]
posted, sent = [], []
clients.slack_post = lambda w, m: posted.append(m)
clients.hec_send = lambda tok, ev: sent.extend(ev)
runner.time.sleep = lambda s: None
TRIAGE = {"verdict": "suspicious", "confidence": "medium", "severity": "high", "headline": "h", "summary": "s",
          "evidence": [], "next_steps": []}
RETRO = {"summary": "fine", "findings": [
    {"repeat_of": 0, "category": "deterministic_filter", "target": "detection:HL - Kubernetes thing", "severity": "high",
     "title": "Same actor every time", "observation": "items 1 and 2", "proposal": "exclude X", "items": [1, 2, 7]}]}
calls = {"triage": 0, "retro": 0, "fail_retro": False, "prompts": []}
def result(out, cost):
    return ResultMessage(subtype="success", duration_ms=5, duration_api_ms=5, is_error=False, num_turns=2,
                         session_id="s", total_cost_usd=cost, usage={}, structured_output=out)
async def fake_query(prompt, options):
    calls["prompts"].append(prompt)
    if options.skills == ["retro"]:
        calls["retro"] += 1
        assert options.mcp_servers == {} and options.tools == ["Skill", "Read"] and options.model == config.RETRO_MODEL
        if calls["fail_retro"]:
            raise RuntimeError("boom")
        yield amsg(ToolUseBlock("r", "Skill", {"skill": "retro"}))
        yield result(RETRO, 0.05)
        return
    calls["triage"] += 1
    yield SystemMessage(subtype="init", data={"tools": ["mcp__homelab__splunk-agent_splunk_run_query", "Skill"], "mcp_servers": [], "skills": []})
    yield amsg(ToolUseBlock("a", "mcp__homelab__splunk-agent_splunk_run_query", {"query": "search index=audit SECRETLOGLINE"}))
    yield umsg(ToolResultBlock("a", "row row row", False))
    if calls["triage"] == 2:
        raise RuntimeError("api down")           # second item fails after one step
    yield result(TRIAGE, 0.2)
runner.query = retro.query = fake_query

rc = asyncio.run(runner.run("sec", False, False, 10))
assert rc == 0
run_ev = [e["event"] for e in sent if e["event"]["kind"] == "run"][0]
kinds = [e["event"]["kind"] for e in sent]
assert kinds.count("item") == 2 and kinds.count("step") == 2 and kinds.count("retro") == 1 and kinds[-1] == "run", kinds
assert run_ev["status"] == "ok" and run_ev["retro_findings"] == 1 and run_ev["retro_new"] == 1 and run_ev["retro_skill_invoked"]
assert abs(run_ev["cost_usd"] - 0.25) < 1e-9 and run_ev["retro_cost_usd"] == 0.05, run_ev
ret = [e["event"] for e in sent if e["event"]["kind"] == "retro"][0]
assert ret["item_keys"] == ["k1", "k2"] and ret["times_seen"] == 1 and ret["finding_id"] and ret["target"].startswith("detection:")
items = [e["event"] for e in sent if e["event"]["kind"] == "item"]
assert items[0]["steps"] == 1 and items[0]["tool_errors"] == 0 and items[0]["denied"] == []
assert items[1]["triage"] is None and items[1]["steps"] == 1 and items[1]["error"] == "api down"   # failed triage keeps its trace
rp = calls["prompts"][-1]
assert "<system_prompt file=\"prompts/sec.md\">" in rp and "<runbook file=\"runbooks/kubernetes.md\">" in rp
assert "splunk-agent_splunk_run_query" in rp and "api down" in rp and '"item": 2' in rp and "<known_findings>\n[" in rp
assert len(posted) == 1                                                                              # one alert card, retro posts nothing
for e in sent: assert len(json.dumps(e)) < 5000, len(json.dumps(e))
print("run ok:", {k: v for k, v in run_ev.items() if k.startswith("retro")})

# second run: retro fails -> run still ok, and the known finding is offered to the next retro
sent.clear(); posted.clear(); calls["fail_retro"] = True; calls["triage"] = 10
for i in ITEMS: i["key"] += "b"
rc = asyncio.run(runner.run("sec", False, False, 10))
run_ev = [e["event"] for e in sent if e["event"]["kind"] == "run"][0]
assert rc == 0 and run_ev["status"] == "ok" and run_ev["retro_error"] == "RuntimeError: boom" and "retro_findings" not in run_ev
assert '"title": "Same actor every time"' in calls["prompts"][-1]
# third: nothing new -> no retro call; RETRO=0 -> no retro call
n = calls["retro"]; sent.clear(); calls["fail_retro"] = False
asyncio.run(runner.run("sec", False, False, 10)); assert calls["retro"] == n
for i in ITEMS: i["key"] += "c"
config.RETRO_ENABLED = False; asyncio.run(runner.run("sec", False, False, 10)); assert calls["retro"] == n
config.RETRO_ENABLED = True
for i in ITEMS: i["key"] += "d"
config.RETRO_START_BY_S = -1; sent.clear(); asyncio.run(runner.run("sec", False, False, 10)); assert calls["retro"] == n
assert [e["event"] for e in sent if e["event"]["kind"] == "run"][0]["retro_error"].startswith("skipped")
print("guards ok")

# a state.db made before the observation column existed is migrated on open
import sqlite3
old = sqlite3.connect(os.path.join(tmp, "state.db"))
old.executescript("DROP TABLE retro_findings; CREATE TABLE retro_findings (id INTEGER PRIMARY KEY AUTOINCREMENT, agent TEXT NOT NULL, "
                  "category TEXT NOT NULL, target TEXT NOT NULL, title TEXT NOT NULL, proposal TEXT NOT NULL, first_seen REAL NOT NULL, "
                  "last_seen REAL NOT NULL, times_seen INTEGER NOT NULL DEFAULT 1);")
old.close()
assert State("sec").save_finding({**f, "observation": "o"})["times_seen"] == 1
print("migration ok")

# OpenTelemetry (otel.py): off without an endpoint; on, a run exports its spans to the
# endpoint (a fake Phoenix on loopback), each SDK call gets a fresh CLAUDE_CONFIG_DIR that
# is removed after, and tool output stays out of the spans.
import http.server, threading
import otel
with otel.sdk_env({"CLAUDE_CODE_OAUTH_TOKEN": "tok"}) as env:
    assert env == {"CLAUDE_CODE_OAUTH_TOKEN": "tok"}, env                   # tracing off: unchanged
received = []
class Sink(http.server.BaseHTTPRequestHandler):
    def do_POST(self):
        received.append((self.path, self.rfile.read(int(self.headers["Content-Length"]))))
        self.send_response(200); self.end_headers()
    def log_message(self, *a): pass
srv = http.server.HTTPServer(("127.0.0.1", 0), Sink)
threading.Thread(target=srv.serve_forever, daemon=True).start()
os.environ["OTEL_EXPORTER_OTLP_ENDPOINT"] = f"http://127.0.0.1:{srv.server_port}"
os.environ["OTEL_SERVICE_NAME"] = "homelab-agent-sec"
with otel.sdk_env({}) as env:                                               # --local-login: no fresh dir
    assert "CLAUDE_CONFIG_DIR" not in env and env["CLAUDE_CODE_ENABLE_TELEMETRY"] == "1"
envs = []
inner = fake_query
async def recording_query(prompt, options):
    envs.append(dict(options.env))
    assert os.path.isdir(options.env["CLAUDE_CONFIG_DIR"])
    async for m in inner(prompt, options):
        yield m
runner.query = retro.query = recording_query
config.RETRO_START_BY_S = 1260; calls["triage"] = 20; sent.clear()
for i in ITEMS: i["key"] += "e"
rc = asyncio.run(runner.run("sec", False, False, 10)); otel.flush()
assert rc == 0 and len(envs) == 3, envs                                     # 2 triages + the retro
dirs = {e["CLAUDE_CONFIG_DIR"] for e in envs}
assert len(dirs) == 3 and not any(os.path.exists(d) for d in dirs), dirs     # one each, removed after
assert all(e["CLAUDE_CODE_OAUTH_TOKEN"] == "tok" and e["OTEL_TRACES_EXPORTER"] == "otlp" for e in envs)
body = b"".join(b for path, b in received if path == "/v1/traces")
for want in (b"triage sec", b"retro sec", b"tool splunk-agent_splunk_run_query", b"SECRETLOGLINE",
             b"agent.verdict", b"suspicious", b"homelab-agent-sec", b"AGENT", b"Same actor every time"):
    assert want in body, want
assert b"row row row" not in body                                           # tool output never exported
run_id = [e["event"] for e in sent if e["event"]["kind"] == "run"][0]["run_id"]
assert body.count(b"session.id") >= 3 and body.count(run_id.encode()) >= 3, run_id   # every span in the run's session
assert [e["event"]["kind"] for e in sent].count("step") == 2                 # Splunk steps unchanged
srv.shutdown()
print("otel ok")
print("all ok")
