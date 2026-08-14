"""Smallest possible ADK 2 agent, for deployment to Vertex AI Agent Engine.

Its only job is to prove the platform works for this project before any product code depends
on it: that an ADK agent packages, uploads, deploys to a managed runtime and answers a query.
It holds no tools, no state and no product logic.

Verified against ``google-adk`` 2.7.0 as installed:

- ``google.adk`` exports ``Agent``, ``Context``, ``Event``, ``Runner`` and ``Workflow``.
- ``Agent`` is ``google.adk.agents.llm_agent.LlmAgent``, constructed with keyword fields
  (``name``, ``model``, ``description``, ``instruction``, ``tools``).
- ``Agent`` inherits from ``google.adk.workflow._base_node.BaseNode``, so an agent *is* a
  workflow node. Graph workflows are built with ``Workflow(name=..., edges=[...])``, the
  ``@node`` decorator, ``START`` and ``Edge``.

The model id below is a placeholder resolved at deploy time from ``MODEL_FAST``.
TODO(verify): the exact Gemini model id available in the target region — ``scripts/probe_geap.py``
resolves this against the live project, which is the only honest way to pin it.
"""

from __future__ import annotations

import os

from google.adk import Agent

MODEL = os.environ.get("MODEL_FAST", "gemini-3.5-flash")

root_agent = Agent(
    name="drawbridge_hello",
    model=MODEL,
    description="Platform check agent. Confirms Agent Engine can host an ADK 2 agent.",
    instruction=(
        "You are a deployment check. Reply in one short sentence confirming you are running, "
        "and name the model you are running on. Do nothing else."
    ),
    tools=[],
)
