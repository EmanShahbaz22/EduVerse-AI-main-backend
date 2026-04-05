import os
from datetime import datetime


def coerce_datetime(value):
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        normalized = value.replace("Z", "+00:00")
        try:
            return datetime.fromisoformat(normalized)
        except ValueError:
            return None
    return None


def history_key(item: dict):
    course_id = item.get("courseId")
    if course_id:
        return ("course", str(course_id))

    reason = (item.get("reason") or "").strip()
    if reason.startswith("Course completion:"):
        return ("course-reason", reason)

    point_value = int(item.get("points", 0) or 0)
    date_value = coerce_datetime(item.get("date"))
    date_key = date_value.isoformat() if date_value else str(item.get("date") or "")
    return ("misc", reason, point_value, date_key)


def compute_points_this_week(points_history: list[dict]) -> int:
    now = datetime.utcnow()
    total = 0
    for item in points_history:
        earned_at = coerce_datetime(item.get("date"))
        if earned_at and (now - earned_at).days < 7:
            total += int(item.get("points", 0) or 0)
    return total


def get_certificate_path(file_id: str) -> str:
    return os.path.join(
        os.path.dirname(__file__),
        "..",
        "uploads",
        "certificates",
        file_id,
    )


def certificate_download_name(course_name: str) -> str:
    safe_name = "".join(
        char if char.isalnum() or char in (" ", "-", "_") else ""
        for char in (course_name or "Certificate").strip()
    ).strip()
    return f"{safe_name or 'Certificate'}.pdf"


async def generate_certificate_file(student_name: str, course_name: str) -> str:
    import uuid

    file_id = f"cert_{uuid.uuid4().hex}.pdf"
    upload_dir = os.path.join(os.path.dirname(__file__), "..", "uploads", "certificates")
    os.makedirs(upload_dir, exist_ok=True)
    file_path = os.path.join(upload_dir, file_id)
    issued_on = datetime.utcnow().strftime("%B %d, %Y")

    try:
        from fpdf import FPDF

        pdf = FPDF(orientation="landscape", format="A4")
        pdf.add_page()
        pdf.set_auto_page_break(False)

        pdf.set_fill_color(248, 250, 252)
        pdf.rect(0, 0, 297, 210, style="F")

        pdf.set_draw_color(35, 169, 151)
        pdf.set_line_width(2)
        pdf.rect(8, 8, 281, 194)

        pdf.set_draw_color(226, 232, 240)
        pdf.set_line_width(0.7)
        pdf.rect(14, 14, 269, 182)

        pdf.set_fill_color(35, 169, 151)
        pdf.rect(14, 14, 269, 5, style="F")

        pdf.set_font("helvetica", "B", 22)
        pdf.set_text_color(24, 31, 57)
        pdf.ln(22)
        pdf.cell(0, 12, "EduVerse", align="C", new_x="LMARGIN", new_y="NEXT")

        pdf.set_font("helvetica", "", 14)
        pdf.set_text_color(100, 116, 139)
        pdf.cell(0, 8, "Certificate of Achievement", align="C", new_x="LMARGIN", new_y="NEXT")

        pdf.ln(8)
        pdf.set_font("helvetica", "B", 34)
        pdf.set_text_color(24, 31, 57)
        pdf.cell(0, 18, "This certificate is presented to", align="C", new_x="LMARGIN", new_y="NEXT")

        pdf.ln(6)
        pdf.set_font("helvetica", "B", 30)
        pdf.set_text_color(35, 169, 151)
        pdf.cell(0, 16, student_name, align="C", new_x="LMARGIN", new_y="NEXT")

        pdf.ln(6)
        pdf.set_font("helvetica", "", 15)
        pdf.set_text_color(71, 85, 105)
        pdf.cell(
            0,
            9,
            "for successfully completing the learning experience",
            align="C",
            new_x="LMARGIN",
            new_y="NEXT",
        )

        pdf.ln(4)
        pdf.set_font("helvetica", "B", 24)
        pdf.set_text_color(24, 31, 57)
        pdf.multi_cell(0, 12, course_name, align="C")

        pdf.ln(12)
        pdf.set_font("helvetica", "", 13)
        pdf.set_text_color(100, 116, 139)
        pdf.cell(
            0,
            8,
            "Recognized by EduVerse for demonstrated course completion.",
            align="C",
            new_x="LMARGIN",
            new_y="NEXT",
        )

        pdf.set_y(154)
        pdf.set_draw_color(203, 213, 225)
        pdf.line(36, 174, 116, 174)
        pdf.line(181, 174, 261, 174)

        pdf.set_font("helvetica", "B", 12)
        pdf.set_text_color(24, 31, 57)
        pdf.set_xy(36, 176)
        pdf.cell(80, 6, "Issued by EduVerse", align="C")
        pdf.set_xy(181, 176)
        pdf.cell(80, 6, "Completion date", align="C")

        pdf.set_font("helvetica", "", 11)
        pdf.set_text_color(100, 116, 139)
        pdf.set_xy(36, 183)
        pdf.cell(80, 6, "Learning Platform", align="C")
        pdf.set_xy(181, 183)
        pdf.cell(80, 6, issued_on, align="C")

        pdf.set_xy(0, 192)
        pdf.set_font("helvetica", "", 9)
        pdf.set_text_color(148, 163, 184)
        pdf.cell(0, 6, f"Credential ID: {file_id.replace('.pdf', '').upper()}", align="C")

        pdf.output(file_path)
    except ModuleNotFoundError:
        def escape(text: str) -> str:
            return text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")

        commands = [
            "0.97 0.98 0.99 rg",
            "0 0 842 595 re f",
            "0.14 0.66 0.59 RG",
            "3 w",
            "24 24 794 547 re S",
            "0.89 0.91 0.94 RG",
            "1 w",
            "38 38 766 519 re S",
            "0.14 0.66 0.59 rg",
            "38 545 766 10 re f",
        ]

        lines = [
            (365, 520, 18, "EduVerse"),
            (300, 488, 13, "Certificate of Achievement"),
            (205, 438, 25, "This certificate is presented to"),
            (255, 396, 22, student_name),
            (170, 352, 13, "for successfully completing the learning experience"),
            (210, 315, 18, course_name),
            (155, 274, 11, "Recognized by EduVerse for demonstrated course completion."),
            (74, 124, 10, "Issued by EduVerse"),
            (504, 124, 10, "Completion date"),
            (84, 106, 9, "Learning Platform"),
            (520, 106, 9, issued_on),
            (250, 62, 8, f"Credential ID: {file_id.replace('.pdf', '').upper()}"),
        ]

        content_lines = commands + ["BT"]
        for x, y, size, text in lines:
            content_lines.append(f"/F1 {size} Tf")
            content_lines.append(f"1 0 0 1 {x} {y} Tm")
            content_lines.append(f"({escape(text)}) Tj")
        content_lines.append("ET")
        content_lines.extend(
            [
                "0.80 0.85 0.89 RG",
                "1 w",
                "74 118 m 234 118 l S",
                "504 118 m 664 118 l S",
            ]
        )
        stream = "\n".join(content_lines).encode("latin-1", "replace")

        objects = [
            b"1 0 obj << /Type /Catalog /Pages 2 0 R >> endobj\n",
            b"2 0 obj << /Type /Pages /Count 1 /Kids [3 0 R] >> endobj\n",
            b"3 0 obj << /Type /Page /Parent 2 0 R /MediaBox [0 0 842 595] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >> endobj\n",
            b"4 0 obj << /Type /Font /Subtype /Type1 /BaseFont /Helvetica >> endobj\n",
            f"5 0 obj << /Length {len(stream)} >> stream\n".encode("latin-1") + stream + b"\nendstream endobj\n",
        ]

        pdf = bytearray(b"%PDF-1.4\n")
        offsets = [0]
        for obj in objects:
            offsets.append(len(pdf))
            pdf.extend(obj)

        xref_start = len(pdf)
        pdf.extend(f"xref\n0 {len(offsets)}\n".encode("latin-1"))
        pdf.extend(b"0000000000 65535 f \n")
        for offset in offsets[1:]:
            pdf.extend(f"{offset:010d} 00000 n \n".encode("latin-1"))
        pdf.extend(
            f"trailer << /Size {len(offsets)} /Root 1 0 R >>\nstartxref\n{xref_start}\n%%EOF".encode(
                "latin-1"
            )
        )

        with open(file_path, "wb") as file_obj:
            file_obj.write(pdf)

    return file_id


def update_level_system(data: dict):
    xp, level = data.get("xp", 0), data.get("level", 1)
    xp_needed = lambda lv: int(round(300 * (1.5 ** (lv - 1)) / 50) * 50)
    req = xp_needed(level)
    while xp >= req:
        xp -= req
        level += 1
        req = xp_needed(level)
    data.update({"xp": xp, "level": level, "xpToNextLevel": req})
    return data


def build_tenant_course_stats(progress_docs: list[dict]) -> tuple[list[dict], list[str]]:
    course_stats: list[dict] = []
    completed_course_ids: list[str] = []

    for progress in progress_docs:
        course_id = str(progress.get("courseId") or "")
        if not course_id:
            continue

        course_stats.append(
            {
                "courseId": course_id,
                "completionPercentage": progress.get("progressPercentage", 0),
                "lastActive": progress.get("lastAccessedAt")
                or progress.get("lastActive")
                or datetime.utcnow().isoformat(),
            }
        )
        if progress.get("progressPercentage", 0) >= 100:
            completed_course_ids.append(course_id)

    return course_stats, completed_course_ids


def build_global_course_stats(progress_docs: list[dict]) -> tuple[dict[str, dict], set[str]]:
    course_stats_map: dict[str, dict] = {}
    completed_course_ids: set[str] = set()

    for progress in progress_docs:
        course_id = str(progress.get("courseId") or "")
        if not course_id:
            continue

        completion = int(progress.get("progressPercentage", 0) or 0)
        last_active = (
            progress.get("lastAccessedAt")
            or progress.get("lastActive")
            or datetime.utcnow().isoformat()
        )
        existing_stat = course_stats_map.get(course_id)
        if not existing_stat:
            course_stats_map[course_id] = {
                "courseId": course_id,
                "completionPercentage": completion,
                "lastActive": last_active,
            }
        else:
            existing_stat["completionPercentage"] = max(
                existing_stat.get("completionPercentage", 0), completion
            )
            existing_date = coerce_datetime(existing_stat.get("lastActive"))
            new_date = coerce_datetime(last_active)
            if new_date and (not existing_date or new_date > existing_date):
                existing_stat["lastActive"] = last_active

        if completion >= 100:
            completed_course_ids.add(course_id)

    return course_stats_map, completed_course_ids


def build_global_points_history(
    existing_history: list[dict],
    completed_course_ids: set[str],
    course_map: dict[str, dict],
) -> list[dict]:
    preserved_history: list[dict] = []
    course_history_by_id: dict[str, dict] = {}
    seen_misc_history = set()

    for item in existing_history:
        course_id = item.get("courseId")
        if course_id:
            course_history_by_id.setdefault(str(course_id), item)
            continue

        item_key = history_key(item)
        if item_key in seen_misc_history:
            continue
        seen_misc_history.add(item_key)
        preserved_history.append(item)

    generated_history: list[dict] = []
    for course_id in completed_course_ids:
        course = course_map.get(course_id)
        course_name = (course or {}).get("title", "Course")
        history_item = course_history_by_id.get(course_id) or {
            "points": 100,
            "reason": f"Course completion: {course_name}",
            "courseId": course_id,
            "date": datetime.utcnow(),
        }
        history_item["courseId"] = course_id
        history_item["reason"] = history_item.get("reason") or f"Course completion: {course_name}"
        history_item["points"] = int(history_item.get("points", 100) or 100)
        generated_history.append(history_item)

    points_history = preserved_history + generated_history
    points_history.sort(
        key=lambda item: coerce_datetime(item.get("date")) or datetime.min,
        reverse=True,
    )
    return points_history


def build_filtered_certificates(
    certificates: list[dict],
    completed_course_ids: set[str],
    course_map: dict[str, dict],
    template_version: str,
) -> list[dict]:
    valid_certificate_ids = {
        course_id
        for course_id in completed_course_ids
        if (course_map.get(course_id) or {}).get("hasCertificate")
    }
    filtered_certificates: list[dict] = []
    seen_certificate_courses = set()

    for cert in certificates:
        course_id = str(cert.get("courseId") or "")
        if (
            not course_id
            or course_id in seen_certificate_courses
            or course_id not in valid_certificate_ids
            or not cert.get("file")
        ):
            continue
        seen_certificate_courses.add(course_id)
        cert["title"] = (course_map.get(course_id) or {}).get(
            "title", cert.get("title") or "Certificate"
        )
        cert["generatedWith"] = cert.get("generatedWith") or template_version
        filtered_certificates.append(cert)

    return filtered_certificates


def collapse_leaderboard_docs(docs: list[dict]) -> list[dict]:
    collapsed = {}
    for doc in docs:
        student_id = str(doc.get("studentId")) if doc.get("studentId") else None
        student_name = (doc.get("user", {}) or {}).get("fullName") or doc.get("studentName")
        key = student_id or student_name or str(doc.get("_id"))
        candidate = {
            "studentId": student_id,
            "studentName": student_name,
            "points": doc.get("totalPoints", 0),
        }
        existing = collapsed.get(key)
        if not existing or candidate["points"] > existing["points"]:
            collapsed[key] = candidate

    return sorted(collapsed.values(), key=lambda item: -item["points"])
