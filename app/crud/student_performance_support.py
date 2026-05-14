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
        "..",
        "uploads",
        "certificate",
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
    upload_dir = os.path.join(os.path.dirname(__file__), "..", "..", "uploads", "certificate")
    os.makedirs(upload_dir, exist_ok=True)
    file_path = os.path.join(upload_dir, file_id)
    issued_on = datetime.utcnow().strftime("%B %d, %Y")

    try:
        from fpdf import FPDF

        pdf = FPDF(orientation="landscape", format="A4")
        pdf.add_page()
        pdf.set_auto_page_break(False)

        # 1. Background and Border
        pdf.set_fill_color(252, 253, 255)  # Soft white
        pdf.rect(0, 0, 297, 210, style="F")

        # Outer Decorative Border (Teal)
        pdf.set_draw_color(35, 169, 151)
        pdf.set_line_width(1.5)
        pdf.rect(10, 10, 277, 190)

        # Inner Thin Border (Gold/Light Teal)
        pdf.set_draw_color(212, 175, 55) # Gold-ish
        pdf.set_line_width(0.5)
        pdf.rect(14, 14, 269, 182)

        # 2. Header
        pdf.ln(25)
        pdf.set_font("helvetica", "B", 32)
        pdf.set_text_color(24, 31, 57) # Deep Navy
        pdf.cell(0, 15, "EduVerse AI", align="C", new_x="LMARGIN", new_y="NEXT")
        
        pdf.set_font("helvetica", "B", 14)
        pdf.set_text_color(35, 169, 151) # Teal
        pdf.cell(0, 10, "CERTIFICATE OF COMPLETION", align="C", new_x="LMARGIN", new_y="NEXT")

        # 3. Content Body
        pdf.ln(15)
        pdf.set_font("helvetica", "", 16)
        pdf.set_text_color(100, 116, 139) # Gray
        pdf.cell(0, 10, "This is to certify that", align="C", new_x="LMARGIN", new_y="NEXT")

        pdf.ln(5)
        pdf.set_font("helvetica", "B", 42)
        pdf.set_text_color(24, 31, 57)
        pdf.cell(0, 20, student_name, align="C", new_x="LMARGIN", new_y="NEXT")

        pdf.ln(5)
        pdf.set_font("helvetica", "", 16)
        pdf.set_text_color(100, 116, 139)
        pdf.cell(0, 10, "has successfully completed the course", align="C", new_x="LMARGIN", new_y="NEXT")

        pdf.ln(5)
        pdf.set_font("helvetica", "B", 28)
        pdf.set_text_color(35, 169, 151)
        pdf.multi_cell(0, 15, f'"{course_name}"', align="C")

        # 4. Footer Section (Signatures/Date)
        pdf.set_y(155)
        
        # Issued By (Left)
        pdf.set_font("helvetica", "B", 12)
        pdf.set_text_color(24, 31, 57)
        pdf.set_xy(40, 170)
        pdf.cell(80, 7, "EduVerse Authority", align="C")
        pdf.line(40, 168, 120, 168) # Signature Line
        pdf.set_xy(40, 177)
        pdf.set_font("helvetica", "", 10)
        pdf.set_text_color(148, 163, 184)
        pdf.cell(80, 5, "Verified Digital Credential", align="C")

        # Date (Right)
        pdf.set_font("helvetica", "B", 12)
        pdf.set_text_color(24, 31, 57)
        pdf.set_xy(177, 170)
        pdf.cell(80, 7, issued_on, align="C")
        pdf.line(177, 168, 257, 168) # Signature Line
        pdf.set_xy(177, 177)
        pdf.set_font("helvetica", "", 10)
        pdf.set_text_color(148, 163, 184)
        pdf.cell(80, 5, "Completion Date", align="C")

        # 5. Credential ID (Bottom Center)
        pdf.set_y(192)
        pdf.set_font("helvetica", "I", 8)
        pdf.set_text_color(148, 163, 184)
        pdf.output(file_path)
    except ModuleNotFoundError:
        def escape(text: str) -> str:
            return text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")

        # Modern Coursera/Google Style Layout (842 units wide, 595 units tall)
        commands = [
            "1.0 1.0 1.0 rg", "0 0 842 595 re f", # White background
            
            # 1. The Sidebar (Left side) - Using Light Brand Teal
            "0.92 0.98 0.97 rg", # Very light mint/teal sidebar
            "0 0 180 595 re f",
            
            "0.14 0.66 0.59 rg", # Main Brand Teal Header in sidebar
            "0 400 180 80 re f",
            
            # 2. Main Content Area
            "0.14 0.66 0.59 RG", # Teal accent line
            "3 w",
            "200 40 m 800 40 l S", # Bottom accent line
        ]

        # Coordinates for the Modern Layout
        # Sidebar elements (x < 180)
        # Main content elements (x > 200)
        lines = [
            # Sidebar Text
            (40, 435, 16, "EduVerse"), # In the dark box
            
            # Main Content (Right Side)
            (220, 500, 34, "EduVerse AI"),
            (220, 460, 11, issued_on),
            (220, 420, 28, student_name), # Large Bold Name
            (220, 395, 14, "has successfully completed the online curriculum for"),
            (220, 350, 36, course_name), # Extra Large Course Title
            
            (220, 280, 11, "Those who earn the EduVerse AI Certificate have demonstrated"),
            (220, 265, 11, "advanced proficiency in the core concepts and practical"),
            (220, 250, 11, "applications of the curriculum, verified by our AI validation."),
            
            # Signature Area (Bottom Right)
            (600, 120, 14, "EduVerse Authority"),
            (600, 100, 10, "Global Director of Learning"),
            (600, 85, 9, f"ID: {file_id.replace('.pdf', '').upper()}"),
        ]

        content_lines = commands + ["BT"]
        for x, y, size, text in lines:
            # Special color for sidebar header
            if y == 435:
                content_lines.append("1.0 1.0 1.0 rg") # White text for sidebar header
            elif x < 180:
                content_lines.append("0.3 0.4 0.5 rg") # Dark grey for sidebar details
            else:
                content_lines.append("0.1 0.15 0.25 rg") # Deep Navy for main text
            
            content_lines.append(f"/F1 {size} Tf")
            content_lines.append(f"1 0 0 1 {x} {y} Tm")
            content_lines.append(f"({escape(text)}) Tj")
        content_lines.append("ET")
        
        # Add Signature Line
        content_lines.extend([
            "0.14 0.66 0.59 RG", # Brand Teal
            "1 w",
            "600 140 m 780 140 l S", # Signature line
        ])
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
