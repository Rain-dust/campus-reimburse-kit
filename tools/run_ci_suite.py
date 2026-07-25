"""Run one unittest suite and expose failures as a GitHub annotation."""
from __future__ import annotations

from io import StringIO
from pathlib import Path
import os
import sys
import unittest


BASE_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE_DIR))


def _escape_workflow_command(value: str) -> str:
    return value.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) != 1:
        print("usage: run_ci_suite.py TEST_FILE", file=sys.stderr)
        return 2

    pattern = Path(args[0]).name
    output = StringIO()
    suite = unittest.defaultTestLoader.discover("tests", pattern=pattern)
    if suite.countTestCases() == 0:
        print(f"no tests discovered for {pattern}", file=sys.stderr)
        return 2
    result = unittest.TextTestRunner(stream=output, verbosity=2).run(suite)
    report = output.getvalue()
    print(report, end="")
    if result.wasSuccessful():
        return 0

    if os.environ.get("GITHUB_ACTIONS") == "true":
        details = []
        for test, traceback in (*result.failures, *result.errors):
            details.append(f"{test.id()}\n{traceback}")
        if result.unexpectedSuccesses:
            details.append(
                "unexpected successes:\n"
                + "\n".join(test.id() for test in result.unexpectedSuccesses)
            )
        failure_report = "\n\n".join(details) or report
        print(
            f"::error title={_escape_workflow_command(pattern)} failed::"
            f"{_escape_workflow_command(failure_report[:12000])}"
        )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
