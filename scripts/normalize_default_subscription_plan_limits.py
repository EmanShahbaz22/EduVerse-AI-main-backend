import argparse
import asyncio
import sys
from datetime import UTC, datetime
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from app.db.database import db


DEFAULT_LIMITS = {
    "starter-basic": {
        "maxStudents": 50,
        "maxTeachers": 5,
        "maxCourses": 10,
        "storageGb": 2,
    },
    "pro-monthly": {
        "maxStudents": 500,
        "maxTeachers": 25,
        "maxCourses": 50,
        "storageGb": 50,
    },
    "enterprise-monthly": {
        "maxStudents": -1,
        "maxTeachers": -1,
        "maxCourses": -1,
        "storageGb": 1000,
    },
}


async def main() -> None:
    parser = argparse.ArgumentParser(
        description="Normalize default subscription plan limits to match the shipped plan tiers.",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Apply changes. Without this flag, runs in dry-run mode.",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=20,
        help="MongoDB ping timeout in seconds.",
    )
    args = parser.parse_args()

    try:
        await asyncio.wait_for(db.command("ping"), timeout=args.timeout)
    except asyncio.TimeoutError:
        print(f"Plan normalization aborted: MongoDB connection timed out after {args.timeout}s.")
        raise SystemExit(2)
    except Exception as exc:
        print(f"Plan normalization aborted: MongoDB connection failed: {exc}")
        raise SystemExit(2)

    docs = await db.subscriptionPlans.find(
        {"code": {"$in": list(DEFAULT_LIMITS.keys())}, "isDeleted": {"$ne": True}}
    ).to_list(length=10)

    print("Default subscription plan normalization:")
    for doc in sorted(docs, key=lambda item: item.get("pricePerMonth", 0)):
        expected = DEFAULT_LIMITS[doc["code"]]
        print(
            f"- {doc['code']}: "
            f"students {doc.get('maxStudents')} -> {expected['maxStudents']}, "
            f"teachers {doc.get('maxTeachers')} -> {expected['maxTeachers']}, "
            f"courses {doc.get('maxCourses')} -> {expected['maxCourses']}, "
            f"storage {doc.get('storageGb')} -> {expected['storageGb']}"
        )

    if not args.apply:
        print("Dry run only. Re-run with --apply to execute changes.")
        return

    now = datetime.now(UTC)
    for doc in docs:
        expected = DEFAULT_LIMITS[doc["code"]]
        await db.subscriptionPlans.update_one(
            {"_id": doc["_id"]},
            {
                "$set": {
                    **expected,
                    "updatedAt": now,
                }
            },
        )

    print(f"Updated {len(docs)} subscription plan(s).")


if __name__ == "__main__":
    asyncio.run(main())
