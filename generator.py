# generator.py
import random
import re
from database import get_connection

TIME_SLOTS = [
    ("08:00", "08:40"),
    ("08:45", "09:25"),
    ("09:30", "10:10"),
    ("10:30", "11:10"),
    ("11:15", "11:55"),
    ("12:00", "12:40"),
    ("13:20", "14:00"),
    ("14:05", "14:45"),
    ("14:50", "15:30"),
    ("15:35", "16:15"),
]

class TimetableGenerator:
    def __init__(self, semester="Semester 1", academic_year="2024"):
        self.semester = semester
        self.academic_year = academic_year
        self.days = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"]
        self.periods = list(range(len(TIME_SLOTS)))

    def parse_preferences(self, description):
        rules = {
            "preferred_subjects": [],
            "avoid_subjects": [],
            "preferred_days": [],
            "avoid_days": [],
            "preferred_periods": [],
            "avoid_periods": []
        }

        if not description or not description.strip():
            return rules

        text = description.strip().lower()
        day_map = {
            "monday": "Monday",
            "tuesday": "Tuesday",
            "wednesday": "Wednesday",
            "thursday": "Thursday",
            "friday": "Friday",
            "saturday": "Saturday",
            "sunday": "Sunday",
        }

        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT subject_name FROM subjects")
        subject_names = [row[0] for row in cursor.fetchall()]
        conn.close()

        for day_name, label in day_map.items():
            if day_name in text:
                if any(phrase in text for phrase in [f"avoid {day_name}", f"do not want {day_name}", f"don't want {day_name}", f"not {day_name}"]):
                    rules["avoid_days"].append(label)
                else:
                    rules["preferred_days"].append(label)

        if "morning" in text:
            rules["preferred_periods"].extend([0, 1, 2])
        if "afternoon" in text:
            rules["preferred_periods"].extend([3, 4])
        if "late" in text:
            rules["preferred_periods"].extend([3, 4])
        if "early" in text:
            rules["preferred_periods"].extend([0, 1, 2])

        for subject_name in subject_names:
            subject_key = subject_name.lower()
            if subject_key not in text:
                continue
            pattern = re.compile(rf"(?:avoid|do not want|don't want|not)\s+(?:.*?\b)?{re.escape(subject_key)}")
            if pattern.search(text):
                rules["avoid_subjects"].append(subject_name)
            else:
                rules["preferred_subjects"].append(subject_name)

        return rules

    def _day_order(self, rules):
        day_order = list(self.days)
        preferred = rules.get("preferred_days", [])
        if preferred:
            day_order = [day for day in preferred if day in day_order] + [day for day in day_order if day not in preferred]
        return day_order

    def _period_order(self, rules):
        period_order = list(self.periods)
        preferred = rules.get("preferred_periods", [])
        if preferred:
            period_order = [period for period in preferred if period in period_order] + [period for period in period_order if period not in preferred]
        return period_order

    @staticmethod
    def _class_keys(class_name):
        normalized = re.sub(r"\s+", "", class_name or "").upper()
        match = re.match(r"FORM(\d+)(.*)", normalized)
        if not match:
            return set()
        grade, section = match.groups()
        section = re.sub(r"[^A-Z0-9]", "", section)
        return {f"F{grade}", f"F{grade}{section}"}

    @classmethod
    def _teacher_can_teach_class(cls, classes_taught, class_name):
        if not classes_taught:
            return False
        class_keys = cls._class_keys(class_name)
        assignments = re.split(r"[,;&/\n]+", str(classes_taught).upper())
        for assignment in assignments:
            token = re.sub(r"\s+", "", assignment)
            token = re.sub(r"^FORM", "F", token)
            token = re.sub(r"[^A-Z0-9]", "", token)
            if token in class_keys or (token.startswith("F") and token[1:].isdigit()
                                       and f"F{token[1:]}" in class_keys):
                return True
        return False

    @staticmethod
    def _subject_matches_syllabus(subject_name, syllabus_name):
        subject_words = set(re.findall(r"[a-z]+", subject_name.lower()))
        syllabus_words = set(re.findall(r"[a-z]+", syllabus_name.lower()))
        ignored = {"and", "in", "the", "na", "ya"}
        subject_words -= ignored
        syllabus_words -= ignored
        return (
            subject_name.strip().lower() == syllabus_name.strip().lower()
            or subject_name.strip().lower() in syllabus_name.strip().lower()
            or syllabus_name.strip().lower() in subject_name.strip().lower()
            or bool(subject_words & syllabus_words)
        )

    @staticmethod
    def _is_vocational_class(class_name, curriculum, class_type):
        name = (class_name or "").strip().lower()
        return (
            (class_type or "").strip().lower() == "vocational"
            or (curriculum or "").strip().lower() == "vocational"
            or "amali" in name
        )

    @classmethod
    def _daily_period_limit(cls, class_name, curriculum, class_type):
        return 10 if cls._is_vocational_class(class_name, curriculum, class_type) else 9

    @staticmethod
    def _subject_allowed_for_class(subject_category, subject_track, class_name, curriculum, class_type):
        """Match subject track + category to the class.
        Track: Normal → academic only, Vocational → Amali/Vocational only, Both → either.
        Category: O-Level / A-Level / Both for academic level classes.
        """
        category = (subject_category or "O-Level").strip() or "O-Level"
        track = (subject_track or "Normal").strip() or "Normal"
        track_key = track.lower()
        vocational_class = TimetableGenerator._is_vocational_class(class_name, curriculum, class_type)

        if vocational_class:
            return track_key in {"vocational", "both"}

        if track_key == "vocational":
            return False
        # Normal and Both are allowed in academic classes.

        resolved_type = (class_type or "O-Level").strip() or "O-Level"
        form_match = re.search(r"FORM\s*(\d+)", (class_name or "").upper())
        form_number = form_match.group(1) if form_match else ""
        if resolved_type not in {"O-Level", "A-Level"} and form_number:
            resolved_type = "A-Level" if form_number in {"5", "6"} else "O-Level"

        if resolved_type == "A-Level":
            return category in {"A-Level", "Both"}
        if resolved_type == "O-Level":
            return category in {"O-Level", "Both"}
        return category in {"O-Level", "Both", "A-Level"}

    @staticmethod
    def _balanced_weekly_targets(subject_ids, total_slots, one_time_ids=None):
        """Spread weekly lessons as evenly as possible across subjects.
        one_time_ids get exactly 1 lesson/week; remaining slots are balanced.
        """
        one_time_ids = [sid for sid in (one_time_ids or []) if sid in subject_ids]
        pool = [sid for sid in subject_ids if sid not in one_time_ids]
        if not pool and one_time_ids:
            pool = list(one_time_ids)
            one_time_ids = []
        if not pool:
            return {}

        reserved = min(len(one_time_ids), total_slots)
        remaining = total_slots - reserved
        base, leftover = divmod(remaining, len(pool))
        # Prefer giving leftover slots to subjects earlier in the shuffled pool
        # so counts differ by at most 1.
        order = list(pool)
        random.shuffle(order)
        targets = {sid: base for sid in pool}
        for sid in order[:leftover]:
            targets[sid] += 1
        for sid in one_time_ids[:reserved]:
            targets[sid] = targets.get(sid, 0) + 1
        return targets

    @staticmethod
    def _build_balanced_day_plan(days, periods_per_day, targets):
        """Assign subjects to days so weekly targets are met and days stay varied."""
        remaining = {sid: count for sid, count in targets.items() if count > 0}
        plan = {}
        for day in days:
            day_list = []
            for _ in range(periods_per_day):
                fresh = [
                    sid for sid, left in remaining.items()
                    if left > 0 and sid not in day_list
                ]
                pool = fresh or [sid for sid, left in remaining.items() if left > 0]
                if not pool:
                    # Safety: refill from original targets if math drifts.
                    pool = list(targets.keys())
                pool.sort(key=lambda sid: (-remaining.get(sid, 0), random.random()))
                pick = pool[0]
                day_list.append(pick)
                remaining[pick] = remaining.get(pick, 0) - 1
            random.shuffle(day_list)
            plan[day] = day_list
        return plan

    @staticmethod
    def _normalized_subject_name(name):
        normalized = re.sub(r"[^a-z0-9]+", " ", (name or "").lower()).strip()
        aliases = {
            "litrature": "literature",
            "programing": "programming",
            "computer aplication": "computer applications",
            "business study": "business studies",
            "general study": "general studies",
            "english": "english language",
        }
        return aliases.get(normalized, normalized)

    @classmethod
    def _teacher_can_teach_subject(cls, qualifications, subject_name):
        subject = cls._normalized_subject_name(subject_name)
        qualification_names = []
        for qualification in qualifications:
            qualification_names.extend(
                value.strip()
                for value in re.split(r"[,;&+/\n]+", qualification or "")
                if value.strip()
            )
        normalized_qualifications = {
            cls._normalized_subject_name(name)
            for name in qualification_names
        }
        if subject in normalized_qualifications:
            return True
        if subject == "literature in english":
            return {"literature", "english language"}.issubset(normalized_qualifications)
        return False

    def validate_schedule(self, schedule):
        errors = []
        class_slots = set()
        teacher_slots = set()
        room_slots = set()
        weekly_subject_counts = {}
        conn = get_connection()
        try:
            cursor = conn.cursor()
            class_rows = cursor.execute(
                "SELECT id, class_name, curriculum, class_type, grade FROM classes"
            ).fetchall()
            class_info = {row[0]: row[1:] for row in class_rows}
            subject_info = {row[0]: row[1:] for row in cursor.execute("""
                SELECT id, subject_name, delivery_location, is_taught,
                       assigned_teacher_id, category, track
                FROM subjects
            """)}
            teacher_info = {row[0]: row[1:] for row in cursor.execute("""
                SELECT id, teacher_name, subject, specialization, classes_taught, status
                FROM teachers
            """)}
            room_info = {row[0]: row[1] for row in cursor.execute("""
                SELECT id, room_name FROM classrooms WHERE status != 'Unavailable'
            """)}
            requirements = {
                (row[0], row[1]): (row[2], row[3])
                for row in cursor.execute("""
                    SELECT class_id, subject_id, periods_per_week, stream_specific
                    FROM class_subject_requirements
                """)
            }
        finally:
            conn.close()

        expected_classes = set(getattr(self, "_selected_class_ids", class_info))
        actual_classes = {entry.get("class_id") for entry in schedule}
        for class_id in sorted(expected_classes - actual_classes):
            errors.append(f"{class_info.get(class_id, (f'Class {class_id}',))[0]} has no scheduled lessons.")
        for class_id in sorted(actual_classes - expected_classes):
            errors.append(f"Schedule contains an unselected or unknown class ID {class_id}.")

        for entry in schedule:
            class_id = entry["class_id"]
            teacher_id = entry.get("teacher_id")
            room_id = entry.get("classroom_id")
            subject_id = entry["subject_id"]
            day = entry["day"]
            start = entry["start_time"]
            end = entry.get("end_time")
            if class_id not in class_info:
                errors.append(f"Unknown class ID {class_id}.")
                continue
            if subject_id not in subject_info:
                errors.append(f"Unknown subject ID {subject_id}.")
                continue
            class_name, curriculum, class_type, _grade = class_info[class_id]
            subject_name, delivery_location, is_taught, designated_teacher, category, track = subject_info[subject_id]
            if not is_taught:
                errors.append(f"Untaught subject '{subject_name}' was included.")
            if (class_id, subject_id) not in requirements:
                errors.append(f"Unauthorized subject '{subject_name}' is scheduled for {class_name}.")
            elif not self._subject_allowed_for_class(
                category, track, class_name, curriculum, class_type
            ):
                errors.append(f"{subject_name} is not valid for {class_name}'s level or track.")

            slot_index = next((index for index, slot in enumerate(TIME_SLOTS)
                               if slot == (start, end)), None)
            if day not in self.days or slot_index is None:
                errors.append(f"Invalid teaching slot {day} {start}-{end} for {class_name}.")
                continue
            slot = (day, start)
            class_slot = (class_id, *slot)
            if class_slot in class_slots:
                errors.append(f"{class_name} has two lessons at {day} {start}.")
            class_slots.add(class_slot)
            weekly_key = (class_id, subject_id)
            weekly_subject_counts[weekly_key] = weekly_subject_counts.get(weekly_key, 0) + 1

            if room_id not in room_info:
                errors.append(f"{class_name} has no valid room at {day} {start}.")
            elif (room_id, *slot) in room_slots:
                errors.append(f"Room conflict at {day} {start} (room ID {room_id}).")
            else:
                room_slots.add((room_id, *slot))
            if room_id in room_info:
                location = (delivery_location or "Classroom").strip().lower()
                room_name = room_info[room_id].lower()
                if location == "classroom" and "lab" in room_name:
                    errors.append(f"{subject_name} requires a classroom, not {room_info[room_id]}.")
                elif location == "laboratory" and "lab" not in room_name:
                    errors.append(f"{subject_name} requires a laboratory, not {room_info[room_id]}.")

            if teacher_id is None:
                errors.append(f"{class_name} has no assigned teacher for {subject_name} at {day} {start}.")
                continue
            if teacher_id not in teacher_info:
                errors.append(f"Unknown teacher ID {teacher_id}.")
                continue
            if (teacher_id, *slot) in teacher_slots:
                teacher_name = teacher_info[teacher_id][0]
                errors.append(f"{teacher_name} is assigned to multiple classes at {day} {start}.")
            else:
                teacher_slots.add((teacher_id, *slot))
            _, teacher_subject, specialization, teacher_classes, status = teacher_info[teacher_id]
            if status != "Active":
                errors.append(f"Inactive teacher {teacher_info[teacher_id][0]} is scheduled.")
            if not self._teacher_can_teach_subject((teacher_subject, specialization), subject_name):
                errors.append(f"{teacher_info[teacher_id][0]} is not assigned to teach {subject_name}.")
            if not self._teacher_can_teach_class(teacher_classes, class_name):
                errors.append(f"{teacher_info[teacher_id][0]} is not assigned to {class_name}.")
            if designated_teacher is not None and teacher_id != designated_teacher:
                errors.append(f"{subject_name} was assigned to a teacher other than its designated teacher.")

        for (class_id, subject_id), (required, _stream_specific) in requirements.items():
            if class_id not in expected_classes:
                continue
            subject_name = subject_info.get(subject_id, (f"Subject {subject_id}",))[0]
            actual = weekly_subject_counts.get((class_id, subject_id), 0)
            if actual != required:
                class_name = class_info.get(class_id, (f"Class {class_id}",))[0]
                errors.append(
                    f"{class_name}: {subject_name} requires {required} periods/week; found {actual}."
                )

        for class_id in expected_classes:
            class_name, curriculum, class_type, _grade = class_info.get(
                class_id, (f"Class {class_id}", "", "", "")
            )
            daily_limit = self._daily_period_limit(class_name, curriculum, class_type)
            for day in self.days:
                count = sum((class_id, day, slot[0]) in class_slots for slot in TIME_SLOTS)
                if count > daily_limit:
                    errors.append(
                        f"{class_name} has {count} lessons on {day}; the limit is {daily_limit}."
                    )

        errors.extend(self._stream_consistency_errors(class_info, requirements, expected_classes))
        return {"errors": list(dict.fromkeys(errors)), "warnings": []}

    @staticmethod
    def _stream_consistency_errors(class_info, requirements, class_ids):
        groups = {}
        for class_id in class_ids:
            if class_id not in class_info:
                continue
            class_name, curriculum, class_type, grade = class_info[class_id]
            form_match = re.search(r"FORM\s*(\d+)", class_name.upper())
            stream_key = (grade or (form_match.group(1) if form_match else ""), curriculum, class_type)
            if stream_key[0]:
                groups.setdefault(stream_key, []).append(class_id)

        errors = []
        for stream_ids in groups.values():
            if len(stream_ids) < 2:
                continue
            subject_sets = {
                class_id: {
                    subject_id for (req_class_id, subject_id), (_count, _stream_specific)
                    in requirements.items() if req_class_id == class_id
                }
                for class_id in stream_ids
            }
            all_subjects = set().union(*subject_sets.values())
            for subject_id in all_subjects:
                present = [class_id for class_id in stream_ids if subject_id in subject_sets[class_id]]
                if len(present) == len(stream_ids):
                    continue
                explicitly_specific = any(
                    requirements.get((class_id, subject_id), (0, 0))[1]
                    for class_id in present
                )
                if not explicitly_specific:
                    names = ", ".join(class_info[class_id][0] for class_id in stream_ids)
                    errors.append(
                        f"Stream subject mismatch for subject ID {subject_id} across {names}. "
                        "Mark the configured subject as stream-specific to allow this exception."
                    )
        return errors

    def generate(self, description="", level=None):
        rules = self.parse_preferences(description)
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT id, class_name, curriculum, class_type, grade FROM classes")
        classes = cursor.fetchall()
        if level:
            classes = [row for row in classes if row[3] == level]
        cursor.execute("""
            SELECT class_id, subject_id, periods_per_week, stream_specific
            FROM class_subject_requirements
        """)
        requirements = cursor.fetchall()
        cursor.execute("""
            SELECT id, teacher_name, subject, specialization, classes_taught, status
            FROM teachers
        """)
        teachers = cursor.fetchall()
        cursor.execute("""
            SELECT id, subject_name, delivery_location, is_taught, assigned_teacher_id, category, track
            FROM subjects
        """)
        subjects = cursor.fetchall()
        cursor.execute("SELECT id, room_name FROM classrooms WHERE status != 'Unavailable'")
        classrooms = cursor.fetchall()
        conn.close()
        if not classes:
            raise RuntimeError(f"No classes found for {level or 'the selected scope'}.")
        class_ids = {row[0] for row in classes}
        self._selected_class_ids = class_ids
        class_info = {row[0]: row[1:] for row in classes}
        subject_info = {row[0]: row[1:] for row in subjects}
        teachers_by_pair = {}
        teacher_by_id = {row[0]: row for row in teachers}
        for class_id, class_name, curriculum, class_type, _grade in classes:
            for subject_id, subject_name, _location, is_taught, designated_teacher, category, track in subjects:
                if not is_taught or not self._subject_allowed_for_class(
                    category, track, class_name, curriculum, class_type
                ):
                    continue
                candidates = [teacher for teacher in teachers if teacher[5] == "Active"]
                candidates = [teacher for teacher in candidates
                              if (designated_teacher is None or teacher[0] == designated_teacher)
                              and self._teacher_can_teach_subject((teacher[2], teacher[3]), subject_name)
                              and self._teacher_can_teach_class(teacher[4], class_name)]
                teachers_by_pair[(class_id, subject_id)] = [row[0] for row in candidates]

        requirement_map = {}
        errors = []
        for class_id, subject_id, periods_per_week, stream_specific in requirements:
            if class_id in class_ids:
                requirement_map[(class_id, subject_id)] = (periods_per_week, stream_specific)
        for class_id, class_name, *_ in classes:
            class_requirements = {
                subject_id: count for (req_class_id, subject_id), (count, _specific)
                in requirement_map.items() if req_class_id == class_id
            }
            if not class_requirements:
                errors.append(f"{class_name} has no configured class-subject requirements.")
                continue
            total_required = sum(class_requirements.values())
            class_name, curriculum, class_type, _grade = class_info[class_id]
            capacity = len(self.days) * self._daily_period_limit(
                class_name, curriculum, class_type
            )
            if total_required > capacity:
                errors.append(
                    f"{class_name} requires {total_required} lessons/week, exceeding its "
                    f"weekly capacity of {capacity} teaching slots."
                )
            for subject_id, count in class_requirements.items():
                subject = subject_info.get(subject_id)
                if not subject:
                    errors.append(f"{class_name} has a requirement for missing subject ID {subject_id}.")
                    continue
                subject_name, _location, is_taught, _designated, category, track = subject
                if not is_taught or not self._subject_allowed_for_class(
                    category, track, class_name, class_info[class_id][1], class_info[class_id][2]
                ):
                    errors.append(f"{subject_name} is not configured for {class_name}.")
                if not teachers_by_pair.get((class_id, subject_id)):
                    errors.append(
                        f"No active teacher assigned to teach {subject_name} in {class_name}."
                    )
                if count > capacity:
                    errors.append(f"{class_name}: {subject_name} exceeds weekly timetable capacity.")
        errors.extend(self._stream_consistency_errors(class_info, requirement_map, class_ids))
        if not classrooms:
            errors.append("No classrooms are configured as available.")
        if errors:
            raise RuntimeError("Timetable configuration is incomplete or conflicting:\n- " + "\n- ".join(dict.fromkeys(errors)))

        room_by_subject = {}
        room_ids = [room[0] for room in classrooms]
        for subject_id, subject in subject_info.items():
            location = (subject[1] or "Classroom").strip().lower()
            suitable = [room[0] for room in classrooms if
                        location == "either"
                        or location == "classroom" and "lab" not in room[1].lower()
                        or location == "laboratory" and "lab" in room[1].lower()]
            room_by_subject[subject_id] = suitable
        for class_id, subject_id in requirement_map:
            if not room_by_subject.get(subject_id):
                class_name = class_info[class_id][0]
                subject_name = subject_info[subject_id][0]
                raise RuntimeError(
                    f"No available room matches the configured delivery location for "
                    f"{subject_name} in {class_name}."
                )

        ordered_slots = [(day, period) for day in self.days for period in self.periods]
        preferred_subjects = {name.casefold() for name in rules["preferred_subjects"]}
        avoided_subjects = {name.casefold() for name in rules["avoid_subjects"]}
        last_failure = "Teacher or room constraints prevent scheduling every required lesson."

        for _attempt in range(40):
            remaining = {key: count for key, (count, _specific) in requirement_map.items()}
            previous_subject = {}
            schedule = []
            failed = False
            current_day = None
            class_day_counts = {}
            for day, period in ordered_slots:
                if day != current_day:
                    current_day = day
                    class_day_counts = {class_id: 0 for class_id in class_ids}
                period_assignments = {}
                used_teachers = set()
                used_rooms = set()

                def assign_remaining():
                    pending_classes = [
                        class_id for class_id in class_ids - set(period_assignments)
                        if class_day_counts[class_id] < self._daily_period_limit(
                            class_info[class_id][0],
                            class_info[class_id][1],
                            class_info[class_id][2],
                        )
                        and any(
                            remaining[(class_id, subject_id)] > 0
                            for req_class_id, subject_id in requirement_map
                            if req_class_id == class_id
                        )
                    ]
                    if not pending_classes:
                        return True
                    best_class = None
                    best_options = None
                    for class_id in pending_classes:
                        options = []
                        for (req_class_id, subject_id), (count, _specific) in requirement_map.items():
                            if req_class_id != class_id or remaining[(class_id, subject_id)] <= 0:
                                continue
                            teacher_choices = [teacher_id for teacher_id in teachers_by_pair[(class_id, subject_id)]
                                               if teacher_id not in used_teachers]
                            rooms = [room_id for room_id in room_by_subject[subject_id]
                                     if room_id not in used_rooms]
                            if teacher_choices and rooms:
                                options.append((subject_id, teacher_choices, rooms))
                        if best_options is None or len(options) < len(best_options):
                            best_class, best_options = class_id, options
                    if not best_options:
                        return False
                    random.shuffle(best_options)
                    best_options.sort(key=lambda option: (
                        subject_info[option[0]][0].casefold() not in preferred_subjects,
                        subject_info[option[0]][0].casefold() in avoided_subjects,
                        -remaining[(best_class, option[0])],
                        previous_subject.get(best_class) == option[0],
                    ))
                    for subject_id, teacher_choices, rooms in best_options:
                        random.shuffle(teacher_choices)
                        random.shuffle(rooms)
                        for teacher_id in teacher_choices:
                            for room_id in rooms:
                                period_assignments[best_class] = (subject_id, teacher_id, room_id)
                                used_teachers.add(teacher_id)
                                used_rooms.add(room_id)
                                remaining[(best_class, subject_id)] -= 1
                                class_day_counts[best_class] += 1
                                if assign_remaining():
                                    return True
                                class_day_counts[best_class] -= 1
                                remaining[(best_class, subject_id)] += 1
                                used_teachers.remove(teacher_id)
                                used_rooms.remove(room_id)
                                del period_assignments[best_class]
                    return False

                if not assign_remaining():
                    last_failure = (
                        f"Teacher or room assignments cannot cover {day} period {period + 1} "
                        "without a conflict."
                    )
                    failed = True
                    break
                for class_id, (subject_id, teacher_id, room_id) in period_assignments.items():
                    start_time, end_time = TIME_SLOTS[period]
                    schedule.append({
                        "class_id": class_id,
                        "subject_id": subject_id,
                        "teacher_id": teacher_id,
                        "classroom_id": room_id,
                        "day": day,
                        "start_time": start_time,
                        "end_time": end_time,
                        "semester": self.semester,
                        "academic_year": self.academic_year,
                    })
                    previous_subject[class_id] = subject_id
            if failed:
                continue
            validation = self.validate_schedule(schedule)
            if not validation["errors"]:
                return schedule
            last_failure = "\n".join(validation["errors"][:8])
        raise RuntimeError("Unable to generate a fully valid timetable after 40 scheduling attempts:\n- " + last_failure)

    def save(self, schedule, clear_existing=True):
        if not schedule:
            return 0
        validation = self.validate_schedule(schedule)
        if validation["errors"]:
            raise ValueError("Schedule validation failed:\n" + "\n".join(validation["errors"][:12]))
        conn = get_connection()
        try:
            cursor = conn.cursor()
            if clear_existing:
                cursor.execute("DELETE FROM timetables")
            for entry in schedule:
                cursor.execute("""
                    INSERT INTO timetables
                    (class_id, subject_id, teacher_id, classroom_id,
                     day, start_time, end_time, semester, academic_year)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    entry['class_id'], entry['subject_id'],
                    entry['teacher_id'], entry['classroom_id'],
                    entry['day'], entry['start_time'], entry['end_time'],
                    entry['semester'], entry['academic_year']
                ))
            conn.commit()
            return len(schedule)
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def generate_and_save(self, clear_existing=True, description="", level=None):
        schedule = self.generate(description, level=level)
        if schedule:
            return self.save(schedule, clear_existing)
        return 0
