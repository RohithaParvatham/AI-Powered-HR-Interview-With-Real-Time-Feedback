import atexit
import json
import os
import sqlite3
import tempfile
import unittest
from contextlib import closing
from datetime import date
from io import BytesIO
from pathlib import Path

import cv2
import numpy as np
from docx import Document

test_instance = tempfile.TemporaryDirectory()
atexit.register(test_instance.cleanup)
os.environ.setdefault("INTERVIEW_INSTANCE_DIR", test_instance.name)
os.environ.setdefault("FLASK_SECRET_KEY", "test-secret-key")

from app import (
    BEHAVIORAL_QUESTION_COUNT,
    MAX_RESUME_BYTES,
    TECHNICAL_QUESTION_COUNT,
    app,
    calculate_streaks,
    concise_interview_question,
    generate_interview_questions,
    extract_resume_text,
    evaluate_answer,
    generate_resume_questions,
)
from werkzeug.security import check_password_hash


def create_docx(text):
    document = Document()
    document.add_paragraph(text)
    content = BytesIO()
    document.save(content)
    content.seek(0)
    return content


def create_pdf(text):
    stream = f"BT /F1 12 Tf 72 720 Td ({text}) Tj ET".encode("ascii")
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
        b"/Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        b"<< /Length " + str(len(stream)).encode("ascii") + b" >>\nstream\n"
        + stream
        + b"\nendstream",
    ]
    document = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for object_id, body in enumerate(objects, start=1):
        offsets.append(len(document))
        document.extend(f"{object_id} 0 obj\n".encode("ascii") + body + b"\nendobj\n")
    xref_offset = len(document)
    document.extend(b"xref\n0 6\n0000000000 65535 f \n")
    document.extend(
        b"".join(f"{offset:010d} 00000 n \n".encode("ascii") for offset in offsets[1:])
    )
    document.extend(
        f"trailer\n<< /Size 6 /Root 1 0 R >>\nstartxref\n{xref_offset}\n%%EOF\n".encode(
            "ascii"
        )
    )
    return BytesIO(document)


def create_profile_png():
    encoded, content = cv2.imencode(".png", np.zeros((32, 32, 3), dtype=np.uint8))
    if not encoded:
        raise RuntimeError("Could not create the profile picture fixture.")
    return BytesIO(content.tobytes())


class InterviewAppTests(unittest.TestCase):
    def setUp(self):
        self.database = tempfile.TemporaryDirectory()
        app.config.update(
            TESTING=True,
            DATABASE=str(Path(self.database.name) / "users.sqlite3"),
        )
        self.client = app.test_client()

    def tearDown(self):
        self.database.cleanup()

    def register_and_login(self, username="practice-user"):
        self.client.post(
            "/register",
            data={
                "full_name": "Practice User",
                "username": username,
                "password": "secure-pass-123",
                "confirm_password": "secure-pass-123",
            },
        )
        return self.client.post(
            "/login",
            data={"username": username, "password": "secure-pass-123"},
        )

    def upload_resume(self, content=None, filename="resume.docx", options=None):
        if content is None:
            content = create_docx(
                "Skills: Python, Flask, SQL, React. Built a service and a web application."
            )
        try:
            return self.client.post(
                "/resume",
                data={"resume": (content, filename), **(options or {})},
                content_type="multipart/form-data",
            )
        finally:
            content.close()

    def get_question_rows(self, interview_id):
        with closing(sqlite3.connect(app.config["DATABASE"])) as connection:
            return connection.execute(
                """SELECT id, position, category, skill, question, status
                   FROM interview_questions
                   WHERE interview_id = ? ORDER BY position""",
                (interview_id,),
            ).fetchall()

    def test_registration_persists_a_hashed_password(self):
        response = self.client.post(
            "/register",
            data={
                "full_name": "Practice User",
                "username": "practice-user",
                "password": "secure-pass-123",
                "confirm_password": "secure-pass-123",
            },
        )
        self.assertEqual(response.status_code, 302)
        with closing(sqlite3.connect(app.config["DATABASE"])) as connection:
            username, password_hash = connection.execute(
                "SELECT username, password_hash FROM users"
            ).fetchone()
        self.assertEqual(username, "practice-user")
        with closing(sqlite3.connect(app.config["DATABASE"])) as connection:
            full_name = connection.execute(
                "SELECT full_name FROM users WHERE username = ?",
                (username,),
            ).fetchone()[0]
        self.assertEqual(full_name, "Practice User")
        self.assertNotEqual(password_hash, "secure-pass-123")
        self.assertTrue(check_password_hash(password_hash, "secure-pass-123"))

    def test_existing_database_is_migrated_for_profile_and_completion_history(self):
        database_path = Path(self.database.name) / "legacy.sqlite3"
        with closing(sqlite3.connect(database_path)) as connection:
            with connection:
                connection.execute(
                    """CREATE TABLE users (
                        username TEXT PRIMARY KEY COLLATE NOCASE,
                        password_hash TEXT NOT NULL
                    )"""
                )
                connection.execute(
                    """CREATE TABLE interviews (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        username TEXT NOT NULL,
                        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                    )"""
                )
                connection.execute(
                    """CREATE TABLE interview_questions (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        interview_id INTEGER NOT NULL,
                        position INTEGER NOT NULL,
                        category TEXT NOT NULL DEFAULT 'Technical',
                        skill TEXT NOT NULL,
                        question TEXT NOT NULL,
                        sample_answer TEXT NOT NULL,
                        answer TEXT,
                        feedback TEXT,
                        status TEXT NOT NULL DEFAULT 'pending'
                    )"""
                )
                connection.execute(
                    "INSERT INTO users (username, password_hash) VALUES (?, ?)",
                    ("existing-user", "existing-hash"),
                )
                connection.execute(
                    "INSERT INTO interviews (username, created_at) VALUES (?, ?)",
                    ("existing-user", "2026-01-01 12:00:00"),
                )
                connection.execute(
                    """INSERT INTO interview_questions
                       (interview_id, position, skill, question, sample_answer, status)
                       VALUES (1, 1, 'Python', 'Question', 'Framework', 'answered')"""
                )
        app.config["DATABASE"] = str(database_path)

        self.client.get("/")

        with closing(sqlite3.connect(database_path)) as connection:
            user_columns = {
                row[1] for row in connection.execute("PRAGMA table_info(users)")
            }
            interview_columns = {
                row[1] for row in connection.execute("PRAGMA table_info(interviews)")
            }
            profile_defaults = connection.execute(
                """SELECT full_name, theme, dashboard_reminders, weekly_goal
                   FROM users WHERE username = ?""",
                ("existing-user",),
            ).fetchone()
            completion_time = connection.execute(
                "SELECT completed_at FROM interviews WHERE id = 1"
            ).fetchone()[0]
        self.assertIn("profile_picture", user_columns)
        self.assertIn("completed_at", interview_columns)
        self.assertEqual(profile_defaults, ("", "light", 1, 3))
        self.assertEqual(completion_time, "2026-01-01 12:00:00")
        self.assertTrue({"mode", "difficulty", "duration_minutes", "recording_enabled"} <= interview_columns)

    def test_login_redirects_to_dashboard_and_protected_pages_require_authentication(self):
        self.assertEqual(self.client.get("/dashboard").status_code, 302)
        self.assertEqual(self.client.get("/resume").status_code, 302)
        self.assertEqual(self.client.get("/profile").status_code, 302)
        self.assertEqual(self.client.get("/settings").status_code, 302)
        response = self.register_and_login()
        self.assertTrue(response.headers["Location"].endswith("/dashboard"))
        self.assertEqual(self.client.get("/resume").status_code, 200)
        self.assertEqual(self.client.get("/dashboard").status_code, 200)
        self.assertEqual(self.client.get("/profile").status_code, 200)
        self.assertEqual(self.client.get("/settings").status_code, 200)

    def test_dashboard_shows_profile_menu_streak_and_collapsible_history(self):
        self.register_and_login()
        response = self.upload_resume()
        interview_id = int(response.headers["Location"].rstrip("/").split("/")[-1])
        questions = self.get_question_rows(interview_id)
        for question in questions:
            result = self.client.post(
                f"/interview/{interview_id}/skip",
                data={"question_id": question[0]},
            ).get_json()

        page = self.client.get("/dashboard")
        self.assertEqual(page.status_code, 200)
        self.assertIn(b"Open profile menu", page.data)
        self.assertIn(b"Edit profile", page.data)
        self.assertIn(b"Settings", page.data)
        self.assertIn(b"Log out", page.data)
        dashboard_header = page.get_data(as_text=True).split("</header>", 1)[0]
        self.assertIn('class="header-streak"', dashboard_header)
        self.assertIn('id="streakMenuButton"', dashboard_header)
        self.assertIn("Longest streak: 1 day", dashboard_header)
        self.assertLess(
            dashboard_header.index('class="header-streak"'),
            dashboard_header.index('id="profileMenuButton"'),
        )
        self.assertIn(b'id="streakPopover"', page.data)
        self.assertIn(b"Badges", page.data)
        self.assertIn(b"Participation sessions", page.data)
        self.assertIn(b"1 session", page.data)
        self.assertIn(b"Completed at", page.data)
        self.assertNotIn("<h2>Practice streak</h2>", page.get_data(as_text=True))
        self.assertIn(b"Show history", page.data)
        self.assertIn(b'id="historyContent" hidden', page.data)
        self.assertNotIn(b"Completed interviews", page.data)
        self.assertNotIn(b"Answers submitted", page.data)
        self.assertNotIn(b"Answer rate", page.data)
        self.assertIn(b"Practice streak", page.data)
        self.assertIn("🔥".encode(), page.data)
        self.assertIn(b"View feedback", page.data)
        self.assertIn(b'aria-label="Delete interview history"', page.data)
        self.assertIn(b"Python", page.data)
        self.assertTrue(result["complete"])

    def test_all_history_items_can_be_deleted_without_affecting_other_accounts(self):
        self.register_and_login("first-user")
        interview_ids = []
        for _ in range(6):
            response = self.upload_resume()
            interview_ids.append(int(response.headers["Location"].rstrip("/").split("/")[-1]))

        page = self.client.get("/dashboard")
        self.assertEqual(page.data.count(b'aria-label="Delete interview history"'), 6)

        deleted_interview_id = interview_ids[0]
        response = self.client.post(f"/interview/{deleted_interview_id}/delete")
        self.assertEqual(response.status_code, 302)
        self.assertTrue(response.headers["Location"].endswith("/dashboard"))
        with closing(sqlite3.connect(app.config["DATABASE"])) as connection:
            deleted_interview = connection.execute(
                "SELECT id FROM interviews WHERE id = ?",
                (deleted_interview_id,),
            ).fetchone()
            deleted_questions = connection.execute(
                "SELECT id FROM interview_questions WHERE interview_id = ?",
                (deleted_interview_id,),
            ).fetchall()
            remaining_interviews = connection.execute(
                "SELECT COUNT(*) FROM interviews WHERE username = ?",
                ("first-user",),
            ).fetchone()[0]
        self.assertIsNone(deleted_interview)
        self.assertEqual(deleted_questions, [])
        self.assertEqual(remaining_interviews, 5)

        self.client.post("/logout")
        self.register_and_login("second-user")
        unauthorized = self.client.post(f"/interview/{interview_ids[1]}/delete")
        self.assertEqual(unauthorized.status_code, 404)
        with closing(sqlite3.connect(app.config["DATABASE"])) as connection:
            still_exists = connection.execute(
                "SELECT id FROM interviews WHERE id = ?",
                (interview_ids[1],),
            ).fetchone()
        self.assertIsNotNone(still_exists)

    def test_profile_and_settings_are_separate_and_save_their_own_fields(self):
        self.register_and_login()
        profile_page = self.client.get("/profile")
        self.assertIn(b"full_name", profile_page.data)
        self.assertNotIn(b'name="theme"', profile_page.data)
        settings_page = self.client.get("/settings")
        self.assertIn(b"name=\"theme\"", settings_page.data)
        self.assertIn(b"name=\"weekly_goal\"", settings_page.data)
        self.assertIn(b"Change password", settings_page.data)
        self.assertNotIn(b'name="full_name"', settings_page.data)

        profile_response = self.client.post(
            "/profile",
            data={
                "full_name": "Avery Example",
                "profile_picture": (create_profile_png(), "profile.png", "image/png"),
            },
            content_type="multipart/form-data",
        )
        self.assertEqual(profile_response.status_code, 302)
        dashboard = self.client.get("/dashboard")
        self.assertIn(b"Avery Example", dashboard.data)

        response = self.client.post(
            "/settings",
            data={
                "section": "preferences",
                "theme": "dark",
                "dashboard_reminders": "on",
            },
        )
        self.assertEqual(response.status_code, 302)
        dashboard = self.client.get("/dashboard")
        self.assertIn(b'data-theme="dark"', dashboard.data)
        self.assertIn(b"Keep your momentum!", dashboard.data)

        photo = self.client.get("/profile/photo")
        self.assertEqual(photo.status_code, 200)
        self.assertEqual(photo.mimetype, "image/jpeg")

        self.client.post(
            "/settings",
            data={"section": "preferences", "theme": "light"},
        )
        dashboard_without_reminder = self.client.get("/dashboard")
        self.assertIn(b'data-theme="light"', dashboard_without_reminder.data)
        self.assertNotIn(b"Keep your momentum!", dashboard_without_reminder.data)

    def test_account_deletion_requires_password_and_username_confirmation(self):
        self.register_and_login()
        self.upload_resume()

        wrong_password = self.client.post(
            "/settings",
            data={
                "section": "delete_account",
                "current_password": "wrong-password",
                "confirm_username": "practice-user",
                "confirm_delete": "on",
            },
        )
        self.assertEqual(wrong_password.status_code, 302)
        self.assertEqual(self.client.get("/dashboard").status_code, 200)

        wrong_username = self.client.post(
            "/settings",
            data={
                "section": "delete_account",
                "current_password": "secure-pass-123",
                "confirm_username": "different-user",
                "confirm_delete": "on",
            },
        )
        self.assertEqual(wrong_username.status_code, 302)
        self.assertEqual(self.client.get("/dashboard").status_code, 200)

        deleted = self.client.post(
            "/settings",
            data={
                "section": "delete_account",
                "current_password": "secure-pass-123",
                "confirm_username": "practice-user",
                "confirm_delete": "on",
            },
        )
        self.assertEqual(deleted.status_code, 302)
        with closing(sqlite3.connect(app.config["DATABASE"])) as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM users").fetchone()[0], 0)
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM interviews").fetchone()[0], 0)
            self.assertEqual(
                connection.execute("SELECT COUNT(*) FROM interview_questions").fetchone()[0],
                0,
            )
        self.assertEqual(self.client.get("/dashboard").status_code, 302)

    def test_settings_change_password_requires_current_password(self):
        self.register_and_login()
        wrong_password = self.client.post(
            "/settings",
            data={
                "section": "password",
                "current_password": "wrong-password",
                "new_password": "updated-pass-456",
                "confirm_password": "updated-pass-456",
            },
        )
        self.assertEqual(wrong_password.status_code, 302)
        self.assertIn(
            b"current password is incorrect",
            self.client.get("/settings").data,
        )

        changed = self.client.post(
            "/settings",
            data={
                "section": "password",
                "current_password": "secure-pass-123",
                "new_password": "updated-pass-456",
                "confirm_password": "updated-pass-456",
            },
        )
        self.assertEqual(changed.status_code, 302)
        self.client.post("/logout")
        old_login = self.client.post(
            "/login",
            data={"username": "practice-user", "password": "secure-pass-123"},
        )
        self.assertFalse(old_login.headers["Location"].endswith("/dashboard"))
        new_login = self.client.post(
            "/login",
            data={"username": "practice-user", "password": "updated-pass-456"},
        )
        self.assertTrue(new_login.headers["Location"].endswith("/dashboard"))

    def test_weekly_goal_can_be_changed_in_settings(self):
        self.register_and_login()
        response = self.client.post(
            "/settings",
            data={
                "section": "preferences",
                "theme": "light",
                "weekly_goal": "5",
                "dashboard_reminders": "on",
            },
        )
        self.assertEqual(response.status_code, 302)
        page = self.client.get("/dashboard")
        self.assertIn(b"0 of 5 interviews completed this week", page.data)

    def test_session_timeout_marks_remaining_questions_skipped(self):
        self.register_and_login()
        response = self.upload_resume()
        interview_id = int(response.headers["Location"].rstrip("/").split("/")[-1])

        finished = self.client.post(f"/interview/{interview_id}/finish").get_json()
        self.assertTrue(finished["complete"])
        self.assertEqual(finished["result_url"], f"/interview/{interview_id}/results")
        with closing(sqlite3.connect(app.config["DATABASE"])) as connection:
            pending_count = connection.execute(
                """SELECT COUNT(*) FROM interview_questions
                   WHERE interview_id = ? AND status = 'pending'""",
                (interview_id,),
            ).fetchone()[0]
            skipped_count = connection.execute(
                """SELECT COUNT(*) FROM interview_questions
                   WHERE interview_id = ? AND status = 'skipped'""",
                (interview_id,),
            ).fetchone()[0]
        self.assertEqual(pending_count, 0)
        self.assertEqual(skipped_count, 10)
        self.assertEqual(self.client.get(finished["result_url"]).status_code, 200)

    def test_upload_saves_mode_job_skill_gaps_and_recording_consent_only(self):
        self.register_and_login()
        response = self.upload_resume(
            options={
                "mode": "Technical",
                "difficulty": "Hard",
                "duration_minutes": "15",
                "job_description": "Required: Python and AWS",
                "recording_enabled": "on",
            }
        )
        interview_id = int(response.headers["Location"].rstrip("/").split("/")[-1])
        with closing(sqlite3.connect(app.config["DATABASE"])) as connection:
            settings = connection.execute(
                """SELECT mode, difficulty, duration_minutes, target_skills,
                          gap_skills, recording_enabled FROM interviews WHERE id = ?""",
                (interview_id,),
            ).fetchone()
            question_count = connection.execute(
                "SELECT COUNT(*) FROM interview_questions WHERE interview_id = ?",
                (interview_id,),
            ).fetchone()[0]
        self.assertEqual(settings[0:3], ("Technical", "Hard", 15))
        self.assertEqual(json.loads(settings[3]), ["Python", "AWS"])
        self.assertEqual(json.loads(settings[4]), ["AWS"])
        self.assertEqual(settings[5], 1)
        self.assertEqual(question_count, 5)
        page = self.client.get(response.headers["Location"])
        self.assertIn(b"data-recording-enabled=\"true\"", page.data)
        self.assertIn(b'id="recordingButton"', page.data)
        self.assertIn(b'<video id="cameraFeed"', page.data)
        self.assertNotIn(b"/video_feed", page.data)
        self.assertIn(b"Record video and audio", self.client.get("/resume").data)

    def test_opt_in_recording_has_local_playback_and_account_cleanup_controls(self):
        self.register_and_login()
        response = self.upload_resume(options={"recording_enabled": "on"})
        interview_id = int(response.headers["Location"].rstrip("/").split("/")[-1])
        for question in self.get_question_rows(interview_id):
            self.client.post(
                f"/interview/{interview_id}/skip",
                data={"question_id": question[0]},
            )

        feedback = self.client.get(f"/interview/{interview_id}/results")
        self.assertEqual(feedback.status_code, 200)
        self.assertIn(b"Private local recordings", feedback.data)
        self.assertIn(b"recordings.js", feedback.data)
        self.assertIn(b"feedback.js", feedback.data)
        settings = self.client.get("/settings")
        self.assertIn(b"Delete all my local recordings", settings.data)

    def test_streak_counts_consecutive_local_completion_days(self):
        completed_at_values = [
            "2026-01-01T12:00:00+00:00",
            "2026-01-02T12:00:00+00:00",
            "2026-01-03T12:00:00+00:00",
            "2026-01-05T12:00:00+00:00",
        ]
        self.assertEqual(
            calculate_streaks(completed_at_values, today=date(2026, 1, 5)),
            (1, 3),
        )

    def test_generated_questions_are_resume_based_and_counted(self):
        questions = generate_resume_questions("Python Flask SQL React")
        self.assertEqual(
            len(questions), TECHNICAL_QUESTION_COUNT + BEHAVIORAL_QUESTION_COUNT
        )
        technical = questions[:TECHNICAL_QUESTION_COUNT]
        self.assertEqual({question[2] for question in technical}, {"Python", "Flask", "SQL", "React"})
        self.assertTrue(all(question[1] == "Technical" for question in technical))
        self.assertTrue(all(question[1] == "Behavioral" for question in questions[TECHNICAL_QUESTION_COUNT:]))
        self.assertTrue(all(question[3] and question[4] for question in questions))

    def test_long_resume_evidence_does_not_make_interview_questions_unfocused(self):
        questions = generate_resume_questions(
            "Skills: Python, Flask\n"
            "Project: Built a Flask service with Python that automated interview scheduling "
            "for a large system with multiple users, background jobs, database integrations, "
            "and authentication requirements."
        )
        self.assertEqual(
            questions[0][3],
            "How did you use Python in a project listed on your resume?",
        )
        self.assertLess(max(len(question[3]) for question in questions[:15]), 100)
        self.assertTrue(
            all("Built a Flask service" not in question[3] for question in questions)
        )
        self.assertEqual(
            concise_interview_question(
                'Your resume mentions: "Built and deployed a long Flask service '
                'with several integrations and background jobs." '
                "How did you use Flask in a project listed on your resume?"
            ),
            "How did you use Flask in a project listed on your resume?",
        )

    def test_existing_interview_and_feedback_pages_hide_legacy_resume_quotes(self):
        self.register_and_login()
        response = self.upload_resume()
        interview_id = int(response.headers["Location"].rstrip("/").split("/")[-1])
        rows = self.get_question_rows(interview_id)
        with closing(sqlite3.connect(app.config["DATABASE"])) as connection:
            with connection:
                connection.execute(
                    """UPDATE interview_questions SET question = ?
                       WHERE id = ? AND interview_id = ?""",
                    (
                        'Your resume mentions: "Built a long Flask backend with '
                        'authentication and multiple integrations." '
                        "How did you use Flask in a project listed on your resume?",
                        rows[0][0],
                        interview_id,
                    ),
                )

        page = self.client.get(f"/interview/{interview_id}")
        self.assertIn(
            b"How did you use Flask in a project listed on your resume?",
            page.data,
        )
        self.assertNotIn(b"Built a long Flask backend", page.data)

        for question in rows:
            self.client.post(
                f"/interview/{interview_id}/skip",
                data={"question_id": question[0]},
            )
        feedback = self.client.get(f"/interview/{interview_id}/results")
        self.assertIn(
            b"How did you use Flask in a project listed on your resume?",
            feedback.data,
        )
        self.assertNotIn(b"Built a long Flask backend", feedback.data)

    def test_answer_feedback_gives_specific_improvement_prompts(self):
        feedback = evaluate_answer("Python is useful.", "Python")
        self.assertIn("context", feedback)
        self.assertIn("specific steps", feedback)
        self.assertIn("outcome", feedback)

    def test_resume_without_supported_technologies_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "could not identify supported technical skills"):
            generate_resume_questions("Experienced professional with strong teamwork skills.")

    def test_resume_upload_creates_configured_questions_without_saving_raw_text(self):
        self.register_and_login()
        response = self.upload_resume()
        self.assertEqual(response.status_code, 302)
        interview_path = response.headers["Location"]
        interview_id = int(interview_path.rstrip("/").split("/")[-1])

        page = self.client.get(interview_path)
        self.assertEqual(page.status_code, 200)
        self.assertIn(b"<h2>Camera preview</h2>", page.data)
        self.assertNotIn(b"Camera preview (optional)", page.data)
        self.assertIn(b"Practice with your camera on", page.data)
        self.assertIn(b"grid-template-columns: minmax(0, 0.9fr) minmax(0, 1.1fr)", page.data)
        self.assertIn(b"Technical interview question", page.data)
        self.assertIn(b'id="answerAudioButton"', page.data)
        self.assertIn(b'aria-label="Record audio answer"', page.data)
        self.assertIn(b'id="answerAudioPreview"', page.data)
        self.assertIn(b'id="microphoneSelect"', page.data)
        self.assertIn(b'id="answerAudioLevel"', page.data)
        self.assertIn(b"Microphone input level", page.data)
        self.assertIn(b"Audio stays in this browser", page.data)
        self.assertIn(b"recordings.js", page.data)
        self.assertNotIn(b"microphoneButton", page.data)
        self.assertIn(b'id="integrityWarning"', page.data)
        self.assertIn(b'aria-live="assertive"', page.data)
        self.assertNotIn(b'id="startRecording"', page.data)
        rows = self.get_question_rows(interview_id)
        self.assertEqual(len(rows), 30 // 3)
        self.assertEqual(sum(row[2] == "Technical" for row in rows), 8)
        self.assertEqual(sum(row[2] == "Behavioral" for row in rows), 2)
        self.assertTrue({row[3] for row in rows if row[2] == "Technical"} <= {"Python", "Flask", "SQL", "React"})
        with closing(sqlite3.connect(app.config["DATABASE"])) as connection:
            columns = {
                row[1] for row in connection.execute("PRAGMA table_info(interviews)")
            }
        self.assertNotIn("resume_text", columns)
        self.assertNotIn("job_description", columns)

    def test_answer_audio_playback_is_available_without_session_recording_opt_in(self):
        self.register_and_login()
        response = self.upload_resume()
        interview_id = int(response.headers["Location"].rstrip("/").split("/")[-1])
        for question in self.get_question_rows(interview_id):
            self.client.post(
                f"/interview/{interview_id}/skip",
                data={"question_id": question[0]},
            )

        with closing(sqlite3.connect(app.config["DATABASE"])) as connection:
            recording_enabled = connection.execute(
                "SELECT recording_enabled FROM interviews WHERE id = ?",
                (interview_id,),
            ).fetchone()[0]
        self.assertEqual(recording_enabled, 0)
        feedback = self.client.get(f"/interview/{interview_id}/results")
        self.assertIn(b"Private local recordings", feedback.data)
        self.assertIn(b"recordings.js", feedback.data)
        self.assertIn(b"feedback.js", feedback.data)

    def test_job_description_prioritizes_matching_skills_and_reports_gaps(self):
        questions, target_skills, gap_skills = generate_interview_questions(
            "Skills: Python, Flask",
            "Technical",
            "Hard",
            15,
            "Required: Python, AWS",
        )
        self.assertEqual(len(questions), 5)
        self.assertEqual(target_skills, ["Python", "AWS"])
        self.assertEqual(gap_skills, ["AWS"])
        self.assertIn("Python", questions[0][2])
        self.assertIn("How would you apply AWS in this role?", questions[1][3])
        self.assertIn("trade-offs", questions[0][3])

    def test_interview_modes_generate_the_selected_question_categories(self):
        resume_text = "Python, SQL, and Flask"
        for mode, expected_category in (
            ("Technical", "Technical"),
            ("Behavioral", "Behavioral"),
            ("HR", "HR"),
            ("Mixed", None),
        ):
            with self.subTest(mode=mode):
                questions, _, _ = generate_interview_questions(
                    resume_text, mode, "Medium", 15, ""
                )
                self.assertEqual(len(questions), 5)
                categories = {question[1] for question in questions}
                if expected_category:
                    self.assertEqual(categories, {expected_category})
                else:
                    self.assertEqual(categories, {"Technical", "Behavioral"})

        for mode in ("Behavioral", "HR"):
            questions, _, _ = generate_interview_questions(
                resume_text, mode, "Medium", 60, ""
            )
            self.assertEqual(len(questions), 20)
            self.assertEqual(len({question[3] for question in questions}), 20)

    def test_pdf_resume_text_is_extracted_and_used_for_questions(self):
        content = create_pdf("Experience with Python, Flask, SQL and REST APIs")
        resume_text = extract_resume_text("resume.pdf", content.getvalue())
        self.assertIn("Python", resume_text)
        self.register_and_login()
        response = self.upload_resume(create_pdf(resume_text), "resume.pdf")
        interview_id = int(response.headers["Location"].rstrip("/").split("/")[-1])
        rows = self.get_question_rows(interview_id)
        self.assertIn("Python", {row[3] for row in rows[:TECHNICAL_QUESTION_COUNT]})

    def test_resume_upload_rejects_unreadable_or_unsupported_files(self):
        self.register_and_login()
        invalid_pdf = self.upload_resume(BytesIO(b"not a pdf"), "resume.pdf")
        self.assertEqual(invalid_pdf.status_code, 302)
        self.assertIn(
            b"valid, text-based PDF",
            self.client.get(invalid_pdf.headers["Location"]).data,
        )
        unsupported = self.upload_resume(BytesIO(b"plain text"), "resume.txt")
        self.assertIn(
            b"Upload a PDF or DOCX",
            self.client.get(unsupported.headers["Location"]).data,
        )

    def test_large_resume_is_rejected(self):
        self.register_and_login()
        oversized = BytesIO(b"x" * (MAX_RESUME_BYTES + 1))
        response = self.upload_resume(oversized, "resume.docx")
        self.assertIn(b"5 MB or smaller", self.client.get(response.headers["Location"]).data)

    def test_answer_skip_and_final_review_include_transcript_and_skipped_frameworks(self):
        self.register_and_login()
        response = self.upload_resume()
        interview_id = int(response.headers["Location"].rstrip("/").split("/")[-1])
        questions = self.get_question_rows(interview_id)

        answer_response = self.client.post(
            f"/interview/{interview_id}/answer",
            data={
                "question_id": questions[0][0],
                "answer": (
                    "In my Python project, my task was to improve the service. I implemented "
                    "caching and testing, and the result improved response time by 20 percent."
                ),
            },
        )
        self.assertEqual(answer_response.status_code, 200)
        self.assertFalse(answer_response.get_json()["complete"])

        for question in questions[1:]:
            skip_response = self.client.post(
                f"/interview/{interview_id}/skip",
                data={"question_id": question[0]},
            )
        with closing(sqlite3.connect(app.config["DATABASE"])) as connection:
            follow_up = connection.execute(
                """SELECT question, is_follow_up FROM interview_questions
                   WHERE interview_id = ? AND is_follow_up = 1""",
                (interview_id,),
            ).fetchone()
            score, star_feedback = connection.execute(
                """SELECT score, star_feedback FROM interview_questions
                   WHERE interview_id = ? AND status = 'answered'""",
                (interview_id,),
            ).fetchone()
            pending_ids = [
                row[0] for row in connection.execute(
                    """SELECT id FROM interview_questions
                       WHERE interview_id = ? AND status = 'pending'""",
                    (interview_id,),
                )
            ]
        self.assertIsNotNone(follow_up)
        self.assertEqual(follow_up[1], 1)
        self.assertEqual(score, 100)
        self.assertIn("Situation: covered", star_feedback)
        for pending_id in pending_ids:
            skip_response = self.client.post(
                f"/interview/{interview_id}/skip",
                data={"question_id": pending_id},
            )
        result = skip_response.get_json()
        self.assertTrue(result["complete"])

        page = self.client.get(result["result_url"])
        self.assertEqual(page.status_code, 200)
        self.assertIn(b"What you said", page.data)
        self.assertIn(b"Python project", page.data)
        self.assertIn(b"Skipped", page.data)
        self.assertIn(b"truthful outcome", page.data)
        self.assertIn(b"STAR practice checklist score", page.data)
        self.assertIn(b"Follow-up", page.data)
        dashboard = self.client.get("/dashboard")
        self.assertIn(b"Progress and weekly practice plan", dashboard.data)
        self.assertIn(b"Recent answer-quality trend", dashboard.data)

    def test_users_cannot_access_another_users_interview(self):
        self.register_and_login("first-user")
        response = self.upload_resume()
        interview_id = int(response.headers["Location"].rstrip("/").split("/")[-1])
        self.client.post("/logout")
        self.register_and_login("second-user")
        self.assertEqual(self.client.get(f"/interview/{interview_id}").status_code, 404)

if __name__ == "__main__":
    unittest.main()
