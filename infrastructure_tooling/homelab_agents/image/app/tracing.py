"""The trace of one triage: every tool call the agent made, with its input, how much
came back and whether it failed. The runner builds it from the SDK's message stream.

It has two readers. Splunk gets one small event per step (kind=step, no tool output),
so tool use can be charted across runs. The retro (retro.py) gets the steps with a
short preview of each result, in memory only, to judge what slowed the agent down.
"""
import json
import time

from claude_agent_sdk import AssistantMessage, TextBlock, ToolResultBlock, ToolUseBlock, UserMessage

MAX_STEPS = 60          # a triage stops at config.MAX_TURNS; this only bounds memory
INPUT_CHARS = 600       # of a tool call's input kept for the retro
PREVIEW_CHARS = 300     # of a tool result kept for the retro (never sent to Splunk)
NOTE_CHARS = 300        # of what the agent said before the call
HEC_INPUT_CHARS = 500
HEC_ERROR_CHARS = 200


def _clip(text, n):
    text = " ".join(str(text).split())
    return text if len(text) <= n else text[: n - 1] + "…"


def _result_text(content):
    """A tool result is a string, or a list of content blocks; only text counts."""
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    return "".join(c.get("text", "") if isinstance(c, dict) else str(c) for c in content)


def tool_name(block):
    """Gateway tools without the server prefix; a Skill call names the skill."""
    if block.name == "Skill":
        return f"Skill:{(block.input or {}).get('skill', '?')}"
    return block.name.removeprefix("mcp__homelab__")


class Trace:
    def __init__(self):
        self.steps = []
        self._open = {}      # tool_use_id -> step, until its result arrives
        self._note = ""

    def add(self, message):
        """Feed every SDK message here; anything that isn't a tool call or result is ignored."""
        if isinstance(message, AssistantMessage):
            for block in message.content:
                if isinstance(block, TextBlock):
                    self._note = block.text
                elif isinstance(block, ToolUseBlock) and len(self.steps) < MAX_STEPS:
                    step = {"n": len(self.steps) + 1, "tool": tool_name(block),
                            "input": json.dumps(block.input or {}, default=str, sort_keys=True),
                            "note": self._note, "started": time.monotonic(),
                            "ms": None, "is_error": None, "result_chars": None, "preview": ""}
                    self._note = ""
                    self.steps.append(step)
                    self._open[block.id] = step
        elif isinstance(message, UserMessage) and isinstance(message.content, list):
            for block in message.content:
                if isinstance(block, ToolResultBlock):
                    step = self._open.pop(block.tool_use_id, None)
                    if step is None:
                        continue
                    text = _result_text(block.content)
                    step.update(ms=round((time.monotonic() - step["started"]) * 1000),
                                is_error=bool(block.is_error), result_chars=len(text), preview=text)

    def tools_used(self):
        counts = {}
        for s in self.steps:
            counts[s["tool"]] = counts.get(s["tool"], 0) + 1
        return counts

    def metrics(self):
        """Numbers for the item's log event: no model needed to see that a triage
        repeated itself, hit errors or pulled back a lot of text."""
        seen, repeats = set(), 0
        for s in self.steps:
            call = (s["tool"], s["input"])
            repeats += call in seen
            seen.add(call)
        sizes = [s["result_chars"] or 0 for s in self.steps]
        return {"steps": len(self.steps), "tool_errors": sum(1 for s in self.steps if s["is_error"]),
                "repeat_calls": repeats, "result_chars": sum(sizes), "max_result_chars": max(sizes, default=0)}

    def hec_steps(self):
        """One dict per step for Splunk. Tool output stays out: only its size, and the
        message when the call failed (an SPL or permission error, not log data)."""
        out = []
        for s in self.steps:
            row = {"n": s["n"], "tool": s["tool"], "input": _clip(s["input"], HEC_INPUT_CHARS),
                   "ms": s["ms"], "is_error": s["is_error"], "result_chars": s["result_chars"]}
            if s["is_error"]:
                row["error"] = _clip(s["preview"], HEC_ERROR_CHARS)
            out.append(row)
        return out

    def for_retro(self, previews=True):
        out = []
        for s in self.steps:
            row = {"n": s["n"], "tool": s["tool"], "input": _clip(s["input"], INPUT_CHARS),
                   "ms": s["ms"], "is_error": s["is_error"], "result_chars": s["result_chars"]}
            if s["note"]:
                row["agent_said"] = _clip(s["note"], NOTE_CHARS)
            if previews or s["is_error"]:
                row["result_preview"] = _clip(s["preview"], PREVIEW_CHARS)
            out.append(row)
        return out
