# API Migration Notes

## Effective Date
These changes are now active in the backend codebase.

## Breaking/Behavioral Changes
1. `POST /quiz-submissions`
   - Removed client-controlled `studentId` and `tenantId`.
   - Server now binds student and tenant from authenticated user context.

2. `GET /quiz-submissions/student/{student_id}`
   - Students can only access their own submissions.
   - Admin/teacher access is tenant-constrained.
   - Super admin remains global.

3. `DELETE /quiz-submissions/{id}`
   - Students can only delete their own submissions.
   - Admin/teacher deletion is tenant-constrained.

4. `POST /courses/enroll` and `POST /courses/unenroll`
   - Student role: student identity is derived server-side.
   - Student marketplace behavior: cross-tenant course enrollment is allowed.
   - Admin/teacher role: student must belong to caller tenant and course checks remain tenant-bound.

5. Role canonicalization
   - Canonical role value is `super_admin`.
   - Legacy `super-admin` is normalized server-side.

6. `POST /payments/create-payment-intent` and payment confirmation paths
   - Student profile identity/tenant are now derived server-side.
   - Client `tenantId` is optional and ignored for identity enforcement.
   - Enrollment after successful payment follows marketplace behavior (`enforce_same_tenant=False`).

## Frontend Contract Updates Required
1. Quiz submission payload must use:
   - `quizId`
   - `courseId`
   - `answers`

2. Enrollment payload may still include `studentId` and `tenantId`, but these are ignored for student-role requests.

## Verification Checklist
1. Student cannot submit/read/delete other student quiz submissions.
2. Student can enroll into marketplace courses across tenants.
3. Admin cannot mutate students/teachers/courses in another tenant.
4. Super admin routes accept canonical role and legacy alias.
