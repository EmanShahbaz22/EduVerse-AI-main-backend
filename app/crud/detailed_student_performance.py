from bson import ObjectId
from app.db.database import (
    courses_collection,
    assignments_collection,
    assignment_submissions_collection,
    quizzes_collection,
    quiz_submissions_collection,
    students_collection,
    users_collection
)
from app.utils.mongo import fix_object_ids

async def get_detailed_student_performance(teacher_id: str, student_id: str, tenant_id: str):
    """
    Fetches detailed quiz and assignment scores for a specific student, 
    scoped only to the courses owned by the given teacher.
    """
    toid = ObjectId(tenant_id)
    
    # 1. Fetch valid courses owned by this teacher
    teacher_query = {
        "tenantId": toid,
        "$or": [
            {"teacherId": teacher_id},
            {"teacherId": ObjectId(teacher_id) if ObjectId.is_valid(teacher_id) else None},
        ],
    }
    courses = await courses_collection.find(teacher_query).to_list(length=None)
    course_ids = [str(c["_id"]) for c in courses]
    
    if not course_ids:
        return []

    # 2. Get the student's name
    student = await students_collection.find_one({"_id": ObjectId(student_id)})
    if not student:
        return []

    student_name = student.get("studentName", "Unknown Student")
    if student.get("userId"):
        user = await users_collection.find_one({"_id": student["userId"]})
        if user:
            student_name = user.get("fullName", student_name)

    results = []

    # 3. For each course, fetch assignments and quizzes
    for course in courses:
        cid = str(course["_id"])
        cname = course.get("title", "Unknown Course")

        # --- ASSIGNMENTS ---
        assignments = await assignments_collection.find({"courseId": cid, "tenantId": toid}).to_list(length=None)
        assignment_map = {str(a["_id"]): a for a in assignments}
        
        assignment_subs = await assignment_submissions_collection.find(
            {"courseId": cid, "studentId": student_id}
        ).to_list(length=None)
        
        assigned_subs_map = {str(s["assignmentId"]): s for s in assignment_subs}

        assignments_out = []
        for aid, a in assignment_map.items():
            sub = assigned_subs_map.get(aid)
            score_val = "Pending"
            if sub and sub.get("obtainedMarks") is not None:
                score_val = sub.get("obtainedMarks")
            elif sub:
                score_val = "Ungraded"
            
            assignments_out.append({
                "id": aid,
                "title": a.get("title", f"Assignment"),
                "score": score_val,
                "total": a.get("totalMarks", 100),
                "scoreDisplay": f"{score_val}/{a.get('totalMarks', 100)}" if isinstance(score_val, (int, float)) else score_val
            })

        # --- QUIZZES ---
        quizzes = await quizzes_collection.find({"courseId": cid, "tenantId": toid}).to_list(length=None)
        quiz_map = {str(q["_id"]): q for q in quizzes}

        quiz_subs = await quiz_submissions_collection.find(
            {"courseId": cid, "studentId": student_id}
        ).to_list(length=None)

        quiz_subs_map = {str(s["quizId"]): s for s in quiz_subs}

        quizzes_out = []
        for qid, q in quiz_map.items():
            sub = quiz_subs_map.get(qid)
            score_val = "Pending"
            if sub and sub.get("score") is not None:
                score_val = sub.get("score")
            elif sub:
                score_val = "Ungraded"

            title = q.get("title") or (f"Quiz {q.get('quizNumber')}" if q.get("quizNumber") else "Quiz")
            
            quizzes_out.append({
                "id": qid,
                "title": title,
                "score": score_val,
                "total": q.get("totalMarks", 100),
                "scoreDisplay": f"{score_val}/{q.get('totalMarks', 100)}" if isinstance(score_val, (int, float)) else score_val
            })

        # Only add the course if there are actual assignments or quizzes 
        # (or always add it if the student is supposedly enrolled, but let's always add it)
        results.append({
            "courseId": cid,
            "courseName": cname,
            "studentName": student_name,
            "assignments": assignments_out,
            "quizzes": quizzes_out
        })

    return results
