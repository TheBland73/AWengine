"""Business agent: greeter (used by the hello workflow and smoke tests)."""

from __future__ import annotations

SYSTEM = """You are a friendly greeter used to smoke-test an agent workflow.
Return one JSON object and nothing else:
{"greeting": "one friendly sentence", "words": <word count>}
"""

TASK = """Greet $name in one short sentence. Return the JSON object."""

AGENT = {
    "name": "greeter",
    "kind": "llm_agent",
    "description": "minimal LLM agent for `awf run hello --fake`",
    "system": SYSTEM,
    "task": TASK,
    "tools": [],
    "artifacts": [],
    "max_steps": 2,
}
