"""OpenTelemetry traces to Phoenix, when an OTLP endpoint is configured. Off otherwise.

Not to be confused with tracing.py, which records each triage's tool calls for Splunk
and the retro. This exports the same story as OTLP so a trace viewer can show it.

Two layers end up in one trace (the pattern of the software-factory agent):
  - ours: one span per triage, retro or self-test, with the item and the verdict as
    attributes and every tool call as a span event (the fields Splunk gets: input,
    duration, size, error; never the tool output). Each carries the run's id as the
    OpenInference session.id, so Phoenix's Sessions tab shows one row per run.
  - the Claude Agent SDK's: its bundled CLI exports a span for each model request and
    tool call, nested under ours because the SDK hands it the current span as parent.

The SDK's layer is best effort (its tracing is in beta), so ours carries the whole
record by itself. What the CLI includes of prompts and tool content is its own opt-in
(OTEL_LOG_USER_PROMPTS, OTEL_LOG_TOOL_DETAILS, OTEL_LOG_TOOL_CONTENT), set in the
CronJob, not here.
"""
import contextlib
import json
import os
import shutil
import sys
import tempfile

SERVICE = "homelab-agents"
_tracer = _provider = None


def enabled():
    return bool(os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT") or os.environ.get("OTEL_EXPORTER_OTLP_TRACES_ENDPOINT"))


def setup():
    global _tracer, _provider
    if _tracer or not enabled():
        return
    from opentelemetry import trace
    from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor
    _provider = TracerProvider(resource=Resource.create({"service.name": os.environ.get("OTEL_SERVICE_NAME", SERVICE)}))
    _provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter()))
    trace.set_tracer_provider(_provider)
    _tracer = trace.get_tracer(SERVICE)


def child_env():
    """What the SDK's CLI needs to export its own spans to the same place."""
    if not enabled():
        return {}
    return {"CLAUDE_CODE_ENABLE_TELEMETRY": "1", "CLAUDE_CODE_ENHANCED_TELEMETRY_BETA": "1",
            "OTEL_TRACES_EXPORTER": "otlp", "OTEL_METRICS_EXPORTER": "none", "OTEL_LOGS_EXPORTER": "none",
            "OTEL_SERVICE_NAME": os.environ.get("OTEL_SERVICE_NAME", SERVICE),
            "OTEL_EXPORTER_OTLP_PROTOCOL": os.environ.get("OTEL_EXPORTER_OTLP_PROTOCOL", "http/protobuf"),
            "OTEL_TRACES_EXPORT_INTERVAL": "1000",
            # The CLI lives for one triage: give it time to hand over its last spans
            # before it exits, and have it say on stderr when an export fails.
            "CLAUDE_CODE_OTEL_FLUSH_TIMEOUT_MS": "5000", "CLAUDE_CODE_OTEL_SHUTDOWN_TIMEOUT_MS": "5000",
            "CLAUDE_CODE_OTEL_DIAG_STDERR": "1"}


def diag(line):
    """The SDK's stderr callback: pass on only what the CLI says about telemetry."""
    if enabled() and any(w in line.lower() for w in ("otel", "otlp", "telemetry", "exporter")):
        print(f"claude telemetry: {line.strip()[:300]}", file=sys.stderr)


@contextlib.contextmanager
def sdk_env(claude_env):
    """The environment for one SDK call: the credential, the CLI's telemetry settings and,
    while tracing is on, a configuration directory of its own that is removed after.
    The CLI's tracing only works on the first run in a directory: on later runs its spans
    arrive without their parent and mostly missing (software-factory, 2026-11-18; cause
    not known). Only with the credential in the environment: a login kept in the default
    directory (--local-login) would not be found in a new one."""
    env = dict(claude_env)
    if not enabled():
        yield env
        return
    env.update(child_env())
    if not (env.get("CLAUDE_CODE_OAUTH_TOKEN") or env.get("ANTHROPIC_API_KEY")):
        yield env
        return
    path = tempfile.mkdtemp(prefix="claude-config-")
    try:
        yield {**env, "CLAUDE_CONFIG_DIR": path}
    finally:
        shutil.rmtree(path, ignore_errors=True)


def _value(v):
    return v if isinstance(v, (str, bool, int, float)) else json.dumps(v, default=str)


class Span:
    def __init__(self, span):
        self.span = span

    def set(self, **attributes):
        """Attributes under 'agent.' unless the key has a dot (OpenInference names do)."""
        if self.span is None:
            return
        for key, value in attributes.items():
            if value is not None:
                self.span.set_attribute(key if "." in key else f"agent.{key}", _value(value))

    def event(self, name, **attributes):
        if self.span is not None:
            self.span.add_event(name, {k: _value(v) for k, v in attributes.items() if v is not None})

    def failed(self, error):
        if self.span is not None:
            from opentelemetry.trace import Status, StatusCode
            self.span.set_status(Status(StatusCode.ERROR, str(error)[:300]))


@contextlib.contextmanager
def span(name, **attributes):
    """A span that is current while the block runs, so the SDK's spans nest under it.
    With tracing off this does nothing and costs nothing."""
    setup()
    if _tracer is None:
        yield Span(None)
        return
    with _tracer.start_as_current_span(name, attributes={"openinference.span.kind": "AGENT"}) as s:
        wrapped = Span(s)
        wrapped.set(**attributes)
        if attributes.get("session.id"):
            _claim_session(attributes["session.id"])
        yield wrapped


def _claim_session(session_id):
    """Phoenix files a trace under the first session.id it receives for it and ignores
    later ones. Claude Code puts its own session id on every span it exports while the
    call runs, and our span only goes out when it ends, so Claude's id would win. A
    short child span with ours, sent before the SDK call starts, gets there first."""
    with _tracer.start_as_current_span("session", attributes={"openinference.span.kind": "CHAIN",
                                                             "session.id": session_id}):
        pass
    try:
        _provider.force_flush(5_000)
    except Exception as e:
        print(f"otel: {e}", file=sys.stderr)


def steps(span_, trace):
    """Every tool call of a triage as a span event, with what Splunk gets of it."""
    for row in trace.hec_steps():
        span_.event(f"tool {row['tool']}", **row)


def flush():
    """Hand over the spans before the process exits. Never fails the run."""
    if _provider is not None:
        try:
            _provider.force_flush(10_000)
        except Exception as e:
            print(f"otel: {e}", file=sys.stderr)
