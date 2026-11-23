"""Cheap gate: decides whether the heavy pipeline needs to run at all."""
import os
from common import load_json, now_utc, needs_work

work = needs_work(load_json("queue.json", []), load_json("state.json", {}),
                  load_json("config.json", {}), now_utc())
line = f"work={'true' if work else 'false'}"
print(line)
out = os.environ.get("GITHUB_OUTPUT")
if out:
    with open(out, "a") as f:
        f.write(line + "\n")
