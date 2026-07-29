#!/usr/bin/env python3
"""Print the FluentFlow Local readiness report.

Exit code 0 when every required check passes (warnings allowed), 1 otherwise.
The launcher runs this before booting the local backend so users see readable
remediation instead of a mid-task failure.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.core.local_readiness import (  # noqa: E402
    failed_required_checks,
    format_report,
    run_readiness_checks,
)


def main() -> int:
    checks = run_readiness_checks()
    print(format_report(checks))
    failed = failed_required_checks(checks)
    if failed:
        print(f"\n{len(failed)} 项必需检查未通过，请按上面的提示处理后重试。")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
