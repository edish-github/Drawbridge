"""Delete the hello-world Agent Engine deployment.

A hello-world that outlives its purpose is a credit drain. This runs after the platform check
passes and the result is recorded in the capability report.

Failure semantics: lists deployments by display name and deletes only exact matches. It never
deletes by prefix and never deletes everything in the project, because a teardown script that
can over-delete is more dangerous than the resources it removes.
"""

from __future__ import annotations

import os
import sys

DISPLAY_NAME = "Drawbridge hello"


def main() -> int:
    project_id = os.environ.get("PROJECT_ID")
    region = os.environ.get("REGION", "us-central1")

    if not project_id:
        print("PROJECT_ID must be set", file=sys.stderr)
        return 2

    import vertexai
    from vertexai import agent_engines

    vertexai.init(project=project_id, location=region)

    matches = [a for a in agent_engines.list() if a.display_name == DISPLAY_NAME]
    if not matches:
        print(f"[hello-agent] no deployment named {DISPLAY_NAME!r}; nothing to do")
        return 0

    for a in matches:
        print(f"[hello-agent] deleting {a.resource_name}")
        agent_engines.delete(a.resource_name, force=True)

    print("[hello-agent] done")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
