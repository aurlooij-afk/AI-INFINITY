from __future__ import annotations
"""CLI wrapper for AI Infinity's real runtime self-test."""
import json
import sys

def main() -> int:
    import professional_creator_fabric as fabric
    result = fabric.self_test()
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if result.get("passed") else 1

if __name__ == "__main__":
    raise SystemExit(main())
