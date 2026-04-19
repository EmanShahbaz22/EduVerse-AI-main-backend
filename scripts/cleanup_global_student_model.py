import argparse
import asyncio
import sys
from datetime import datetime, UTC
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from app.db.database import db, ensure_indexes


LEGACY_STUDENT_INDEX_NAMES = (
    "students_user_tenant_idx",
    "students_tenant_enrolled_courses_idx",
)


async def _collect_plan() -> dict:
    student_user_query = {"role": "student", "tenantId": {"$exists": True}}
    student_profile_query = {"tenantId": {"$exists": True}}

    student_user_count = await db.users.count_documents(student_user_query)
    student_profile_count = await db.students.count_documents(student_profile_query)
    student_indexes = await db.students.index_information()
    legacy_indexes = [
        name for name in LEGACY_STUDENT_INDEX_NAMES if name in student_indexes
    ]

    return {
        "student_user_query": student_user_query,
        "student_profile_query": student_profile_query,
        "student_user_count": student_user_count,
        "student_profile_count": student_profile_count,
        "legacy_indexes": legacy_indexes,
    }


async def _apply_cleanup(plan: dict) -> None:
    now = datetime.now(UTC)

    user_result = await db.users.update_many(
        plan["student_user_query"],
        {
            "$unset": {"tenantId": ""},
            "$set": {"updatedAt": now},
        },
    )

    student_result = await db.students.update_many(
        plan["student_profile_query"],
        {
            "$unset": {"tenantId": ""},
            "$set": {"updatedAt": now},
        },
    )

    for index_name in plan["legacy_indexes"]:
        await db.students.drop_index(index_name)

    await ensure_indexes()

    print("Cleanup applied successfully.")
    print(f"- Student user records updated: {user_result.modified_count}")
    print(f"- Student profile records updated: {student_result.modified_count}")
    print(f"- Legacy student indexes dropped: {len(plan['legacy_indexes'])}")


async def main() -> None:
    parser = argparse.ArgumentParser(
        description="Remove legacy tenant binding from global student records.",
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
        print(f"Cleanup aborted: MongoDB connection timed out after {args.timeout}s.")
        raise SystemExit(2)
    except Exception as exc:
        print(f"Cleanup aborted: MongoDB connection failed: {exc}")
        raise SystemExit(2)

    plan = await _collect_plan()

    print("Global student cleanup plan:")
    print(f"- Student user records with legacy tenantId: {plan['student_user_count']}")
    print(f"- Student profile records with legacy tenantId: {plan['student_profile_count']}")
    print(
        f"- Legacy student indexes present: "
        f"{', '.join(plan['legacy_indexes']) if plan['legacy_indexes'] else 'none'}"
    )

    if not args.apply:
        print("Dry run only. Re-run with --apply to execute changes.")
        return

    await _apply_cleanup(plan)


if __name__ == "__main__":
    asyncio.run(main())
