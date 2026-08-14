"""Deploy the hello-world agent to Vertex AI Agent Engine, then query it once.

Run:
    PROJECT_ID=... REGION=... STAGING_BUCKET=gs://... python infra/deploy/hello_agent/deploy.py

Verified against ``google-cloud-aiplatform`` 1.164.0 as installed:

- ``vertexai.init(project=..., location=..., staging_bucket=...)``
- ``vertexai.agent_engines.create(agent_engine=<BaseAgent>, requirements=[...],
  display_name=..., description=..., service_account=..., min_instances=..., max_instances=...)``
  accepts an ADK ``BaseAgent`` directly, so no wrapper is needed for a plain agent.
- ``vertexai.agent_engines.AdkApp(agent=...)`` exists for the case where session, artifact or
  memory services need binding; the hello agent needs none of that.
- ``vertexai.agent_engines.list`` / ``get`` / ``delete`` for lifecycle.

TODO(verify): the streaming query method name on the returned ``AgentEngine`` handle. The
class advertises ``Queryable`` and ``StreamQueryable`` protocols; this script probes the
handle at runtime and reports what it found rather than guessing a method name.

Failure semantics: any deployment failure prints the full exception and exits non-zero. It
never retries — a retried Agent Engine deployment leaves an orphaned resource that quietly
consumes credits. Teardown is a separate, explicit step.
"""

from __future__ import annotations

import os
import sys

REQUIREMENTS = [
    "google-adk==2.7.0",
    "google-cloud-aiplatform==1.164.0",
]


def main() -> int:
    project_id = os.environ.get("PROJECT_ID")
    region = os.environ.get("REGION", "us-central1")
    staging_bucket = os.environ.get("STAGING_BUCKET")

    if not project_id:
        print("PROJECT_ID must be set", file=sys.stderr)
        return 2
    if not staging_bucket:
        print("STAGING_BUCKET must be set (gs://...)", file=sys.stderr)
        return 2

    import vertexai
    from vertexai import agent_engines

    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from agent import root_agent

    print(f"[hello-agent] init project={project_id} region={region}")
    vertexai.init(project=project_id, location=region, staging_bucket=staging_bucket)

    print("[hello-agent] creating agent engine deployment (this takes several minutes)")
    remote = agent_engines.create(
        agent_engine=root_agent,
        requirements=REQUIREMENTS,
        display_name="Drawbridge hello",
        description="Platform check: confirms Agent Engine can host an ADK 2 agent.",
        min_instances=0,
        max_instances=1,
    )

    print(f"[hello-agent] deployed: {remote.resource_name}")

    candidates = ("stream_query", "query", "async_stream_query")
    query_methods = [m for m in candidates if hasattr(remote, m)]
    print(f"[hello-agent] query methods on the handle: {query_methods or 'none found'}")

    if "stream_query" in query_methods:
        print("[hello-agent] querying:")
        for event in remote.stream_query(message="Confirm you are running.", user_id="probe"):
            print(f"  {event}")
    else:
        print("[hello-agent] no stream_query on the handle; record the method names above")

    print("[hello-agent] tear down with: python infra/deploy/hello_agent/teardown.py")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
