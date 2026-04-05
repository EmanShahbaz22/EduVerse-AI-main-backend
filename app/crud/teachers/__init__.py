"""Teacher CRUD — split into core (CRUD) and related (quizzes, profile)."""

from app.crud.teachers.core import (
    to_oid,
    serialize_teacher,
    merge_user_data_teacher,
    create_teacher,
    create_or_link_teacher_for_tenant,
    get_all_teachers,
    get_teacher,
    update_teacher,
    delete_teacher,
    change_password,
)
from app.crud.teachers.related import (
    get_teacher_quizzes_route,
    create_teacher_quiz_route,
    get_teacher_dashboard,
    get_teacher_students,
    get_teacher_courses,
    get_teacher_by_user,
    update_teacher_profile,
    get_teacher_me,
    update_teacher_me,
    change_teacher_me_password,
)
