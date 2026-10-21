import asyncio
import hashlib
import hmac
import re
import secrets
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import flet as ft
from PIL import Image, ImageDraw

from database import get_connection
from generator import TIME_SLOTS, TimetableGenerator


ROOT = Path(__file__).resolve().parents[1]
DB_PATH = ROOT / "database.db"
PBKDF2_ITERATIONS = 310_000
INK = "#F1F5FB"
MUTED = "#8A97AA"
ACCENT = "#7669F5"
SURFACE = "#151F2D"
PAGE_BG = "#0C1320"
OUTLINE = "#253244"
SECONDARY = "#1D2939"
CHART_COLORS = ("#4688F5", "#24B89A", "#F3B843", "#7867F5", "#E577A8")
SCHOOL_NAME = "TUMAINI SENIOR SECONDARY SCHOOL"


@dataclass(frozen=True)
class Field:
    name: str
    label: str
    kind: str = "text"
    required: bool = False
    options: tuple[str, ...] = ()


@dataclass(frozen=True)
class Resource:
    key: str
    title: str
    table: str
    fields: tuple[Field, ...]
    list_fields: tuple[str, ...]
    order_by: str


RESOURCES = {
    "classes": Resource(
        "classes",
        "Classes",
        "classes",
        (
            Field("class_name", "Class name", required=True),
            Field("class_code", "Class code"),
            Field("grade", "Grade / form"),
            Field("academic_year", "Academic year"),
            Field("homeroom_teacher", "Homeroom teacher"),
            Field("curriculum", "Curriculum", "choice", options=("New TIE", "Old TIE", "Vocational")),
            Field("class_type", "Class type", "choice", options=("O-Level", "A-Level", "Vocational")),
            Field("combination", "Subject combination"),
        ),
        ("class_name", "grade", "class_type", "academic_year"),
        "class_name COLLATE NOCASE",
    ),
    "students": Resource(
        "students",
        "Students",
        "students",
        (
            Field("student_name", "Student name", required=True),
            Field("gender", "Gender", "choice", options=("Female", "Male", "Other")),
            Field("student_class", "Class"),
            Field("phone", "Phone"),
            Field("guardian", "Guardian"),
        ),
        ("student_name", "student_class", "phone", "guardian"),
        "student_name COLLATE NOCASE",
    ),
    "teachers": Resource(
        "teachers",
        "Teachers",
        "teachers",
        (
            Field("teacher_name", "Teacher name", required=True),
            Field("teacher_code", "Teacher code"),
            Field("gender", "Gender", "choice", options=("Female", "Male", "Other")),
            Field("phone", "Phone"),
            Field("email", "Email"),
            Field("subject", "Subjects taught"),
            Field("department", "Department"),
            Field("qualification", "Qualification"),
            Field("specialization", "Specialization"),
            Field("classes_taught", "Classes taught"),
            Field("status", "Status", "choice", options=("Active", "Inactive")),
            Field("hire_date", "Hire date"),
            Field("notes", "Notes", "multiline"),
        ),
        ("teacher_name", "teacher_code", "subject", "status"),
        "teacher_name COLLATE NOCASE",
    ),
    "subjects": Resource(
        "subjects",
        "Subjects",
        "subjects",
        (
            Field("subject_name", "Subject name", required=True),
            Field("subject_code", "Subject code"),
            Field("description", "Description", "multiline"),
            Field("category", "Category", "choice", options=("O-Level", "A-Level", "Both")),
            Field("track", "Track", "choice", options=("Normal", "Vocational")),
            Field("delivery_location", "Delivery location", "choice", options=("Classroom", "Laboratory", "Either")),
            Field("is_taught", "Taught", "bool"),
            Field("is_extra", "Extra subject", "bool"),
        ),
        ("subject_name", "subject_code", "category", "track"),
        "subject_name COLLATE NOCASE",
    ),
    "classrooms": Resource(
        "classrooms",
        "Classrooms",
        "classrooms",
        (
            Field("room_name", "Room name", required=True),
            Field("room_code", "Room code"),
            Field("capacity", "Capacity", "int"),
            Field("floor", "Floor", "int"),
            Field("status", "Status", "choice", options=("Available", "Unavailable")),
        ),
        ("room_name", "room_code", "capacity", "status"),
        "room_name COLLATE NOCASE",
    ),
    "syllabi": Resource(
        "syllabi",
        "Syllabi",
        "syllabi",
        (
            Field("curriculum", "Curriculum", "choice", options=("New TIE", "Old TIE", "Vocational"), required=True),
            Field("form_level", "Form / level", required=True),
            Field("subject_name", "Subject", required=True),
            Field("syllabus_text", "Syllabus content", "multiline", required=True),
            Field("source", "Source"),
        ),
        ("subject_name", "form_level", "curriculum", "source"),
        "subject_name COLLATE NOCASE",
    ),
}


def _connection():
    conn = get_connection()
    conn.row_factory = sqlite3.Row
    return conn


def fetch_all(query, params=()):
    conn = _connection()
    try:
        return [dict(row) for row in conn.execute(query, params).fetchall()]
    finally:
        conn.close()


def execute(query, params=()):
    conn = _connection()
    try:
        cursor = conn.execute(query, params)
        conn.commit()
        return cursor.lastrowid
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _password_digest(password, salt=None):
    salt = salt or secrets.token_hex(16)
    derived = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), bytes.fromhex(salt), PBKDF2_ITERATIONS
    ).hex()
    return f"pbkdf2_sha256${PBKDF2_ITERATIONS}${salt}${derived}"


def _verify_password(password, stored):
    if stored.startswith("pbkdf2_sha256$"):
        try:
            _, rounds, salt, expected = stored.split("$", 3)
            actual = hashlib.pbkdf2_hmac(
                "sha256", password.encode("utf-8"), bytes.fromhex(salt), int(rounds)
            ).hex()
        except (ValueError, TypeError):
            return False
        return hmac.compare_digest(actual, expected)
    return hmac.compare_digest(hashlib.sha256(password.encode("utf-8")).hexdigest(), stored)


def _label(value):
    return str(value).replace("_", " ").title()


def _singular(value):
    return {
        "Classes": "Class",
        "Students": "Student",
        "Teachers": "Teacher",
        "Subjects": "Subject",
        "Classrooms": "Classroom",
        "Syllabi": "Syllabus",
    }.get(value, value)


def _icon(name, color=INK, size=20):
    return ft.Icon(getattr(ft.Icons, name), color=color, size=size)


def _chart_asset(name, draw_chart):
    asset_dir = ROOT / "assets" / "dashboard_charts"
    asset_dir.mkdir(parents=True, exist_ok=True)
    asset_path = asset_dir / name
    draw_chart(asset_path)
    return f"dashboard_charts/{name}"


def _write_line_chart(path, labels, values, color):
    width, height = 840, 340
    image = Image.new("RGB", (width, height), SECONDARY)
    draw = ImageDraw.Draw(image)
    left, right, top, bottom = 62, width - 22, 24, height - 47
    max_value = max(values, default=0)
    axis_max = max(1, ((max_value + 3) // 4) * 4)

    for tick in range(5):
        y = bottom - (bottom - top) * tick // 4
        draw.line((left, y, right, y), fill="#304052", width=1)
        draw.text((8, y - 7), str(axis_max * tick // 4), fill="#8A97AA")

    if values:
        span = max(1, right - left)
        step = span / max(1, len(values) - 1)
        points = [
            (round(left + index * step), round(bottom - value / axis_max * (bottom - top)))
            for index, value in enumerate(values)
        ]
        draw.line(points, fill=color, width=4, joint="curve")
        for x, y in points:
            draw.ellipse((x - 6, y - 6, x + 6, y + 6), fill=SECONDARY, outline=color, width=3)
        for index, label in enumerate(labels):
            x = round(left + index * step)
            draw.text((x, bottom + 14), label[:10], fill="#A6B1C0", anchor="mt")
    image.save(path, format="PNG")


def _write_pie_chart(path, values):
    width, height = 600, 340
    image = Image.new("RGB", (width, height), SECONDARY)
    draw = ImageDraw.Draw(image)
    diameter = 274
    left = (width - diameter) // 2
    top = (height - diameter) // 2
    bounds = (left, top, left + diameter, top + diameter)
    total = sum(values)
    if not total:
        draw.ellipse(bounds, outline="#354358", width=2)
    else:
        start = -90
        for index, value in enumerate(values):
            end = start + value / total * 360
            draw.pieslice(
                bounds,
                start=start,
                end=end,
                fill=CHART_COLORS[index % len(CHART_COLORS)],
                outline=SECONDARY,
                width=4,
            )
            start = end
    image.save(path, format="PNG")


def main(page: ft.Page):
    page.title = f"{SCHOOL_NAME} | TTMS"
    page.theme_mode = ft.ThemeMode.DARK
    page.theme = ft.Theme(color_scheme_seed=ACCENT)
    page.bgcolor = PAGE_BG
    page.padding = 0
    page.window.width = 1280
    page.window.height = 820
    page.window.min_width = 960
    page.window.min_height = 640
    icon_path = ROOT / "assets" / "ttms_icon.ico"
    if icon_path.exists():
        page.window.icon = str(icon_path)

    session_user = None
    active_section = "overview"

    def notify(message, error=False):
        page.show_dialog(
            ft.SnackBar(
                content=ft.Text(message, color=ft.Colors.WHITE),
                bgcolor="#B42318" if error else "#176B55",
            )
        )

    def card(content, padding=20, expand=False):
        return ft.Container(
            content=content,
            bgcolor=SURFACE,
            border_radius=16,
            padding=padding,
            expand=expand,
            border=ft.Border.all(1, OUTLINE),
        )

    def section_title(title, subtitle):
        return ft.Column(
            [
                ft.Text(title, size=26, weight=ft.FontWeight.W_700, color=INK),
                ft.Text(subtitle, size=13, color=MUTED),
            ],
            spacing=4,
        )

    def load_overview():
        stats = [
            ("Total Students", "students", "PEOPLE_ROUNDED", "#4688F5", "students enrolled"),
            ("Total Teachers", "teachers", "PERSON_OUTLINE_ROUNDED", "#A66AF3", "teachers registered"),
            ("Total Classes", "classes", "CLASS_ROUNDED", "#F3B843", "classes registered"),
            ("Classrooms", "classrooms", "MEETING_ROOM_ROUNDED", "#24B89A", "learning spaces"),
        ]
        stat_cards = []
        for label, table, icon_name, color, detail in stats:
            count = fetch_all(f"SELECT COUNT(*) AS total FROM {table}")[0]["total"]
            stat_cards.append(
                ft.Container(
                    content=ft.Row(
                        [
                            ft.Column(
                                [
                                    ft.Text(label, size=12, color=MUTED),
                                    ft.Text(str(count), size=27, weight=ft.FontWeight.W_700, color=color),
                                    ft.Row(
                                        [
                                            ft.Container(width=6, height=6, border_radius=3, bgcolor=color),
                                            ft.Text(detail, size=10, color=MUTED),
                                        ],
                                        spacing=4,
                                    ),
                                ],
                                spacing=3,
                                expand=True,
                            ),
                            ft.Container(
                                content=_icon(icon_name, color, 23),
                                bgcolor=SECONDARY,
                                border_radius=24,
                                padding=11,
                            ),
                        ],
                        alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                        vertical_alignment=ft.CrossAxisAlignment.CENTER,
                    ),
                    bgcolor=SURFACE,
                    border=ft.Border.all(1, OUTLINE),
                    border_radius=12,
                    padding=16,
                    expand=True,
                )
            )
        enrollment = fetch_all(
            """
            SELECT COALESCE(NULLIF(TRIM(student_class), ''), 'Unassigned') AS label,
                   COUNT(*) AS total
            FROM students
            GROUP BY COALESCE(NULLIF(TRIM(student_class), ''), 'Unassigned')
            ORDER BY total DESC, label COLLATE NOCASE
            LIMIT 6
            """
        )
        class_types = fetch_all(
            """
            SELECT COALESCE(NULLIF(TRIM(class_type), ''), 'Other') AS label,
                   COUNT(*) AS total
            FROM classes
            GROUP BY COALESCE(NULLIF(TRIM(class_type), ''), 'Other')
            ORDER BY total DESC, label COLLATE NOCASE
            """
        )
        days = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday")
        weekly_counts = {
            row["day"]: row["total"]
            for row in fetch_all(
                "SELECT day, COUNT(*) AS total FROM timetables GROUP BY day"
            )
        }
        enrollment_has_data = bool(enrollment)
        class_distribution_has_data = bool(class_types)
        schedule_has_data = sum(weekly_counts.values()) > 0
        enrollment_image = _chart_asset(
            "student_enrollment.png",
            lambda path: _write_line_chart(
                path,
                [row["label"] for row in enrollment],
                [row["total"] for row in enrollment],
                CHART_COLORS[0],
            ),
        )
        distribution_image = _chart_asset(
            "class_distribution.png",
            lambda path: _write_pie_chart(path, [row["total"] for row in class_types]),
        )
        schedule_image = _chart_asset(
            "weekly_schedule.png",
            lambda path: _write_line_chart(
                path,
                [day[:3] for day in days],
                [weekly_counts.get(day, 0) for day in days],
                CHART_COLORS[1],
            ),
        )

        def chart_panel(title, subtitle, image, has_data, empty_message, badge, footer=None):
            return ft.Container(
                content=ft.Column(
                    [
                        ft.Row(
                            [
                                ft.Column(
                                    [
                                        ft.Text(title, size=15, weight=ft.FontWeight.W_600, color=INK),
                                        ft.Text(subtitle, size=11, color=MUTED),
                                    ],
                                    spacing=3,
                                    expand=True,
                                ),
                                ft.Container(
                                    content=ft.Text(badge, size=10, color="#B7B0FF"),
                                    bgcolor="#282544",
                                    padding=ft.Padding.symmetric(horizontal=10, vertical=5),
                                    border_radius=20,
                                ),
                            ],
                            vertical_alignment=ft.CrossAxisAlignment.START,
                        ),
                        ft.Stack(
                            [
                                ft.Image(
                                    src=image,
                                    fit=ft.BoxFit.CONTAIN,
                                    width=300,
                                    height=168,
                                ),
                                ft.Container(
                                    content=ft.Text(empty_message, size=11, color=MUTED),
                                    alignment=ft.Alignment.CENTER,
                                    visible=not has_data,
                                    width=300,
                                    height=168,
                                ),
                            ],
                            width=300,
                            height=168,
                        ),
                        footer if footer is not None else ft.Container(height=0),
                    ],
                    spacing=12,
                ),
                bgcolor=SURFACE,
                border=ft.Border.all(1, OUTLINE),
                border_radius=13,
                padding=16,
                col={"sm": 12, "lg": 4},
            )

        legend = ft.Row(
            [
                ft.Row(
                    [
                        ft.Container(
                            width=8,
                            height=8,
                            border_radius=4,
                            bgcolor=CHART_COLORS[index % len(CHART_COLORS)],
                        ),
                        ft.Text(
                            f"{row['label']}  {row['total']}",
                            size=10,
                            color="#A6B1C0",
                        ),
                    ],
                    spacing=5,
                )
                for index, row in enumerate(class_types)
            ],
            wrap=True,
            spacing=10,
            run_spacing=4,
        )
        return ft.Column(
            [
                section_title(
                    "Dashboard",
                    "Real-time insights into your school at a glance.",
                ),
                ft.ResponsiveRow(
                    [
                        ft.Container(content=item, col={"sm": 6, "lg": 3})
                        for item in stat_cards
                    ],
                    spacing=12,
                    run_spacing=12,
                ),
                ft.ResponsiveRow(
                    [
                        chart_panel(
                            "Student Enrollment",
                            "Students by class",
                            enrollment_image,
                            enrollment_has_data,
                            "No student enrollment data yet",
                            "BY CLASS",
                        ),
                        chart_panel(
                            "Class Distribution",
                            "Classes by school level",
                            distribution_image,
                            class_distribution_has_data,
                            "No class distribution data yet",
                            "BY LEVEL",
                            footer=legend,
                        ),
                        chart_panel(
                            "Weekly Timetable",
                            "Scheduled lessons by weekday",
                            schedule_image,
                            schedule_has_data,
                            "No timetable entries yet",
                            "THIS WEEK",
                        ),
                    ],
                    spacing=12,
                    run_spacing=12,
                ),
            ],
            spacing=14,
            scroll=ft.ScrollMode.AUTO,
            expand=True,
        )

    def build_schedule_table(weekday="All weekdays"):
        rows = fetch_all(
            """
            SELECT c.id AS class_id, c.class_name, c.grade, s.subject_name,
                   t.day, t.start_time, t.end_time
            FROM timetables t
            LEFT JOIN classes c ON c.id = t.class_id
            LEFT JOIN subjects s ON s.id = t.subject_id
            WHERE t.day IN ('Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday')
            ORDER BY t.start_time
            """
        )
        classes = {
            row["id"]: {"class_name": row["class_name"], "grade": row["grade"]}
            for row in fetch_all("SELECT id, class_name, grade FROM classes")
        }

        def class_sort_key(item):
            class_id, record = item
            match = re.search(r"\d+", record["grade"] or record["class_name"] or "")
            grade = int(match.group()) if match else 99
            suffix = re.sub(
                r"[^A-Z0-9]",
                "",
                re.sub(r"^FORM\s*\d+", "", record["class_name"] or "", flags=re.IGNORECASE).upper(),
            )
            return grade, suffix, record["class_name"].casefold(), class_id

        ordered_classes = sorted(classes.items(), key=class_sort_key)
        by_day_and_slot = {
            (row["day"], row["start_time"], row["class_id"]): row
            for row in rows
        }
        weekdays = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday")
        days = weekdays if weekday == "All weekdays" else (weekday,)
        time_slots = fetch_all(
            """
            SELECT start_time, end_time
            FROM timetables
            WHERE day IN ('Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday')
            GROUP BY start_time, end_time
            ORDER BY start_time
            """
        )
        if not time_slots:
            time_slots = [
                {"start_time": start, "end_time": end}
                for start, end in TIME_SLOTS
            ]

        time_width = 112
        column_width = 150
        grid_width = time_width + len(ordered_classes) * column_width

        def cell(content, width, bgcolor=None, color=INK, bold=False):
            text = content if isinstance(content, ft.Control) else ft.Text(
                content,
                size=12,
                color=color,
                weight=ft.FontWeight.W_600 if bold else ft.FontWeight.W_400,
                max_lines=2,
                overflow=ft.TextOverflow.ELLIPSIS,
            )
            return ft.Container(
                content=text,
                width=width,
                height=42,
                padding=ft.Padding.symmetric(horizontal=11, vertical=7),
                alignment=ft.Alignment.CENTER_LEFT,
                bgcolor=bgcolor,
            )

        grid_rows = [
            ft.Row(
                [
                    cell("Time", time_width, bgcolor=SECONDARY, bold=True),
                    *[
                        cell(record["class_name"], column_width, bgcolor=SECONDARY, bold=True)
                        for _class_id, record in ordered_classes
                    ],
                ],
                spacing=0,
            )
        ]

        for day in days:
            grid_rows.append(
                ft.Container(
                    content=ft.Text(day.upper(), size=11, weight=ft.FontWeight.W_700, color="#B7B0FF"),
                    bgcolor="#282544",
                    border_radius=6,
                    padding=ft.Padding.symmetric(horizontal=12, vertical=9),
                    width=grid_width,
                )
            )
            grid_rows.append(
                ft.Row(
                    [
                        cell("07:30–08:00", time_width, color=MUTED),
                        *[cell("Assembly", column_width) for _ in ordered_classes],
                    ],
                    spacing=0,
                )
            )
            for index, slot in enumerate(time_slots):
                grid_rows.append(
                    ft.Row(
                        [
                            cell(f"{slot['start_time']}–{slot['end_time']}", time_width, color=MUTED),
                            *[
                                cell(
                                    by_day_and_slot[(day, slot["start_time"], class_id)]["subject_name"] or "—",
                                    column_width,
                                    bgcolor="#192432",
                                )
                                if (day, slot["start_time"], class_id) in by_day_and_slot
                                else cell("—", column_width, color=MUTED)
                                for class_id, _record in ordered_classes
                            ],
                        ],
                        spacing=0,
                    )
                )
                if index in (2, 5) and index + 1 < len(time_slots):
                    is_break = index == 2
                    start, end = slot["end_time"], time_slots[index + 1]["start_time"]
                    grid_rows.append(
                        ft.Row(
                            [
                                cell(f"{start}–{end}", time_width, color=MUTED),
                                ft.Container(
                                    content=ft.Text(
                                        "BREAK — ALL CLASSES" if is_break else "LUNCH BREAK — ALL CLASSES",
                                        size=12,
                                        weight=ft.FontWeight.W_700,
                                        color="#10251D" if is_break else "#392B0A",
                                        text_align=ft.TextAlign.CENTER,
                                    ),
                                    width=column_width * max(1, len(ordered_classes)),
                                    height=38,
                                    padding=9,
                                    bgcolor="#B9F4DA" if is_break else "#FDEDB7",
                                    border_radius=8,
                                    alignment=ft.Alignment.CENTER,
                                ),
                            ],
                            spacing=0,
                        )
                    )

        if not ordered_classes:
            return ft.Container(
                content=ft.Text("Add classes to display a master timetable.", color=MUTED),
                padding=16,
            )

        return ft.Column(
            [
                ft.Container(
                    content=ft.Column(grid_rows, spacing=1, tight=True),
                    width=grid_width,
                )
            ],
            scroll=ft.ScrollMode.AUTO,
            expand=True,
        )

    def build_resource_table(resource, search_value=""):
        data = fetch_all(
            f"SELECT id, {', '.join(field.name for field in resource.fields)} "
            f"FROM {resource.table} ORDER BY {resource.order_by}"
        )
        query = search_value.strip().casefold()
        if query:
            data = [
                item
                for item in data
                if any(query in str(item.get(name) or "").casefold() for name in resource.list_fields)
            ]
        if not data:
            return ft.Container(
                content=ft.Text("No records found.", color=MUTED),
                padding=18,
                alignment=ft.Alignment.CENTER_LEFT,
            )
        columns = [ft.DataColumn(ft.Text("ID"))]
        columns.extend(ft.DataColumn(ft.Text(_label(name))) for name in resource.list_fields)
        if session_user["role"] == "Administrator":
            columns.append(ft.DataColumn(ft.Text("Actions"), numeric=True))
        table_rows = []
        for item in data:
            cells = [ft.DataCell(ft.Text(str(item["id"]), size=12))]
            cells.extend(
                ft.DataCell(ft.Text(str(item.get(name) or "—"), size=12, max_lines=1))
                for name in resource.list_fields
            )
            if session_user["role"] == "Administrator":
                cells.append(
                    ft.DataCell(
                        ft.Row(
                            [
                                ft.IconButton(
                                    icon=ft.Icons.EDIT_OUTLINED,
                                    tooltip="Edit",
                                    icon_color=ACCENT,
                                    on_click=lambda _, r=resource, row=item: open_resource_dialog(r, row),
                                ),
                                ft.IconButton(
                                    icon=ft.Icons.DELETE_OUTLINE_ROUNDED,
                                    tooltip="Delete",
                                    icon_color="#F97066",
                                    on_click=lambda _, r=resource, row_id=item["id"]: confirm_delete(r, row_id),
                                ),
                            ],
                            spacing=0,
                        )
                    )
                )
            table_rows.append(ft.DataRow(cells=cells))
        return ft.Row(
            [
                ft.DataTable(
                    columns=columns,
                    rows=table_rows,
                    heading_row_color=SECONDARY,
                    column_spacing=18,
                    data_row_min_height=48,
                )
            ],
            scroll=ft.ScrollMode.AUTO,
        )

    def open_resource_dialog(resource, record=None):
        controls = []
        inputs = {}
        for field in resource.fields:
            value = record.get(field.name) if record else None
            if field.kind == "bool":
                control = ft.Checkbox(
                    label=field.label,
                    value=bool(value) if record else field.name == "is_taught",
                )
            elif field.kind == "choice":
                options = [ft.DropdownOption(key=item, text=item) for item in field.options]
                current = str(value) if value is not None else None
                if current and current not in field.options:
                    options.insert(0, ft.DropdownOption(key=current, text=current))
                control = ft.Dropdown(
                    label=field.label,
                    options=options,
                    value=current if current else (field.options[0] if field.options else None),
                    expand=True,
                )
            else:
                control = ft.TextField(
                    label=field.label + (" *" if field.required else ""),
                    value="" if value is None else str(value),
                    multiline=field.kind == "multiline",
                    min_lines=3 if field.kind == "multiline" else 1,
                    max_lines=7 if field.kind == "multiline" else 1,
                    keyboard_type=ft.KeyboardType.NUMBER if field.kind == "int" else ft.KeyboardType.TEXT,
                    expand=True,
                )
            inputs[field.name] = control
            controls.append(control)

        def save_record(_):
            values = {}
            try:
                for field in resource.fields:
                    control = inputs[field.name]
                    value = control.value
                    if field.required and not str(value or "").strip():
                        notify(f"{field.label} is required.", error=True)
                        return
                    if field.kind == "bool":
                        values[field.name] = 1 if value else 0
                    elif field.kind == "int":
                        values[field.name] = int(value) if str(value or "").strip() else None
                    else:
                        values[field.name] = str(value or "").strip() or None
                names = list(values)
                if record:
                    assignments = ", ".join(f"{name} = ?" for name in names)
                    execute(
                        f"UPDATE {resource.table} SET {assignments} WHERE id = ?",
                        (*[values[name] for name in names], record["id"]),
                    )
                else:
                    execute(
                        f"INSERT INTO {resource.table} ({', '.join(names)}) "
                        f"VALUES ({', '.join('?' for _ in names)})",
                        tuple(values[name] for name in names),
                    )
            except ValueError:
                notify("Enter a valid whole number for numeric fields.", error=True)
                return
            except sqlite3.IntegrityError as exc:
                notify(f"Could not save this record: {exc}", error=True)
                return
            page.pop_dialog()
            notify(f"{resource.title[:-1] if resource.title.endswith('s') else resource.title} saved.")
            navigate(active_section)

        dialog = ft.AlertDialog(
            modal=True,
            title=ft.Text(f"{'Edit' if record else 'Add'} {_singular(resource.title)}"),
            content=ft.Container(
                content=ft.Column(controls, spacing=10, scroll=ft.ScrollMode.AUTO),
                width=520,
                height=560,
            ),
            actions=[
                ft.TextButton("Cancel", on_click=lambda _: page.pop_dialog()),
                ft.FilledButton("Save", icon=ft.Icons.CHECK_ROUNDED, on_click=save_record),
            ],
            actions_alignment=ft.MainAxisAlignment.END,
        )
        page.show_dialog(dialog)

    def confirm_delete(resource, record_id):
        def delete_record(_):
            try:
                execute(f"DELETE FROM {resource.table} WHERE id = ?", (record_id,))
            except sqlite3.IntegrityError as exc:
                page.pop_dialog()
                notify(f"Could not delete this record: {exc}", error=True)
                return
            page.pop_dialog()
            notify("Record deleted.")
            navigate(active_section)

        dialog = ft.AlertDialog(
            modal=True,
            title=ft.Text("Delete record?"),
            content=ft.Text("This action cannot be undone."),
            actions=[
                ft.TextButton("Cancel", on_click=lambda _: page.pop_dialog()),
                ft.FilledButton(
                    "Delete",
                    icon=ft.Icons.DELETE_OUTLINE_ROUNDED,
                    style=ft.ButtonStyle(bgcolor="#7F2929", color=ft.Colors.WHITE),
                    on_click=delete_record,
                ),
            ],
        )
        page.show_dialog(dialog)

    def resource_view(resource):
        search = ft.TextField(
            label=f"Search {resource.title.lower()}",
            prefix_icon=ft.Icons.SEARCH_ROUNDED,
            value=global_search_query,
            on_submit=lambda e: refresh_table(e.control.value),
            expand=True,
        )
        table_slot = ft.Container(expand=True)

        def refresh_table(value=None):
            nonlocal global_search_query
            if value is None:
                value = search.value
            global_search_query = value
            table_slot.content = card(build_resource_table(resource, value), padding=8)
            page.update()

        search.on_change = lambda e: refresh_table(e.control.value)
        buttons = []
        if session_user["role"] == "Administrator":
            buttons.append(
                ft.FilledButton(
                    f"Add {_singular(resource.title)}",
                    icon=ft.Icons.ADD_ROUNDED,
                    on_click=lambda _: open_resource_dialog(resource),
                )
            )
        return ft.Column(
            [
                ft.Row(
                    [
                        section_title(resource.title, f"Browse and manage {resource.title.lower()}."),
                        *buttons,
                    ],
                    alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                    vertical_alignment=ft.CrossAxisAlignment.CENTER,
                    wrap=True,
                ),
                ft.Row([search], spacing=12),
                table_slot,
            ],
            spacing=16,
            expand=True,
        ), refresh_table

    def requirements_view():
        if session_user["role"] != "Administrator":
            return ft.Column(
                [
                    section_title("Weekly requirements", "Configure lessons required by each class."),
                    card(ft.Text("Administrator access is required to edit timetable requirements.", color=MUTED)),
                ],
                spacing=16,
            )
        table_slot = ft.Container(expand=True)

        def refresh():
            rows = fetch_all(
                """
                SELECT r.id, c.class_name, s.subject_name,
                       r.periods_per_week, r.annual_hours, r.stream_specific
                FROM class_subject_requirements r
                JOIN classes c ON c.id = r.class_id
                JOIN subjects s ON s.id = r.subject_id
                ORDER BY c.class_name COLLATE NOCASE, s.subject_name COLLATE NOCASE
                """
            )
            columns = ["Class", "Subject", "Periods per week", "Annual hours", "Stream specific"]
            data_rows = [
                ft.DataRow(
                    cells=[
                        ft.DataCell(ft.Text(str(row[key]), size=12))
                        for key in ("class_name", "subject_name", "periods_per_week")
                    ]
                    + [
                        ft.DataCell(
                            ft.Text(
                                f"{row['annual_hours']:g}" if row["annual_hours"] is not None else "—",
                                size=12,
                            )
                        ),
                        ft.DataCell(ft.Text(str(row["stream_specific"]), size=12)),
                    ]
                    + [
                        ft.DataCell(
                            ft.IconButton(
                                icon=ft.Icons.DELETE_OUTLINE_ROUNDED,
                                tooltip="Delete requirement",
                                icon_color="#F97066",
                                on_click=lambda _, rid=row["id"]: delete_requirement(rid),
                            )
                        )
                    ]
                )
                for row in rows
            ]
            if data_rows:
                table_slot.content = card(
                    ft.Row(
                        [
                            ft.DataTable(
                                columns=[ft.DataColumn(ft.Text(name)) for name in (*columns, "Actions")],
                                rows=data_rows,
                                heading_row_color=SECONDARY,
                            )
                        ],
                        scroll=ft.ScrollMode.AUTO,
                    ),
                    padding=8,
                )
            else:
                table_slot.content = card(ft.Text("No weekly requirements configured yet.", color=MUTED))
            page.update()

        def delete_requirement(requirement_id):
            execute("DELETE FROM class_subject_requirements WHERE id = ?", (requirement_id,))
            notify("Weekly requirement deleted.")
            refresh()

        def open_requirement_dialog(_=None):
            classes = fetch_all("SELECT id, class_name FROM classes ORDER BY class_name COLLATE NOCASE")
            subjects = fetch_all("SELECT id, subject_name FROM subjects ORDER BY subject_name COLLATE NOCASE")
            if not classes or not subjects:
                notify("Add at least one class and one subject before setting requirements.", error=True)
                return
            class_picker = ft.Dropdown(
                label="Class",
                options=[ft.DropdownOption(key=str(row["id"]), text=row["class_name"]) for row in classes],
                expand=True,
            )
            subject_picker = ft.Dropdown(
                label="Subject",
                options=[ft.DropdownOption(key=str(row["id"]), text=row["subject_name"]) for row in subjects],
                expand=True,
            )
            periods = ft.TextField(label="Periods per week (1–50)", value="5", keyboard_type=ft.KeyboardType.NUMBER)
            annual_hours = ft.TextField(
                label="Annual hours (optional)",
                keyboard_type=ft.KeyboardType.NUMBER,
            )
            stream_specific = ft.Checkbox(label="Stream-specific subject")

            def save(_):
                try:
                    count = int(periods.value or "")
                    class_id = int(class_picker.value or "")
                    subject_id = int(subject_picker.value or "")
                    entered_hours = (annual_hours.value or "").strip()
                    hours = float(entered_hours) if entered_hours else None
                    if not 1 <= count <= 50 or (hours is not None and hours <= 0):
                        raise ValueError
                except ValueError:
                    notify("Choose a class and subject, enter 1–50 periods, and use positive annual hours.", error=True)
                    return
                try:
                    execute(
                        """
                        INSERT INTO class_subject_requirements
                            (class_id, subject_id, periods_per_week, annual_hours, stream_specific)
                        VALUES (?, ?, ?, ?, ?)
                        ON CONFLICT(class_id, subject_id)
                        DO UPDATE SET periods_per_week = excluded.periods_per_week,
                                      annual_hours = excluded.annual_hours,
                                      stream_specific = excluded.stream_specific
                        """,
                        (
                            class_id,
                            subject_id,
                            count,
                            hours,
                            int(bool(stream_specific.value)),
                        ),
                    )
                except sqlite3.IntegrityError as exc:
                    notify(f"Could not save this requirement: {exc}", error=True)
                    return
                page.pop_dialog()
                notify("Weekly requirement saved.")
                refresh()

            dialog = ft.AlertDialog(
                modal=True,
                title=ft.Text("Add weekly requirement"),
                content=ft.Column(
                    [class_picker, subject_picker, periods, annual_hours, stream_specific],
                    tight=True,
                    spacing=12,
                ),
                actions=[
                    ft.TextButton("Cancel", on_click=lambda _: page.pop_dialog()),
                    ft.FilledButton("Save", on_click=save),
                ],
            )
            page.show_dialog(dialog)

        refresh()
        return ft.Column(
            [
                ft.Row(
                    [
                        section_title("Weekly requirements", "Assign each class the lessons it needs each week."),
                        ft.FilledButton("Add requirement", icon=ft.Icons.ADD_ROUNDED, on_click=open_requirement_dialog),
                    ],
                    alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                    wrap=True,
                ),
                ft.Text(
                    "Current TIE requirements are loaded for all configured classes. O-Level electives remain "
                    "unassigned. Periods are 40 minutes: up to 9 daily for academic classes and 10 for vocational "
                    "classes. Amali modules store 290 annual hours each; 11 weekly periods schedule about 286 hours "
                    "over 39 weeks.",
                    size=13,
                    color=MUTED,
                ),
                ft.Text(
                    "Sources: TIE Curriculum for Ordinary Secondary Education, Form I–IV (2026); "
                    "Curriculum for Advanced Secondary Education, Form V–VI (2023 edition).",
                    size=12,
                    color=MUTED,
                ),
                table_slot,
            ],
            spacing=16,
            expand=True,
        )

    def schedule_view():
        semester = ft.TextField(label="Semester", value="Semester 1", width=180)
        year = ft.TextField(label="Academic year", value=str(datetime.now().year), width=150)
        weekday_picker = ft.Dropdown(
            label="Weekday",
            width=180,
            value="All weekdays",
            options=[
                ft.DropdownOption(key="All weekdays", text="All weekdays"),
                *[
                    ft.DropdownOption(key=day, text=day)
                    for day in ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday")
                ],
            ],
            on_select=lambda _: refresh(),
        )
        class_types = fetch_all("SELECT DISTINCT class_type FROM classes WHERE class_type IS NOT NULL ORDER BY class_type")
        level_picker = ft.Dropdown(
            label="Class scope",
            width=220,
            options=[ft.DropdownOption(key="", text="All classes")]
            + [ft.DropdownOption(key=row["class_type"], text=row["class_type"]) for row in class_types],
            value="",
        )
        preferences = ft.TextField(
            label="Scheduling preferences (optional)",
            hint_text="For example: prefer mathematics in the morning",
            expand=True,
        )
        schedule_slot = ft.Container(expand=True)

        def refresh():
            saved_periods = {
                (row["start_time"], row["end_time"])
                for row in fetch_all(
                    """
                    SELECT DISTINCT start_time, end_time
                    FROM timetables
                    WHERE day IN ('Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday')
                    """
                )
            }
            has_outdated_periods = bool(saved_periods - set(TIME_SLOTS))
            schedule_slot.content = card(
                ft.Column(
                    [
                        ft.Column(
                            [
                                ft.Text(
                                    "SCHOOL MASTER TIMETABLE",
                                    size=19,
                                    weight=ft.FontWeight.W_700,
                                    color=INK,
                                    text_align=ft.TextAlign.CENTER,
                                ),
                                ft.Text(
                                    (
                                        "Monday – Friday"
                                        if weekday_picker.value == "All weekdays"
                                        else weekday_picker.value
                                    )
                                    + " · All classes",
                                    size=12,
                                    color=MUTED,
                                    text_align=ft.TextAlign.CENTER,
                                ),
                            ],
                            horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                            spacing=4,
                        ),
                        ft.Text(
                            "Saved timetable entries use the previous period structure. They are kept; "
                            "generate a new timetable to apply the current TIE periods."
                            if has_outdated_periods
                            else "",
                            size=12,
                            color="#F3B843",
                            visible=has_outdated_periods,
                        ),
                        build_schedule_table(weekday_picker.value or "All weekdays"),
                    ],
                    spacing=12,
                    expand=True,
                ),
                padding=14,
                expand=True,
            )
            page.update()

        async def create_schedule(_):
            if not semester.value.strip() or not year.value.strip():
                notify("Enter the semester and academic year.", error=True)
                return
            try:
                generator = TimetableGenerator(semester=semester.value.strip(), academic_year=year.value.strip())
                schedule = await asyncio.to_thread(
                    generator.generate,
                    preferences.value.strip(),
                    level_picker.value or None,
                )
                if not schedule:
                    notify("No lessons were generated. Check the timetable requirements.", error=True)
                    return
                saved = await asyncio.to_thread(generator.save, schedule, True)
            except (RuntimeError, ValueError, sqlite3.Error) as exc:
                notify(f"Timetable was not generated: {exc}", error=True)
                return
            notify(f"Generated and saved {saved} lessons. The previous timetable was replaced.")
            refresh()

        def confirm_generation(_):
            async def confirmed_generate(event):
                page.pop_dialog()
                await create_schedule(event)

            dialog = ft.AlertDialog(
                modal=True,
                title=ft.Text("Replace the current timetable?"),
                content=ft.Text("Generating a new timetable replaces all existing timetable entries."),
                actions=[
                    ft.TextButton("Cancel", on_click=lambda _: page.pop_dialog()),
                    ft.FilledButton(
                        "Generate timetable",
                        icon=ft.Icons.AUTO_AWESOME_ROUNDED,
                        on_click=confirmed_generate,
                    ),
                ],
            )
            page.show_dialog(dialog)

        actions = []
        if session_user["role"] == "Administrator":
            actions.append(
                ft.FilledButton(
                    "Generate timetable",
                    icon=ft.Icons.AUTO_AWESOME_ROUNDED,
                    on_click=confirm_generation,
                )
            )
        refresh()
        return ft.Column(
            [
                ft.Row(
                    [
                        section_title("Timetable", "Review lessons or generate a conflict-checked weekly schedule."),
                        *actions,
                    ],
                    alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                    wrap=True,
                ),
                card(
                    ft.Row([weekday_picker], wrap=True, spacing=12),
                    padding=12,
                ),
                card(
                    ft.Column(
                        [
                            ft.Text("Schedule settings", size=16, weight=ft.FontWeight.W_600, color=INK),
                            ft.Row([semester, year, level_picker], wrap=True, spacing=12),
                            ft.Row([preferences], spacing=12),
                        ],
                        spacing=12,
                    )
                )
                if session_user["role"] == "Administrator"
                else ft.Container(),
                schedule_slot,
            ],
            spacing=16,
            expand=True,
        )

    def users_view():
        if session_user["role"] != "Administrator":
            return ft.Column(
                [
                    section_title("Accounts", "Manage access to the timetable system."),
                    card(ft.Text("Administrator access is required to manage accounts.", color=MUTED)),
                ],
                spacing=16,
            )
        table_slot = ft.Container(expand=True)

        def refresh():
            rows = fetch_all(
                "SELECT id, full_name, username, role, created_at FROM users ORDER BY full_name COLLATE NOCASE"
            )
            data_rows = [
                ft.DataRow(
                    cells=[
                        ft.DataCell(ft.Text(str(row[key] or "—"), size=12))
                        for key in ("id", "full_name", "username", "role", "created_at")
                    ]
                    + [
                        ft.DataCell(
                            ft.IconButton(
                                icon=ft.Icons.DELETE_OUTLINE_ROUNDED,
                                icon_color="#F97066",
                                tooltip="Delete account",
                                disabled=row["id"] == session_user["id"],
                                on_click=lambda _, user_id=row["id"]: delete_user(user_id),
                            )
                        )
                    ]
                )
                for row in rows
            ]
            table_slot.content = card(
                ft.Row(
                    [
                        ft.DataTable(
                            columns=[
                                ft.DataColumn(ft.Text(label))
                                for label in ("ID", "Full name", "Username", "Role", "Created", "Actions")
                            ],
                            rows=data_rows,
                            heading_row_color=SECONDARY,
                        )
                    ],
                    scroll=ft.ScrollMode.AUTO,
                ),
                padding=8,
            )
            page.update()

        def delete_user(user_id):
            execute("DELETE FROM users WHERE id = ?", (user_id,))
            notify("Account deleted.")
            refresh()

        def open_user_dialog(_):
            full_name = ft.TextField(label="Full name")
            username = ft.TextField(label="Username")
            password = ft.TextField(label="Password", password=True, can_reveal_password=True)
            role = ft.Dropdown(
                label="Role",
                value="Teacher",
                options=[
                    ft.DropdownOption(key=value, text=value)
                    for value in ("Teacher", "Student", "Administrator")
                ],
            )

            def save(_):
                name = full_name.value.strip()
                login = username.value.strip().lower()
                secret = password.value or ""
                if not name or not login or len(secret) < 4:
                    notify("Enter a name, username, and a password of at least 4 characters.", error=True)
                    return
                try:
                    execute(
                        "INSERT INTO users (full_name, username, password_hash, role) VALUES (?, ?, ?, ?)",
                        (name, login, _password_digest(secret), role.value),
                    )
                except sqlite3.IntegrityError as exc:
                    notify(f"Could not create this account: {exc}", error=True)
                    return
                page.pop_dialog()
                notify("Account created.")
                refresh()

            dialog = ft.AlertDialog(
                modal=True,
                title=ft.Text("Create account"),
                content=ft.Column([full_name, username, password, role], tight=True, spacing=12),
                actions=[
                    ft.TextButton("Cancel", on_click=lambda _: page.pop_dialog()),
                    ft.FilledButton("Create account", on_click=save),
                ],
            )
            page.show_dialog(dialog)

        refresh()
        return ft.Column(
            [
                ft.Row(
                    [
                        section_title("Accounts", "Manage users who can sign in to this app."),
                        ft.FilledButton("Create account", icon=ft.Icons.ADD_ROUNDED, on_click=open_user_dialog),
                    ],
                    alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                    wrap=True,
                ),
                table_slot,
            ],
            spacing=16,
            expand=True,
        )

    sections = [
        ("overview", "Overview", "SPACE_DASHBOARD_ROUNDED"),
        ("schedule", "Timetable", "CALENDAR_MONTH_ROUNDED"),
        ("classes", "Classes", "CLASS_ROUNDED"),
        ("students", "Students", "PEOPLE_ROUNDED"),
        ("teachers", "Teachers", "PERSON_OUTLINE_ROUNDED"),
        ("subjects", "Subjects", "MENU_BOOK_ROUNDED"),
        ("classrooms", "Classrooms", "MEETING_ROOM_ROUNDED"),
        ("syllabi", "Syllabi", "AUTO_STORIES_ROUNDED"),
        ("requirements", "Requirements", "TUNE_ROUNDED"),
    ]

    def navigate(section, keep_search=False):
        nonlocal active_section, global_search_query
        active_section = section
        if not keep_search:
            global_search_query = ""
        render_app()

    global_search_query = ""

    def search_records(event):
        nonlocal global_search_query
        query = event.control.value.strip()
        if not query:
            return
        like = f"%{query}%"
        searches = (
            ("students", "student_name"),
            ("classes", "class_name"),
            ("teachers", "teacher_name"),
            ("subjects", "subject_name"),
            ("classrooms", "room_name"),
            ("syllabi", "subject_name"),
        )
        for section, column in searches:
            if fetch_all(
                f"SELECT id FROM {RESOURCES[section].table} WHERE {column} LIKE ? LIMIT 1",
                (like,),
            ):
                global_search_query = query
                navigate(section, keep_search=True)
                return
        notify(f'No records found for "{query}".', error=True)

    def render_app():
        page.clean()
        nav_items = []
        visible_sections = list(sections)
        if session_user["role"] == "Administrator":
            visible_sections.append(("users", "Accounts", "GROUP_ROUNDED"))
        for key, label, icon_name in visible_sections:
            selected = active_section == key
            nav_items.append(
                ft.Container(
                    content=ft.Row(
                        [
                            _icon(icon_name, "#9288FF" if selected else "#8290A4", 19),
                            ft.Text(
                                label,
                                size=14,
                                weight=ft.FontWeight.W_600 if selected else ft.FontWeight.W_500,
                                color="#F2F4F8" if selected else "#A5B0C0",
                            ),
                        ],
                        spacing=12,
                    ),
                    padding=ft.Padding.symmetric(horizontal=14, vertical=11),
                    border_radius=9,
                    bgcolor="#282544" if selected else None,
                    on_click=lambda _, destination=key: navigate(destination),
                    ink=True,
                )
            )
        sidebar = ft.Container(
            content=ft.Column(
                [
                    ft.Row(
                        [
                            ft.Image(src="ttms_icon.png", width=36, height=36, fit=ft.BoxFit.CONTAIN),
                            ft.Column(
                                [
                                    ft.Text(SCHOOL_NAME, size=11, weight=ft.FontWeight.W_600, color=INK),
                                    ft.Text("School timetable manager", size=10, color=MUTED),
                                ],
                                spacing=0,
                            ),
                        ],
                        spacing=8,
                    ),
                    ft.Divider(color=OUTLINE),
                    ft.Column(nav_items, spacing=3, scroll=ft.ScrollMode.AUTO, expand=True),
                    ft.Divider(color=OUTLINE),
                    ft.Row(
                        [
                            ft.CircleAvatar(
                                content=ft.Text(session_user["full_name"][:1].upper(), color=ft.Colors.WHITE),
                                bgcolor=ACCENT,
                                radius=19,
                            ),
                            ft.Column(
                                [
                                    ft.Text(session_user["full_name"], size=12, weight=ft.FontWeight.W_600, color=INK),
                                    ft.Text(session_user["role"], size=11, color=MUTED),
                                ],
                                spacing=1,
                                expand=True,
                            ),
                            ft.IconButton(
                                icon=ft.Icons.LOGOUT_ROUNDED,
                                icon_color=MUTED,
                                tooltip="Sign out",
                                on_click=sign_out,
                            ),
                        ],
                        spacing=8,
                    ),
                ],
                spacing=12,
                expand=True,
            ),
            width=246,
            bgcolor="#111A28",
            padding=16,
            border=ft.Border.all(1, OUTLINE),
        )
        content = content_for_section(active_section)
        header = ft.Container(
            content=ft.Row(
                [
                    ft.Image(src="ttms_icon.png", width=30, height=30, fit=ft.BoxFit.CONTAIN),
                    ft.Text(SCHOOL_NAME, size=12, weight=ft.FontWeight.W_600, color=INK),
                    ft.Container(
                        content=ft.Row(
                            [
                                ft.Text(
                                    session_user["role"],
                                    size=10,
                                    color="#B7B0FF",
                                ),
                                _icon("ARROW_DROP_DOWN_ROUNDED", "#B7B0FF", 15),
                            ],
                            spacing=2,
                        ),
                        bgcolor="#282544",
                        border_radius=8,
                        padding=ft.Padding.symmetric(horizontal=9, vertical=6),
                    ),
                    ft.Container(expand=True),
                    ft.Container(
                        content=ft.TextField(
                            hint_text="Search anything...",
                            prefix_icon=ft.Icons.SEARCH_ROUNDED,
                            on_submit=search_records,
                            value=global_search_query,
                            width=245,
                            height=38,
                            text_size=12,
                            content_padding=10,
                            border_radius=8,
                            filled=True,
                            bgcolor=SECONDARY,
                            border_color=OUTLINE,
                            focused_border_color=ACCENT,
                            hint_style=ft.TextStyle(color=MUTED, size=12),
                        ),
                        width=245,
                        height=38,
                    ),
                    ft.IconButton(
                        icon=ft.Icons.NOTIFICATIONS_NONE_ROUNDED,
                        icon_color="#9AA7B9",
                        tooltip="Notifications",
                        on_click=lambda _: notify("You're all caught up."),
                    ),
                    ft.CircleAvatar(
                        content=ft.Text(session_user["full_name"][:1].upper(), color=ft.Colors.WHITE),
                        bgcolor=ACCENT,
                        radius=16,
                    ),
                    ft.Column(
                        [
                            ft.Text(session_user["full_name"], size=11, color=INK),
                            ft.Text(session_user["role"], size=10, color=MUTED),
                        ],
                        spacing=0,
                    ),
                ],
                spacing=11,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
            ),
            bgcolor="#111A28",
            border=ft.Border.all(1, OUTLINE),
            border_radius=11,
            padding=ft.Padding.symmetric(horizontal=14, vertical=8),
        )
        main_area = ft.Container(
            content=ft.Column(
                [
                    header,
                    content,
                ],
                spacing=18,
                expand=True,
            ),
            padding=ft.Padding.only(left=22, top=14, right=22, bottom=18),
            expand=True,
        )
        page.add(ft.Row([sidebar, main_area], spacing=0, expand=True))
        page.update()

    def content_for_section(section):
        if section == "overview":
            return load_overview()
        if section == "schedule":
            return schedule_view()
        if section == "requirements":
            return requirements_view()
        if section == "users":
            return users_view()
        resource = RESOURCES.get(section)
        if resource:
            view, refresh = resource_view(resource)
            refresh()
            return view
        return ft.Text("Page not found.", color=MUTED)

    def sign_out(_):
        nonlocal session_user, active_section
        session_user = None
        active_section = "overview"
        show_login()

    def show_login(register=False):
        page.clean()
        full_name = ft.TextField(label="Full name", visible=register, prefix_icon=ft.Icons.PERSON_OUTLINE_ROUNDED)
        username = ft.TextField(label="Username", prefix_icon=ft.Icons.PERSON_OUTLINE_ROUNDED)
        password = ft.TextField(
            label="Password",
            password=True,
            can_reveal_password=True,
            prefix_icon=ft.Icons.LOCK_OUTLINE_ROUNDED,
            on_submit=lambda _: authenticate(),
        )
        role = ft.Dropdown(
            label="Account type",
            value="Teacher",
            visible=register,
            options=[
                ft.DropdownOption(key="Teacher", text="Teacher"),
                ft.DropdownOption(key="Student", text="Student"),
            ],
        )
        message = ft.Text("", color="#B42318", size=12)
        heading = "Create your account" if register else "Welcome back"
        subheading = "Sign up to manage your school timetable." if register else "Sign in to continue to your school workspace."

        def authenticate():
            nonlocal session_user
            login = username.value.strip().lower()
            secret = password.value or ""
            if not login or not secret:
                message.value = "Enter your username and password."
                page.update()
                return
            rows = fetch_all(
                "SELECT id, full_name, username, password_hash, role FROM users WHERE lower(username) = ?",
                (login,),
            )
            if not rows or not _verify_password(secret, rows[0]["password_hash"]):
                message.value = "Username or password is incorrect."
                page.update()
                return
            user = rows[0]
            if not user["password_hash"].startswith("pbkdf2_sha256$"):
                execute(
                    "UPDATE users SET password_hash = ? WHERE id = ?",
                    (_password_digest(secret), user["id"]),
                )
            session_user = user
            navigate("overview")

        def register_account(_):
            nonlocal session_user
            name = full_name.value.strip()
            login = username.value.strip().lower()
            secret = password.value or ""
            if not name or not login or len(secret) < 4:
                message.value = "Enter your name, username, and a password of at least 4 characters."
                page.update()
                return
            try:
                user_id = execute(
                    "INSERT INTO users (full_name, username, password_hash, role) VALUES (?, ?, ?, ?)",
                    (name, login, _password_digest(secret), role.value),
                )
            except sqlite3.IntegrityError:
                message.value = "That username is already registered."
                page.update()
                return
            session_user = {
                "id": user_id,
                "full_name": name,
                "username": login,
                "role": role.value,
            }
            navigate("overview")

        form_controls = [full_name, username, password, role, message]
        form_controls.append(
            ft.FilledButton(
                "Create account" if register else "Sign in",
                icon=ft.Icons.ARROW_FORWARD_ROUNDED if not register else ft.Icons.PERSON_ADD_ALT_1_ROUNDED,
                on_click=register_account if register else lambda _: authenticate(),
                expand=True,
            )
        )
        switch = ft.TextButton(
            "Already have an account? Sign in" if register else "Create a teacher or student account",
            on_click=lambda _: show_login(not register),
        )
        logo = ft.Image(src="ttms_icon.png", width=72, height=72, fit=ft.BoxFit.CONTAIN)
        form = ft.Container(
            content=ft.Column(
                [
                    logo,
                    ft.Text(heading, size=27, weight=ft.FontWeight.W_700, color=INK),
                    ft.Text(subheading, size=13, color=MUTED),
                    ft.Column(form_controls, spacing=12),
                    switch,
                ],
                spacing=14,
                horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                tight=True,
            ),
            width=430,
            padding=36,
            bgcolor=SURFACE,
            border=ft.Border.all(1, OUTLINE),
            border_radius=20,
        )
        page.add(
            ft.Container(
                content=ft.Column(
                    [
                        ft.Text(SCHOOL_NAME, size=14, weight=ft.FontWeight.W_700, color=ACCENT),
                        form,
                        ft.Text(SCHOOL_NAME, size=12, color=MUTED),
                    ],
                    alignment=ft.MainAxisAlignment.CENTER,
                    horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                    spacing=22,
                    expand=True,
                ),
                expand=True,
                alignment=ft.Alignment.CENTER,
                padding=24,
            )
        )
        page.update()

    show_login()
