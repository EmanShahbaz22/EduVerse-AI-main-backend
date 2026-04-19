## Project Handoff (Backend & Frontend Pointers)

### Stack & Entry Points
- Backend: FastAPI (app/main.py), MongoDB (`MONGO_URI`), async Motor client. Key env: `MONGO_URI`, `GEMINI_API_KEY`, `JWT_SECRET`.
- Auth: `/auth/token` (OAuth2PasswordRequestForm), cookies enabled; student signup `/auth/student/signup`.
- Frontend (served separately, default dev URL `http://localhost:4200`); backend runs on `http://localhost:8000`.

### Adaptive Learning Flow (working)
1) Student marks lesson complete: `POST /courses/progress/mark-complete?tenantId=...` body `{"courseId","lessonId"}`.
2) Background pipeline (in `app/crud/student_progress.py`):
   - Generates AI quiz (`aiQuizSessions`) via `generate_ai_quiz` (Gemini).
   - Sleeps 15s, then pre-generates next lesson via `generate_lesson_for_student` (aiGeneratedLessons).
3) Student sees AI quiz: `GET /quizzes/student/me` now returns AI quiz sessions (router patched).
4) Submit quiz: `POST /quiz-submissions/` body `{"quizId","courseId","answers":[{questionIndex,selected},...]}`; grading supports `correctAnswer` from AI quizzes.
5) Next adaptive lesson fetched: `GET /adaptive/student/{student_id}/generated-lessons?course_id=...`.

### Recent Fixes (baseline)
- `app/crud/student_progress.py`: single-pass lesson lookup; string/ObjectId-safe lesson id check; Gemini throttle (15s) between quiz and lesson generation; dedup guard to avoid duplicate tasks; tenant_id passed into quiz generation.
- `app/services/chat_tutor.py`: memory `return_messages=False`; history rebuilt from DB rows.
- `app/routers/quizzes.py`: `/quizzes/student/me` surfaces AI quiz sessions first.
- `app/crud/quiz_submissions.py`: normalizes answers, grades `correctAnswer`, accepts AI quiz docs, derives tenant if missing.
- `app/schemas/quiz_submissions.py`: flexible answers payload.

### Known Pitfalls / Open Items
- Gemini rate limit: free tier 5 req/min. Sleep already added; still monitor for 429s.
- `student_performance.py` largely unaudited beyond current use.
- Lesson id mismatch can still produce "Unknown Lesson" if course modules mix str/ObjectId; now string-compared but verify data consistency.
- Uvicorn logs not persisted; use console to observe “Adaptive Pipeline…” lines.

### Quick Test Script (manual)
```powershell
# activate
.\venv\Scripts\Activate.ps1

# signup student
Invoke-RestMethod -Method Post http://localhost:8000/auth/student/signup `
  -ContentType application/json `
  -Body '{"fullName":"Flow Student","email":"flow_student@example.com","password":"Flow123!","role":"student"}'

# token
curl.exe -X POST "http://localhost:8000/auth/token" ^
  -H "Content-Type: application/x-www-form-urlencoded" ^
  -d "username=flow_student@example.com&password=Flow123!"

# mark lesson complete
curl.exe -X POST "http://localhost:8000/courses/progress/mark-complete?tenantId=TENANT_ID" ^
  -H "Authorization: Bearer TOKEN" ^
  -H "Content-Type: application/json" ^
  -d "{\"courseId\":\"COURSE_ID\",\"lessonId\":\"LESSON_ID\"}"

# list quizzes for student (AI sessions)
curl.exe "http://localhost:8000/quizzes/student/me" -H "Authorization: Bearer TOKEN"

# submit quiz (example answers)
$body = @{quizId='QUIZ_ID';courseId='COURSE_ID';answers=@(@{questionIndex=0;selected='A'},@{questionIndex=1;selected='B'})} | ConvertTo-Json -Depth 4
Invoke-RestMethod -Method Post "http://localhost:8000/quiz-submissions/" -Headers @{Authorization="Bearer TOKEN"} -ContentType "application/json" -Body $body

# adaptive lesson
curl.exe "http://localhost:8000/adaptive/student/STUDENT_ID/generated-lessons?course_id=COURSE_ID" -H "Authorization: Bearer TOKEN"
```

### Data Pointers
- Courses collection examples: English Literature `69ccbc472bc999ca4020ac91`; Test Course `69701b6dd38b2c56540cc852`.
- aiQuizSessions store `studentId` (string), `courseId`, `topic`, `questions[].correctAnswer`.
- aiGeneratedLessons keyed by `studentId` + optional `quizId`.

### Frontend Notes
- Expect frontend to call the same endpoints above; CORS already configured in FastAPI main (check `app/main.py` if adjusting origins).
- If quizzes were empty on UI before, ensure frontend uses `/quizzes/student/me` (AI sessions) instead of legacy `/quizzes`.
