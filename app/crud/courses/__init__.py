"""Course CRUD operations — split into submodules for maintainability."""

from app.crud.courses.queries import (
    get_course_by_id,
    get_course_by_id_any_tenant,
    get_all_courses,
    get_marketplace_courses,
    get_student_courses,
    get_student_courses_any_tenant,
    get_enrolled_students,
)
from app.crud.courses.mutations import (
    create_course,
    update_course,
    delete_course,
    publish_course,
)
from app.crud.courses.enrollment import (
    enroll_student,
    unenroll_student,
    reorder_lessons,
    reorder_modules,
)


class CourseCRUD:
    """Thin wrapper preserving the class-based API used by routers."""

    get_course_by_id = staticmethod(get_course_by_id)
    get_course_by_id_any_tenant = staticmethod(get_course_by_id_any_tenant)
    get_all_courses = staticmethod(get_all_courses)
    get_marketplace_courses = staticmethod(get_marketplace_courses)
    get_student_courses = staticmethod(get_student_courses)
    get_student_courses_any_tenant = staticmethod(get_student_courses_any_tenant)
    get_enrolled_students = staticmethod(get_enrolled_students)
    create_course = staticmethod(create_course)
    update_course = staticmethod(update_course)
    delete_course = staticmethod(delete_course)
    publish_course = staticmethod(publish_course)
    enroll_student = staticmethod(enroll_student)
    unenroll_student = staticmethod(unenroll_student)
    reorder_lessons = staticmethod(reorder_lessons)
    reorder_modules = staticmethod(reorder_modules)


course_crud = CourseCRUD()
