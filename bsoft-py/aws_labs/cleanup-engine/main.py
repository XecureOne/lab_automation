from __future__ import annotations

import argparse
import json
import sys

from cleanup_engine import cleanup_account


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="AWS Sandbox Cleanup Engine"
    )

    parser.add_argument(
        "--account-id",
        required=True,
        help="12-digit AWS sandbox account ID",
    )

    parser.add_argument(
        "--config",
        default="config.yaml",
    )

    parser.add_argument(
        "--dry-run",
        action="store_true",
    )

    parser.add_argument(
        "--student-id",
        default=None,
        help="Optional student identifier to include in cleanup logs",
    )

    return parser.parse_args()


def main() -> int:
    args = parse_args()

    try:
        result = cleanup_account(
            account_id=args.account_id,
            config_path=args.config,
            dry_run=True if args.dry_run else None,
            student_id=args.student_id,
        )

    except Exception as exc:  # noqa: BLE001
        print(
            json.dumps(
                {
                    "status": "FAIL",
                    "error": str(exc),
                },
                indent=2,
            )
        )

        return 1

    print(
        json.dumps(
            result,
            indent=2,
            default=str,
        )
    )

    return (
        0
        if result["status"] == "PASS"
        else 1
    )


if __name__ == "__main__":
    sys.exit(main())
