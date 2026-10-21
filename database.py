# database.py
import os
import re
import sqlite3

DB_PATH = os.path.join(os.path.dirname(__file__), "database.db")

def get_connection():
    return sqlite3.connect(DB_PATH)

def init_db():
    conn = get_connection()
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS students (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        student_name TEXT NOT NULL,
        gender TEXT,
        student_class TEXT,
        phone TEXT,
        guardian TEXT
    )''')
    c.execute('''CREATE TABLE IF NOT EXISTS subjects (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        subject_name TEXT NOT NULL,
        subject_code TEXT UNIQUE,
        description TEXT,
        delivery_location TEXT DEFAULT 'Classroom',
        is_extra INTEGER NOT NULL DEFAULT 0,
        is_taught INTEGER NOT NULL DEFAULT 1,
        assigned_teacher_id INTEGER,
        category TEXT DEFAULT 'O-Level',
        track TEXT DEFAULT 'Normal'
    )''')
    c.execute('''CREATE TABLE IF NOT EXISTS syllabi (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        curriculum TEXT NOT NULL CHECK(curriculum IN ('New TIE', 'Old TIE', 'Vocational')),
        form_level TEXT NOT NULL,
        subject_name TEXT NOT NULL,
        syllabus_text TEXT NOT NULL,
        source TEXT,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    )''')
    syllabus_sql = c.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='syllabi'"
    ).fetchone()[0] or ""
    if "Vocational" not in syllabus_sql:
        c.execute("ALTER TABLE syllabi RENAME TO syllabi_legacy")
        c.execute('''CREATE TABLE syllabi (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            curriculum TEXT NOT NULL CHECK(curriculum IN ('New TIE', 'Old TIE', 'Vocational')),
            form_level TEXT NOT NULL,
            subject_name TEXT NOT NULL,
            syllabus_text TEXT NOT NULL,
            source TEXT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )''')
        c.execute("""INSERT INTO syllabi
            (id, curriculum, form_level, subject_name, syllabus_text, source, created_at)
            SELECT id, curriculum, form_level, subject_name, syllabus_text, source, created_at
            FROM syllabi_legacy""")
        c.execute("DROP TABLE syllabi_legacy")
    c.execute('''CREATE TABLE IF NOT EXISTS classes (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        class_name TEXT NOT NULL,
        class_code TEXT UNIQUE,
        grade TEXT,
        academic_year TEXT,
        homeroom_teacher TEXT,
        curriculum TEXT DEFAULT 'New TIE',
        class_type TEXT DEFAULT 'O-Level',
        combination TEXT DEFAULT ''
    )''')
    c.execute('''CREATE TABLE IF NOT EXISTS class_subject_requirements (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        class_id INTEGER NOT NULL,
        subject_id INTEGER NOT NULL,
        periods_per_week INTEGER NOT NULL CHECK(periods_per_week > 0),
        annual_hours REAL CHECK(annual_hours IS NULL OR annual_hours > 0),
        stream_specific INTEGER NOT NULL DEFAULT 0,
        UNIQUE(class_id, subject_id),
        FOREIGN KEY (class_id) REFERENCES classes(id) ON DELETE CASCADE,
        FOREIGN KEY (subject_id) REFERENCES subjects(id) ON DELETE CASCADE
    )''')
    requirement_columns = [
        column[1] for column in c.execute("PRAGMA table_info(class_subject_requirements)").fetchall()
    ]
    if "annual_hours" not in requirement_columns:
        c.execute("ALTER TABLE class_subject_requirements ADD COLUMN annual_hours REAL")
    subjects_existing = [row[1] for row in c.execute("PRAGMA table_info(subjects)").fetchall()]
    track_was_missing = "track" not in subjects_existing
    for table, columns in {
        "subjects": {
            "delivery_location": "TEXT DEFAULT 'Classroom'",
            "is_extra": "INTEGER NOT NULL DEFAULT 0",
            "is_taught": "INTEGER NOT NULL DEFAULT 1",
            "assigned_teacher_id": "INTEGER",
            "category": "TEXT DEFAULT 'O-Level'",
            "track": "TEXT DEFAULT 'Normal'",
        },
        "classes": {
            "curriculum": "TEXT DEFAULT 'New TIE'",
            "class_type": "TEXT DEFAULT 'O-Level'",
            "combination": "TEXT DEFAULT ''",
        },
    }.items():
        existing = [row[1] for row in c.execute(f"PRAGMA table_info({table})").fetchall()]
        for name, definition in columns.items():
            if name not in existing:
                c.execute(f"ALTER TABLE {table} ADD COLUMN {name} {definition}")
    # Tag A-Level / shared subjects only when category is still empty.
    c.execute("""
        UPDATE subjects
        SET category = 'A-Level'
        WHERE (category IS NULL OR TRIM(category) = '')
          AND (
                LOWER(COALESCE(description, '')) LIKE '%a-level%'
             OR LOWER(COALESCE(subject_name, '')) IN (
                'general studies', 'basic applied mathematics',
                'english language', 'literature in english',
                'information & computer studies', 'commerce', 'accountancy',
                'entrepreneurship', 'vocational/technical subjects'
             )
          )
    """)
    c.execute("""
        UPDATE subjects
        SET category = 'Both'
        WHERE (category IS NULL OR TRIM(category) = '')
          AND LOWER(COALESCE(subject_name, '')) IN (
            'mathematics', 'physics', 'chemistry', 'biology',
            'history', 'geography', 'economics', 'kiswahili',
            'computer science', 'agriculture', 'french', 'arabic',
            'fine art', 'music', 'food & nutrition', 'physical education'
        )
    """)
    # Seed known vocational / amali subjects only on first track migration.
    if track_was_missing:
        c.execute("""
            UPDATE subjects
            SET track = 'Vocational'
            WHERE LOWER(COALESCE(subject_name, '')) IN (
                'htm', 'programing', 'programming', 'computer aplication',
                'computer application', 'computer applications', 'stem',
                'vocational/technical subjects', 'life skills', 'personal study'
            )
        """)
    c.execute("""
        UPDATE classes
        SET class_type = 'Vocational'
        WHERE (
            LOWER(COALESCE(curriculum, '')) = 'vocational'
            OR LOWER(COALESCE(class_name, '')) LIKE '%amali%'
        )
          AND COALESCE(class_type, '') != 'Vocational'
    """)
    # Default A-Level class combinations when none are set.
    defaults = [
        ("PCM", "Form 5A"), ("PCB", "Form 5 B"), ("HGL", "Form  5 C"),
        ("PCM", "Form 6 A"), ("CBG", "Form 6 B"), ("ECA", "Form 6C"),
    ]
    for combination, class_name in defaults:
        c.execute("""
            UPDATE classes
            SET combination = ?
            WHERE class_type = 'A-Level'
              AND class_name = ?
              AND (combination IS NULL OR TRIM(combination) = '')
        """, (combination, class_name))
    c.execute('''CREATE TABLE IF NOT EXISTS teachers (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        teacher_name TEXT NOT NULL,
        teacher_code TEXT,
        gender TEXT,
        phone TEXT,
        email TEXT,
        subject TEXT,
        department TEXT,
        qualification TEXT,
        specialization TEXT,
        subject_id INTEGER,
        class_id INTEGER,
        status TEXT DEFAULT 'Active',
        hire_date TEXT,
        notes TEXT,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (subject_id) REFERENCES subjects(id),
        FOREIGN KEY (class_id) REFERENCES classes(id)
    )''')
    teacher_columns = [column[1] for column in c.execute("PRAGMA table_info(teachers)").fetchall()]
    for col_name, col_definition in {
        "teacher_code": "TEXT",
        "department": "TEXT",
        "qualification": "TEXT",
        "specialization": "TEXT",
        "subject_id": "INTEGER",
        "class_id": "INTEGER",
        "status": "TEXT DEFAULT 'Active'",
        "hire_date": "TEXT",
        "notes": "TEXT",
        "created_at": "TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP",
        "classes_taught": "TEXT",
    }.items():
        if col_name not in teacher_columns:
            c.execute(f"ALTER TABLE teachers ADD COLUMN {col_name} {col_definition}")
    c.execute('''CREATE TABLE IF NOT EXISTS classrooms (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        room_name TEXT NOT NULL,
        room_code TEXT UNIQUE,
        capacity INTEGER DEFAULT 30,
        floor INTEGER,
        status TEXT DEFAULT 'Available'
    )''')
    c.execute('''CREATE TABLE IF NOT EXISTS timetables (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        class_id INTEGER,
        subject_id INTEGER,
        teacher_id INTEGER,
        classroom_id INTEGER,
        day TEXT,
        start_time TEXT,
        end_time TEXT,
        semester TEXT,
        academic_year TEXT,
        FOREIGN KEY (class_id) REFERENCES classes(id),
        FOREIGN KEY (subject_id) REFERENCES subjects(id),
        FOREIGN KEY (teacher_id) REFERENCES teachers(id),
        FOREIGN KEY (classroom_id) REFERENCES classrooms(id)
    )''')
    c.execute('''CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        full_name TEXT NOT NULL,
        username TEXT NOT NULL UNIQUE,
        password_hash TEXT NOT NULL,
        role TEXT NOT NULL DEFAULT 'Teacher',
        profile_photo TEXT DEFAULT NULL,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    )''')
    user_columns = [column[1] for column in c.execute("PRAGMA table_info(users)").fetchall()]
    if "profile_photo" not in user_columns:
        c.execute("ALTER TABLE users ADD COLUMN profile_photo TEXT DEFAULT NULL")
    c.execute("""
        DELETE FROM users
        WHERE username = ?
          AND role = 'Administrator'
          AND password_hash = ?
    """, ("creator", "5e884898da28047151d0e56f8dc6292773603d0d6aabbdd62a11ef721d1542d8"))
    c.execute("""
        CREATE TABLE IF NOT EXISTS app_migrations (
            migration_key TEXT PRIMARY KEY,
            applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
    """)
    _seed_current_tie_requirements(c)
    conn.commit()
    conn.close()


def _seed_current_tie_requirements(cursor):
    migration_key = "current_tie_subject_requirements_2026_01"
    if cursor.execute(
        "SELECT 1 FROM app_migrations WHERE migration_key = ?", (migration_key,)
    ).fetchone():
        return

    classes = cursor.execute(
        "SELECT id, class_name, class_type FROM classes ORDER BY id"
    ).fetchall()
    if not classes:
        return

    def configure_subject(code, name, description, category, track):
        existing = cursor.execute(
            "SELECT id FROM subjects WHERE subject_code = ?", (code,)
        ).fetchone()
        if existing:
            subject_id = existing[0]
            cursor.execute(
                """
                UPDATE subjects
                SET subject_name = ?, description = ?, category = ?, track = ?, is_taught = 1
                WHERE id = ?
                """,
                (name, description, category, track, subject_id),
            )
        else:
            cursor.execute(
                """
                INSERT INTO subjects
                    (subject_name, subject_code, description, category, track, is_taught)
                VALUES (?, ?, ?, ?, ?, 1)
                """,
                (name, code, description, category, track),
            )
            subject_id = cursor.lastrowid
        return subject_id

    subject_ids = {
        "Mathematics": configure_subject(
            "MATH101", "Mathematics", "TIE compulsory subject", "Both", "Both"
        ),
        "Kiswahili": configure_subject(
            "KIS101", "Kiswahili", "TIE compulsory subject", "Both", "Normal"
        ),
        "English": configure_subject(
            "ENG101", "English", "TIE compulsory subject", "Both", "Both"
        ),
        "Business Studies": configure_subject(
            "B03", "Business Studies", "TIE compulsory subject", "Both", "Both"
        ),
        "Geography": configure_subject(
            "GEO101", "Geography", "TIE core/elective subject", "Both", "Both"
        ),
        "Historia ya Tanzania na Maadili": configure_subject(
            "A06", "Historia ya Tanzania na Maadili", "TIE compulsory subject", "Both", "Both"
        ),
        "Physics": configure_subject(
            "PHY101", "Physics", "TIE subject combination", "Both", "Normal"
        ),
        "Chemistry": configure_subject(
            "CHEM101", "Chemistry", "TIE subject combination", "Both", "Normal"
        ),
        "Biology": configure_subject(
            "BIO101", "Biology", "TIE subject combination", "Both", "Normal"
        ),
        "History": configure_subject(
            "HIST101", "History", "TIE subject combination", "Both", "Normal"
        ),
        "Economics": configure_subject(
            "B02", "Economics", "TIE subject combination", "A-Level", "Normal"
        ),
        "Accountancy": configure_subject(
            "A", "Accountancy", "TIE subject combination", "A-Level", "Normal"
        ),
        "Computer Application": configure_subject(
            "A09", "Computer Application", "Vocational module", "O-Level", "Vocational"
        ),
        "Computer Programming": configure_subject(
            "A08", "Computer Programming", "Vocational module", "O-Level", "Vocational"
        ),
        "Engineering Science": configure_subject(
            "ENGSCI-VOC", "Engineering Science", "Vocational module", "O-Level", "Vocational"
        ),
        "Academic Communication": configure_subject(
            "AC-V-VI", "Academic Communication", "TIE compulsory subject", "A-Level", "Normal"
        ),
    }

    cursor.execute("""
        UPDATE classes
        SET curriculum = 'New TIE'
        WHERE curriculum = 'Old TIE' AND class_type IN ('O-Level', 'A-Level')
    """)
    cursor.execute("""
        UPDATE classes
        SET combination = 'EBuAc'
        WHERE class_name = 'Form 6C' AND combination = 'ECA'
    """)

    requirements = []
    combinations = {
        "PCM": ("Physics", "Chemistry", "Mathematics"),
        "PCB": ("Physics", "Chemistry", "Biology"),
        "HGL": ("History", "Geography", "English"),
        "CBG": ("Chemistry", "Biology", "Geography"),
        "EBUAC": ("Economics", "Business Studies", "Accountancy"),
    }
    for class_id, class_name, class_type in classes:
        is_vocational = class_type == "Vocational" or "amali" in class_name.lower()
        form_match = re.search(r"FORM\s*(\d+)", class_name.upper())
        form_number = int(form_match.group(1)) if form_match else None
        if is_vocational:
            class_requirements = (
                ("Mathematics", 5, None, 0),
                ("Business Studies", 2, None, 0),
                ("English", 5, None, 0),
                ("Historia ya Tanzania na Maadili", 2, None, 0),
                ("Geography", 3, None, 0),
                ("Computer Application", 11, 290, 1),
                ("Computer Programming", 11, 290, 1),
                ("Engineering Science", 11, 290, 1),
            )
        elif class_type == "O-Level":
            if form_number in (1, 2):
                class_requirements = (
                    ("Mathematics", 5, None, 0),
                    ("Kiswahili", 4, None, 0),
                    ("English", 5, None, 0),
                    ("Business Studies", 2, None, 0),
                    ("Geography", 3, None, 0),
                    ("Historia ya Tanzania na Maadili", 2, None, 0),
                )
            elif form_number in (3, 4):
                class_requirements = (
                    ("Mathematics", 5, None, 0),
                    ("Kiswahili", 4, None, 0),
                    ("English", 5, None, 0),
                    ("Business Studies", 4, None, 0),
                    ("Geography", 4, None, 0),
                    ("Historia ya Tanzania na Maadili", 2, None, 0),
                )
            else:
                continue
        elif class_type == "A-Level":
            combination = (cursor.execute(
                "SELECT combination FROM classes WHERE id = ?", (class_id,)
            ).fetchone()[0] or "").strip().upper()
            combination_subjects = combinations.get(combination)
            if not combination_subjects:
                raise ValueError(
                    f"No current TIE subject mapping exists for {class_name} ({combination})."
                )
            class_requirements = tuple(
                (name, 10, None, 1) for name in combination_subjects
            ) + (
                ("Historia ya Tanzania na Maadili", 4, None, 0),
                ("Academic Communication", 4, None, 0),
            )
        else:
            continue

        for subject_name, periods, annual_hours, stream_specific in class_requirements:
            requirements.append(
                (class_id, subject_ids[subject_name], periods, annual_hours, stream_specific)
            )

    for class_id, subject_id, periods, annual_hours, stream_specific in requirements:
        cursor.execute(
            """
            INSERT INTO class_subject_requirements
                (class_id, subject_id, periods_per_week, annual_hours, stream_specific)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(class_id, subject_id) DO UPDATE SET
                periods_per_week = excluded.periods_per_week,
                annual_hours = excluded.annual_hours,
                stream_specific = excluded.stream_specific
            """,
            (class_id, subject_id, periods, annual_hours, stream_specific),
        )

    cursor.execute("""
        DELETE FROM class_subject_requirements
        WHERE subject_id = (SELECT id FROM subjects WHERE subject_name = 'STEM')
          AND class_id IN (
              SELECT id FROM classes
              WHERE class_type = 'Vocational' AND LOWER(class_name) LIKE '%amali%'
          )
    """)
    cursor.execute(
        "INSERT INTO app_migrations (migration_key) VALUES (?)", (migration_key,)
    )


init_db()
