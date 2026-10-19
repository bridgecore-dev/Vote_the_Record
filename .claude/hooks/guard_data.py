"""PreToolUse hook: require the owner's approval for anything touching data/.

Returns permissionDecision "ask" (which prompts even in auto mode) when:
- Edit/Write/NotebookEdit targets a path under <repo>/data/
- a Bash/PowerShell command mentions a data/ path or changes into data

Anything else passes through silently. See CLAUDE.md rule 3a.
"""

import json
import os
import re
import sys

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DATA = os.path.normcase(os.path.join(REPO, "data"))

# data/ or data\ as a path segment (not metadata/), or cd/pushd/Set-Location into data.
SHELL_DATA = re.compile(
    r"(?<![\w.-])data[\\/]|\b(cd|pushd|set-location|sl)\s+[\"']?(\.[\\/])?data\b",
    re.IGNORECASE,
)


def under_data(path, cwd):
    full = os.path.normcase(os.path.abspath(os.path.join(cwd or REPO, path)))
    return full == DATA or full.startswith(DATA + os.sep)


SWITCH = os.path.join(REPO, ".claude", "data-guard.json")


def guard_on():
    """On/off switch: .claude/data-guard.json {"ask_before_data_edits": true|false}. On if missing."""
    try:
        with open(SWITCH, encoding="utf-8") as f:
            return bool(json.load(f).get("ask_before_data_edits", True))
    except (OSError, ValueError):
        return True


def main():
    if not guard_on():
        return



if __name__ == "__main__":
    main()
