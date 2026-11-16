import json
import os
import re
import secrets
import sqlite3
from contextlib import closing
from datetime import date, datetime, timedelta, timezone
from functools import wraps
from io import BytesIO
from pathlib import Path
from zipfile import BadZipFile

import cv2
import numpy as np
from docx import Document
from docx.opc.exceptions import PackageNotFoundError
from flask import (
    Flask,
    Response,
    abort,
    current_app,
    flash,
    jsonify,
    redirect,
    render_template,
    request,
    session,
    url_for,
)
from pypdf import PdfReader
from pypdf.errors import PdfReadError
from werkzeug.security import check_password_hash, generate_password_hash


app = Flask(__name__)
INSTANCE_DIR = Path(os.environ.get("INTERVIEW_INSTANCE_DIR", Path(app.root_path) / "instance"))
INSTANCE_DIR.mkdir(parents=True, exist_ok=True)
secret_key_file = INSTANCE_DIR / "flask_secret_key"
app.config.update(
    SECRET_KEY=os.environ.get("FLASK_SECRET_KEY"),
    DATABASE=os.environ.get("INTERVIEW_DATABASE", str(INSTANCE_DIR / "users.sqlite3")),
    MAX_CONTENT_LENGTH=16 * 1024 * 1024,
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
)
if app.config["SECRET_KEY"] is None:
    if not secret_key_file.exists():
        secret_key_file.write_text(secrets.token_hex(32), encoding="utf-8")
    app.config["SECRET_KEY"] = secret_key_file.read_text(encoding="utf-8").strip()

TECHNICAL_QUESTION_COUNT = 15
BEHAVIORAL_QUESTIONS = (
    (
        "Tell me about yourself.",
        "Connect your relevant experience to the role, give one or two strengths backed by examples, and finish with what you want to contribute next.",
    ),
    (
        "What are your strengths and weaknesses?",
        "Name a strength and support it with a real example. For a weakness, explain a specific step you are taking to improve.",
    ),
    (
        "Why should we hire you?",
        "Match two requirements of the role to evidence from your experience, then explain the value you could bring.",
    ),
    (
        "Describe a challenge you faced and how you overcame it.",
        "Use STAR: explain the Situation and Task, the Actions you personally took, and the Result you can truthfully support.",
    ),
    (
        "Where do you see yourself in five years?",
        "Describe a realistic direction for your growth and connect it to skills and responsibilities relevant to this role.",
    ),
)
BEHAVIORAL_QUESTION_COUNT = len(BEHAVIORAL_QUESTIONS)
HR_QUESTIONS = (
    ("Why are you interested in this role?", "Connect the role's responsibilities to your experience, interests, and realistic goals."),
    ("What kind of team helps you do your best work?", "Describe the communication and collaboration that help you contribute effectively."),
    ("How do you prioritize when several tasks are urgent?", "Explain how you clarify impact, deadlines, dependencies, and communicate trade-offs."),
    ("Tell me about a time you received difficult feedback.", "Describe the feedback, how you responded, and what you changed afterward."),
    ("What would you like to learn in your next role?", "Name a relevant skill or responsibility and a practical way you plan to develop it."),
)
BEHAVIORAL_MODE_QUESTIONS = BEHAVIORAL_QUESTIONS + (
    ("Tell me about a disagreement with a teammate.", "Explain the shared goal, how you listened and responded, and what the team decided."),
    ("Describe a mistake you made at work or in a project.", "Take responsibility, explain how you corrected it, and share what changed afterward."),
    ("Tell me about a time you took initiative.", "Describe what you noticed, the action you took, and the outcome you observed."),
    ("How have you handled an ambiguous assignment?", "Explain how you clarified the goal, identified assumptions, and checked that your approach was useful."),
    ("Describe a time you had to learn something quickly.", "Name the learning goal, the steps you took, and how you applied or verified what you learned."),
    ("Tell me about a time you persuaded others.", "Explain the concern, the evidence you shared, and how you responded to feedback."),
    ("Describe a time you supported a teammate.", "Explain what support was needed, what you did, and how it affected the shared work."),
    ("Tell me about a time you had to change your approach.", "Describe the signal that your first approach was not working and what you changed."),
    ("How did you handle a tight deadline?", "Explain how you prioritized, communicated trade-offs, and checked the result."),
    ("Describe a project that did not go as planned.", "Explain what changed, your contribution to recovery, and what you learned."),
    ("Tell me about a time you made a process better.", "Describe the friction you noticed, the change you proposed, and how you assessed its effect."),
    ("How have you handled competing priorities?", "Explain the criteria you used, who you aligned with, and what you delivered."),
    ("Describe a time you worked with someone with a different perspective.", "Explain how you understood their perspective and found a productive way forward."),
    ("Tell me about a goal you did not meet.", "Be factual about the gap, what you learned, and the next step you took."),
    ("Describe a time you helped resolve a conflict.", "Explain the underlying needs, the action you took, and the resulting agreement."),
)
HR_QUESTIONS += (
    ("What motivates you at work?", "Connect a genuine motivator to the responsibilities and outcomes of this role."),
    ("How do you prefer to receive feedback?", "Describe what makes feedback actionable and how you apply it."),
    ("What work environment helps you contribute?", "Explain the communication and collaboration practices that support your best work."),
    ("How do you stay organized?", "Describe a practical system you use and how you adjust when priorities change."),
    ("What are you looking for in your next role?", "Name responsibilities and learning opportunities that align with your experience and goals."),
    ("How do you respond to changing priorities?", "Explain how you clarify what changed, communicate impact, and update your plan."),
    ("What would you like a manager to know about working with you?", "Share a useful preference and how you remain adaptable to team needs."),
    ("How do you build trust with a new team?", "Describe specific communication and follow-through habits you use."),
    ("What does professional growth mean to you?", "Connect a realistic growth goal to actions you can take in the role."),
    ("How do you handle a demanding workload?", "Explain how you prioritize, ask for clarification, and protect quality."),
    ("What helps you communicate effectively with colleagues?", "Describe practical habits you use to share context and confirm understanding."),
    ("What do you value most in a team?", "Name a value and explain how you contribute to it through your own behavior."),
    ("How do you handle a task when expectations are unclear?", "Explain the questions you ask, the assumptions you document, and how you confirm direction."),
    ("What challenge would you like to take on next?", "Describe a relevant challenge and the skills you would use to approach it."),
    ("What questions do you have about this role?", "Ask about responsibilities, success measures, collaboration, or onboarding that would help you understand the role."),
)
INTERVIEW_MODES = ("Mixed", "Technical", "Behavioral", "HR")
DIFFICULTY_LEVELS = ("Easy", "Medium", "Hard")
DURATION_OPTIONS = (15, 30, 45, 60)
MAX_RESUME_BYTES = 5 * 1024 * 1024
MAX_PROFILE_PICTURE_BYTES = 2 * 1024 * 1024
MAX_JOB_DESCRIPTION_CHARACTERS = 10_000
MAX_RESUME_PAGES = 80
MAX_RESUME_CHARACTERS = 100_000
STREAK_BADGES = (
    (1, "🔥", "1-day streak"),
    (3, "⭐", "3-day streak"),
    (7, "🏆", "7-day streak"),
    (30, "👑", "30-day streak"),
)
TECHNOLOGIES = (
    ("Python", r"\bpython\b"),
    ("JavaScript", r"\bjavascript\b"),
    ("TypeScript", r"\btypescript\b"),
    ("Java", r"\bjava\b"),
    ("PHP", r"\bphp\b"),
    ("Ruby", r"\bruby\b"),
    ("Kotlin", r"\bkotlin\b"),
    ("Swift", r"\bswift\b"),
    ("Scala", r"\bscala\b"),
    ("C++", r"(?<!\w)c\+\+(?!\w)"),
    ("C#", r"(?<!\w)c#(?!\w)"),
    ("React", r"\breact(?:\.js)?\b"),
    ("Node.js", r"\bnode(?:\.js)?\b"),
    ("Flask", r"\bflask\b"),
    ("Django", r"\bdjango\b"),
    ("FastAPI", r"\bfastapi\b"),
    (".NET", r"(?<!\w)\.net\b"),
    ("SQL", r"\bsql\b"),
    ("PostgreSQL", r"\bpostgres(?:ql)?\b"),
    ("MySQL", r"\bmysql\b"),
    ("SQLite", r"\bsqlite\b"),
    ("Oracle", r"\boracle\b"),
    ("MongoDB", r"\bmongodb\b"),
    ("DynamoDB", r"\bdynamodb\b"),
    ("BigQuery", r"\bbigquery\b"),
    ("AWS", r"\baws\b|\bamazon web services\b"),
    ("Azure", r"\bazure\b"),
    ("Google Cloud", r"\bgcp\b|\bgoogle cloud\b"),
    ("Docker", r"\bdocker\b"),
    ("Kubernetes", r"\bkubernetes\b"),
    ("Terraform", r"\bterraform\b"),
    ("Jenkins", r"\bjenkins\b"),
    ("Git", r"\bgit\b"),
    ("REST APIs", r"\brest(?:ful)? apis?\b"),
    ("GraphQL", r"\bgraphql\b"),
    ("Machine Learning", r"\bmachine learning\b"),
    ("TensorFlow", r"\btensorflow\b"),
    ("PyTorch", r"\bpytorch\b"),
    ("scikit-learn", r"\bscikit.learn\b"),
    ("pandas", r"\bpandas\b"),
    ("NumPy", r"\bnumpy\b"),
    ("Apache Spark", r"\bapache spark\b|\bspark\b"),
    ("Kafka", r"\bkafka\b"),
    ("HTML/CSS", r"\bhtml\b|\bcss\b"),
    ("Linux", r"\blinux\b"),
    ("Redis", r"\bredis\b"),
    ("Firebase", r"\bfirebase\b"),
    ("Spring", r"\bspring(?: boot)?\b"),
    ("Angular", r"\bangular\b"),
    ("Vue", r"\bvue(?:\.js)?\b"),
    ("Power BI", r"\bpower bi\b"),
)
QUESTION_TEMPLATES = (
    "How did you use {skill} in a project listed on your resume?",
    "What problem did {skill} help you solve, and what was your contribution?",
    "Why did you choose {skill} instead of an alternative?",
    "Describe a challenging issue you handled while using {skill}.",
    "How would you test work you built with {skill}?",
    "What performance or reliability trade-offs did you consider with {skill}?",
    "How did you handle errors or edge cases in your {skill} work?",
    "How does {skill} integrate with other technologies in your resume?",
    "What security considerations matter when using {skill}?",
    "Explain a key design decision in your {skill} project.",
    "What would you improve if you revisited the {skill} work?",
    "How did you measure the result of your {skill} work?",
    "How would you scale the {skill} solution for more users or data?",
    "What did you learn while using {skill}, and how did you apply it?",
    "How would you debug a failure in a system using {skill}?",
)
SAMPLE_ANSWER_TEMPLATES = (
    "In [project], I used {skill} for [goal]. My contribution was [specific actions], and the result was [truthful outcome or measure].",
    "The problem was [problem] under [constraint]. I used {skill} to [approach], handled [challenge], and achieved [result].",
    "I chose {skill} because [requirement]. I considered [alternative], compared [trade-offs], and chose this approach because [reason].",
    "I found [symptom] while using {skill}. I reproduced it, investigated [evidence], fixed it by [action], and verified [result].",
    "For {skill}, I would test [expected behavior], [edge cases], and [failure cases] using [tools or test types]. I would know it worked when [criteria].",
    "I noticed [performance or reliability concern] in the {skill} work. I measured [baseline], changed [approach], and checked the result with [measure].",
    "One edge case was [case]. I handled it in {skill} by [validation or error handling], and tested that [expected safe behavior].",
    "The {skill} component connected to [other system] through [interface or data flow]. I owned [part] and verified the integration by [test].",
    "For the {skill} feature, I protected [data or operation] with [specific control]. I also considered [risk] and verified [result].",
    "I chose [design] for the {skill} project because [requirement]. I considered [alternative], accepted [trade-off], and validated it by [method].",
    "If I revisited the {skill} work, I would improve [specific part]. I would prioritize it because [evidence] and measure success by [metric].",
    "I measured the {skill} work using [baseline and metric]. After [action], the result was [verified outcome], measured over [context].",
    "To scale the {skill} solution, I would first address [bottleneck], then consider [design change]. I would validate it with [load or reliability measure].",
    "While learning {skill}, I needed to understand [concept]. I applied it to [task], checked my understanding with [evidence], and learned [lesson].",
    "If a {skill} system failed, I would first check [signals], narrow down [component], test [hypothesis], and confirm the fix with [verification].",
)


def initialize_database():
    database_path = Path(current_app.config["DATABASE"])
    database_path.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(database_path)) as connection:
        with connection:
            connection.execute(
                """CREATE TABLE IF NOT EXISTS users (
                    username TEXT PRIMARY KEY COLLATE NOCASE,
                    password_hash TEXT NOT NULL
                )"""
            )
            user_columns = {
                row[1] for row in connection.execute("PRAGMA table_info(users)")
            }
            user_migrations = (
                ("full_name", "TEXT NOT NULL DEFAULT ''"),
                ("profile_picture", "BLOB"),
                ("theme", "TEXT NOT NULL DEFAULT 'light'"),
                ("dashboard_reminders", "INTEGER NOT NULL DEFAULT 1"),
                ("weekly_goal", "INTEGER NOT NULL DEFAULT 3"),
            )
            for column, declaration in user_migrations:
                if column not in user_columns:
                    connection.execute(
                        f"ALTER TABLE users ADD COLUMN {column} {declaration}"
                    )
            connection.execute(
                """CREATE TABLE IF NOT EXISTS interviews (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    username TEXT NOT NULL REFERENCES users(username),
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    completed_at TEXT,
                    mode TEXT NOT NULL DEFAULT 'Mixed',
                    difficulty TEXT NOT NULL DEFAULT 'Medium',
                    duration_minutes INTEGER NOT NULL DEFAULT 30,
                    target_skills TEXT NOT NULL DEFAULT '[]',
                    gap_skills TEXT NOT NULL DEFAULT '[]',
                    recording_enabled INTEGER NOT NULL DEFAULT 0
                )"""
            )
            interview_columns = {
                row[1] for row in connection.execute("PRAGMA table_info(interviews)")
            }
            interview_migrations = (
                ("completed_at", "TEXT"),
                ("mode", "TEXT NOT NULL DEFAULT 'Mixed'"),
                ("difficulty", "TEXT NOT NULL DEFAULT 'Medium'"),
                ("duration_minutes", "INTEGER NOT NULL DEFAULT 30"),
                ("target_skills", "TEXT NOT NULL DEFAULT '[]'"),
                ("gap_skills", "TEXT NOT NULL DEFAULT '[]'"),
                ("recording_enabled", "INTEGER NOT NULL DEFAULT 0"),
            )
            for column, declaration in interview_migrations:
                if column not in interview_columns:
                    connection.execute(
                        f"ALTER TABLE interviews ADD COLUMN {column} {declaration}"
                    )
            connection.execute(
                """CREATE TABLE IF NOT EXISTS interview_questions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    interview_id INTEGER NOT NULL REFERENCES interviews(id) ON DELETE CASCADE,
                    position INTEGER NOT NULL,
                    category TEXT NOT NULL DEFAULT 'Technical',
                    skill TEXT NOT NULL,
                    question TEXT NOT NULL,
                    sample_answer TEXT NOT NULL,
                    answer TEXT,
                    feedback TEXT,
                    score INTEGER,
                    star_feedback TEXT,
                    is_follow_up INTEGER NOT NULL DEFAULT 0,
                    status TEXT NOT NULL DEFAULT 'pending'
                        CHECK (status IN ('pending', 'answered', 'skipped')),
                    UNIQUE (interview_id, position)
                )"""
            )
            question_columns = {
                row[1] for row in connection.execute("PRAGMA table_info(interview_questions)")
            }
            question_migrations = (
                ("category", "TEXT NOT NULL DEFAULT 'Technical'"),
                ("score", "INTEGER"),
                ("star_feedback", "TEXT"),
                ("is_follow_up", "INTEGER NOT NULL DEFAULT 0"),
            )
            for column, declaration in question_migrations:
                if column not in question_columns:
                    connection.execute(
                        f"ALTER TABLE interview_questions ADD COLUMN {column} {declaration}"
                    )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_interviews_username_completed ON interviews(username, completed_at)"
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_questions_interview_status ON interview_questions(interview_id, status, position)"
            )
            connection.execute(
                """UPDATE interviews SET completed_at = created_at
                   WHERE completed_at IS NULL
                   AND EXISTS (
                       SELECT 1 FROM interview_questions
                       WHERE interview_questions.interview_id = interviews.id
                   )
                   AND NOT EXISTS (
                       SELECT 1 FROM interview_questions
                       WHERE interview_questions.interview_id = interviews.id
                         AND interview_questions.status = 'pending'
                   )"""
            )


@app.before_request
def ensure_database():
    initialize_database()


@app.context_processor
def inject_theme():
    theme = "light"
    username = session.get("username")
    if username:
        with closing(sqlite3.connect(current_app.config["DATABASE"])) as connection:
            row = connection.execute(
                "SELECT theme FROM users WHERE username = ?",
                (username,),
            ).fetchone()
        if row and row[0] in {"light", "dark"}:
            theme = row[0]
    return {"theme": theme}


def login_required(view):
    @wraps(view)
    def wrapped_view(*args, **kwargs):
        if "username" not in session:
            return redirect(url_for("home"))
        return view(*args, **kwargs)

    return wrapped_view


def normalize_profile_picture(content):
    try:
        image = cv2.imdecode(np.frombuffer(content, dtype=np.uint8), cv2.IMREAD_COLOR)
    except cv2.error as error:
        raise ValueError("Choose a valid JPG, PNG, or WebP image.") from error
    if image is None:
        raise ValueError("Choose a valid JPG, PNG, or WebP image.")

    height, width = image.shape[:2]
    if width > 4096 or height > 4096 or width * height > 16_000_000:
        raise ValueError("Profile pictures must be no larger than 16 megapixels.")
    scale = min(1, 512 / max(width, height))
    if scale < 1:
        image = cv2.resize(
            image,
            (round(width * scale), round(height * scale)),
            interpolation=cv2.INTER_AREA,
        )
    try:
        encoded, content = cv2.imencode(
            ".jpg",
            image,
            [cv2.IMWRITE_JPEG_QUALITY, 85],
        )
    except cv2.error as error:
        raise ValueError("We could not prepare that profile picture.") from error
    if not encoded:
        raise ValueError("We could not prepare that profile picture.")
    return content.tobytes()


def calculate_streaks(completed_at_values, today: date | None = None):
    completed_days = set()
    for value in completed_at_values:
        completed_at = datetime.fromisoformat(value)
        if completed_at.tzinfo is None:
            completed_at = completed_at.replace(tzinfo=timezone.utc)
        completed_days.add(completed_at.astimezone().date())

    today = today or datetime.now().astimezone().date()
    if today in completed_days:
        current_day = today
    elif today - timedelta(days=1) in completed_days:
        current_day = today - timedelta(days=1)
    else:
        current_day = None

    current_streak = 0
    while current_day is not None and current_day in completed_days:
        current_streak += 1
        current_day -= timedelta(days=1)

    longest_streak = 0
    run_length = 0
    previous_day = None
    for completed_day in sorted(completed_days):
        run_length = run_length + 1 if previous_day and completed_day == previous_day + timedelta(days=1) else 1
        longest_streak = max(longest_streak, run_length)
        previous_day = completed_day
    return current_streak, longest_streak


def format_local_datetime(value):
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone().strftime("%b %d, %Y at %I:%M %p")


def extract_resume_text(filename, content):
    suffix = Path(filename).suffix.lower()
    try:
        if suffix == ".pdf":
            with BytesIO(content) as resume_stream:
                reader = PdfReader(resume_stream, strict=False)
                if reader.is_encrypted:
                    raise ValueError("This PDF is password-protected. Upload an unlocked copy.")
                if len(reader.pages) > MAX_RESUME_PAGES:
                    raise ValueError(f"PDFs must have {MAX_RESUME_PAGES} pages or fewer.")
                text = "\n".join(page.extract_text() or "" for page in reader.pages)
        elif suffix == ".docx":
            with BytesIO(content) as resume_stream:
                document = Document(resume_stream)
                text_parts = [paragraph.text for paragraph in document.paragraphs]
                for table in document.tables:
                    text_parts.extend(
                        cell.text
                        for row in table.rows
                        for cell in row.cells
                    )
                text = "\n".join(text_parts)
        else:
            raise ValueError("Upload a PDF or DOCX resume.")
    except PdfReadError as error:
        raise ValueError("We could not read that PDF. Check that it is a valid, text-based PDF.") from error
    except (BadZipFile, PackageNotFoundError) as error:
        raise ValueError("We could not read that DOCX file. Please upload a valid Word document.") from error

    text = text[:MAX_RESUME_CHARACTERS].strip()
    if not text:
        raise ValueError("No readable text was found. Scanned-image PDFs are not supported yet.")
    return text


def generate_resume_questions(resume_text):
    skills = extract_supported_skills(resume_text)
    if not skills:
        raise ValueError(
            "We could not identify supported technical skills. Add a readable skills or technologies section and upload again."
        )

    questions = []
    for position in range(TECHNICAL_QUESTION_COUNT):
        skill = skills[position % len(skills)]
        template_index = (position // len(skills)) % len(QUESTION_TEMPLATES)
        question_template = QUESTION_TEMPLATES[template_index].format(skill=skill)
        sample_answer = SAMPLE_ANSWER_TEMPLATES[template_index].format(skill=skill)
        questions.append(
            (position + 1, "Technical", skill, question_template, sample_answer)
        )

    next_position = len(questions) + 1
    for offset, (question, sample_answer) in enumerate(BEHAVIORAL_QUESTIONS):
        questions.append(
            (next_position + offset, "Behavioral", "General", question, sample_answer)
        )
    return questions


def extract_supported_skills(text):
    return [
        name
        for name, pattern in TECHNOLOGIES
        if re.search(pattern, text, re.IGNORECASE)
    ]


def generate_interview_questions(resume_text, mode, difficulty, duration_minutes, job_description):
    if mode not in INTERVIEW_MODES:
        raise ValueError("Choose a valid interview mode.")
    if difficulty not in DIFFICULTY_LEVELS:
        raise ValueError("Choose a valid difficulty level.")
    if duration_minutes not in DURATION_OPTIONS:
        raise ValueError("Choose a valid practice duration.")

    resume_skills = extract_supported_skills(resume_text)
    job_skills = extract_supported_skills(job_description)
    target_skills = job_skills if job_description else resume_skills
    gap_skills = [skill for skill in job_skills if skill not in resume_skills]
    technical_skills = job_skills + [
        skill for skill in resume_skills if skill not in job_skills
    ]
    question_count = duration_minutes // 3
    if mode in {"Mixed", "Technical"} and not technical_skills:
        raise ValueError(
            "Technical questions need a supported technology in your resume or job description."
        )

    question_specs = []
    technical_count = (
        max(1, round(question_count * 0.75))
        if mode == "Mixed"
        else question_count
        if mode == "Technical"
        else 0
    )
    if technical_count:
        skills = technical_skills or prioritized_skills
        for index in range(technical_count):
            skill = skills[index % len(skills)]
            template_index = (index // len(skills)) % len(QUESTION_TEMPLATES)
            template = QUESTION_TEMPLATES[template_index].format(skill=skill)
            question = (
                f"How would you apply {skill} in this role?"
                if skill in job_skills and skill not in resume_skills
                else template
            )
            sample_answer = SAMPLE_ANSWER_TEMPLATES[template_index].format(skill=skill)
            question_specs.append(("Technical", skill, question, sample_answer))

    other_count = question_count - technical_count
    if mode == "Behavioral":
        question_pool = BEHAVIORAL_MODE_QUESTIONS
        category = "Behavioral"
    elif mode == "HR":
        question_pool = HR_QUESTIONS
        category = "HR"
    elif mode == "Mixed":
        question_pool = BEHAVIORAL_QUESTIONS
        category = "Behavioral"
    else:
        question_pool = ()
        category = ""
    for index in range(other_count):
        question, sample_answer = question_pool[index % len(question_pool)]
        question_specs.append((category, "General", question, sample_answer))

    if difficulty == "Easy":
        question_specs = [
            (category, skill, f"Start with a simple example. {question}", sample)
            for category, skill, question, sample in question_specs
        ]
    elif difficulty == "Hard":
        question_specs = [
            (
                category,
                skill,
                f"{question} Include constraints, trade-offs, and how you would verify the outcome.",
                sample,
            )
            for category, skill, question, sample in question_specs
        ]

    questions = [
        (position, category, skill, question, sample, 0, None, None)
        for position, (category, skill, question, sample) in enumerate(question_specs, start=1)
    ]
    return questions, target_skills, gap_skills


def concise_interview_question(question):
    job_gap = re.match(
        r"^The job description lists (.+?), which was not identified in your resume\."
        r"\s+How would you approach using it in this role\?(.*)$",
        question,
    )
    if job_gap:
        return f"How would you apply {job_gap.group(1)} in this role?{job_gap.group(2)}"

    resume_excerpt = re.match(
        r'^Your resume mentions: ".*"\s+((?:How|What|Why|Describe|Explain)\b.*)$',
        question,
    )
    if resume_excerpt:
        return resume_excerpt.group(1)

    resume_skill = re.match(r"^Your resume lists [^.]+\.\s+(.+)$", question)
    if resume_skill:
        return resume_skill.group(1)
    return question


def evaluate_answer(answer, skill):
    word_count = len(re.findall(r"\b[\w'-]+\b", answer))
    includes_action = bool(
        re.search(
            r"\b(built|developed|implemented|designed|created|led|"
            r"tested|fixed|analyzed|automated|improved|configured|"
            r"optimized|integrated|used|owned|delivered)\b",
            answer,
            re.I,
        )
    )
    includes_result = bool(
        re.search(r"\b(result|improv|reduc|increas|improv|percent|users|faster|saved)\w*\b|\d+%", answer, re.I)
    )
    suggestions = []
    if word_count < 25:
        suggestions.append("Add context about the problem and what you personally did.")
    if skill != "General" and skill.lower() not in answer.lower():
        suggestions.append(f"Connect your example directly to {skill}.")
    if not includes_action:
        suggestions.append("Describe the specific steps you took, using clear action verbs.")
    if not includes_result:
        suggestions.append("Explain the outcome; include a truthful measure if you have one.")
    if skill == "General" and not re.search(r"\b(I|my|we)\b", answer, re.I):
        suggestions.append("Make your own actions clear rather than describing only what the team did.")
    if not suggestions:
        return "Good structure: you described an action and outcome. Keep the example specific and accurate."
    return "To strengthen your answer: " + " ".join(suggestions)


def assess_star_answer(answer):
    cues = {
        "Situation": r"\b(when|while|during|in my|at my|the project|the team|the system)\b",
        "Task": r"\b(task|responsib\w*|goal|needed to|challenge|asked to|expected to)\b",
        "Action": r"\b(built|developed|implemented|designed|created|led|tested|fixed|"
        r"analyzed|automated|improved|configured|optimized|integrated|used|delivered)\b",
        "Result": r"\b(result|improv\w*|reduc\w*|increas\w*|saved|faster|users)\b|\d+%",
    }
    components = {
        component: bool(re.search(pattern, answer, re.IGNORECASE))
        for component, pattern in cues.items()
    }
    score = 25 * sum(components.values())
    if len(re.findall(r"\b[\w'-]+\b", answer)) < 25:
        score = max(0, score - 10)
    checklist = " · ".join(
        f"{component}: {'covered' if included else 'add detail'}"
        for component, included in components.items()
    )
    return score, checklist, components


def build_follow_up(skill, missing_components):
    if "Result" in missing_components:
        prompt = "What result followed your actions, and how did you verify or measure it?"
    elif "Action" in missing_components:
        prompt = "Which specific steps did you personally take to address the situation?"
    elif "Situation" in missing_components or "Task" in missing_components:
        prompt = "What was the situation, and what were you personally responsible for?"
    else:
        prompt = "What was the biggest challenge, and what would you improve if you did it again?"
    focus = f" while working with {skill}" if skill != "General" else ""
    return f"Follow-up{focus}: {prompt}"


def insert_follow_up(interview_id, question, missing_components):
    if question[9]:
        return
    position = question[1]
    follow_up = build_follow_up(question[2], missing_components)
    sample_answer = (
        "Add a truthful detail that answers the follow-up, explains your own contribution, "
        "and includes a verifiable result where possible."
    )
    with closing(sqlite3.connect(app.config["DATABASE"])) as connection:
        with connection:
            later_questions = connection.execute(
                """SELECT id, position FROM interview_questions
                   WHERE interview_id = ? AND position > ? ORDER BY position DESC""",
                (interview_id, position),
            ).fetchall()
            for question_id, later_position in later_questions:
                connection.execute(
                    "UPDATE interview_questions SET position = ? WHERE id = ?",
                    (later_position + 1, question_id),
                )
            connection.execute(
                """INSERT INTO interview_questions
                   (interview_id, position, category, skill, question, sample_answer, is_follow_up)
                   VALUES (?, ?, ?, ?, ?, ?, 1)""",
                (
                    interview_id,
                    position + 1,
                    question[8],
                    question[2],
                    follow_up,
                    sample_answer,
                ),
            )


def owned_interview(interview_id):
    with closing(sqlite3.connect(app.config["DATABASE"])) as connection:
        interview = connection.execute(
            """SELECT id, created_at, mode, difficulty, duration_minutes, target_skills,
                      gap_skills, recording_enabled
               FROM interviews WHERE id = ? AND username = ?""",
            (interview_id, session["username"]),
        ).fetchone()
    if interview is None:
        abort(404)
    return interview


def get_question(interview_id, question_id):
    with closing(sqlite3.connect(app.config["DATABASE"])) as connection:
        question = connection.execute(
            """SELECT id, position, skill, question, sample_answer, answer, feedback,
                      status, category, is_follow_up
               FROM interview_questions WHERE id = ? AND interview_id = ?""",
            (question_id, interview_id),
        ).fetchone()
    if question is None:
        abort(404)
    return question


def advance_response(interview_id):
    with closing(sqlite3.connect(app.config["DATABASE"])) as connection:
        next_question = connection.execute(
            """SELECT id, position, question FROM interview_questions
               WHERE interview_id = ? AND status = 'pending'
               ORDER BY position LIMIT 1""",
            (interview_id,),
        ).fetchone()
    if next_question is None:
        with closing(sqlite3.connect(app.config["DATABASE"])) as connection:
            with connection:
                connection.execute(
                    """UPDATE interviews SET completed_at = COALESCE(completed_at, CURRENT_TIMESTAMP)
                       WHERE id = ?""",
                    (interview_id,),
                )
        return {"complete": True, "result_url": url_for("interview_results", interview_id=interview_id)}
    return {
        "complete": False,
        "next_url": url_for("interview_page", interview_id=interview_id),
        "next_question": {
            "id": next_question[0],
            "position": next_question[1],
            "question": next_question[2],
        },
    }


def save_answer(interview_id, question_id, answer):
    question = get_question(interview_id, question_id)
    if question[7] != "pending":
        return None, (jsonify({"error": "This question has already been completed."}), 409)

    score, star_checklist, components = assess_star_answer(answer)
    feedback = (
        f"{evaluate_answer(answer, question[2])} "
        f"STAR practice checklist: {star_checklist}."
    )
    with closing(sqlite3.connect(app.config["DATABASE"])) as connection:
        with connection:
            connection.execute(
                """UPDATE interview_questions
                   SET answer = ?, feedback = ?, score = ?, star_feedback = ?, status = 'answered'
                   WHERE id = ? AND interview_id = ? AND status = 'pending'""",
                (answer, feedback, score, star_checklist, question_id, interview_id),
            )
    insert_follow_up(
        interview_id,
        question,
        [component for component, included in components.items() if not included],
    )
    return {"feedback": feedback, **advance_response(interview_id)}, None


@app.route("/")
def home():
    if "username" in session:
        return redirect(url_for("dashboard"))
    return render_template("login.html")


@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        full_name = request.form.get("full_name", "").strip()
        password = request.form.get("password", "")
        confirm_password = request.form.get("confirm_password", "")

        if not username or len(username) > 80:
            flash("Enter a username of up to 80 characters.", "error")
            return redirect(url_for("register"))
        if not full_name or len(full_name) > 120:
            flash("Enter a full name of up to 120 characters.", "error")
            return redirect(url_for("register"))
        if len(password) < 8:
            flash("Use a password with at least 8 characters.", "error")
            return redirect(url_for("register"))
        if password != confirm_password:
            flash("Passwords do not match.", "error")
            return redirect(url_for("register"))

        try:
            with closing(sqlite3.connect(app.config["DATABASE"])) as connection:
                with connection:
                    connection.execute(
                        "INSERT INTO users (username, password_hash, full_name) VALUES (?, ?, ?)",
                        (username, generate_password_hash(password), full_name),
                    )
        except sqlite3.IntegrityError:
            flash("That username is already registered.", "error")
            return redirect(url_for("register"))

        flash("Account created. You can now log in.", "success")
        return redirect(url_for("home"))

    return render_template("register.html")


@app.route("/login", methods=["POST"])
def login():
    username = request.form.get("username", "").strip()
    password = request.form.get("password", "")
    with closing(sqlite3.connect(app.config["DATABASE"])) as connection:
        user = connection.execute(
            "SELECT username, password_hash FROM users WHERE username = ? COLLATE NOCASE",
            (username,),
        ).fetchone()

    if user is None or not check_password_hash(user[1], password):
        flash("Invalid username or password.", "error")
        return redirect(url_for("home"))

    session.clear()
    session["username"] = user[0]
    return redirect(url_for("dashboard"))


@app.route("/dashboard")
@login_required
def dashboard():
    username = session["username"]
    with closing(sqlite3.connect(app.config["DATABASE"])) as connection:
        connection.row_factory = sqlite3.Row
        user = connection.execute(
            """SELECT username, full_name, theme, dashboard_reminders, weekly_goal,
                      profile_picture IS NOT NULL AS has_profile_picture
               FROM users WHERE username = ?""",
            (username,),
        ).fetchone()
        if user is None:
            abort(404)
        interview_rows = connection.execute(
            """SELECT i.id, i.created_at, i.completed_at,
                      COUNT(q.id) AS question_count,
                      SUM(CASE WHEN q.status = 'answered' THEN 1 ELSE 0 END) AS answered_count,
                      SUM(CASE WHEN q.status = 'skipped' THEN 1 ELSE 0 END) AS skipped_count,
                      GROUP_CONCAT(DISTINCT CASE
                          WHEN q.category = 'Technical' THEN q.skill END) AS skills
               FROM interviews i
               LEFT JOIN interview_questions q ON q.interview_id = i.id
               WHERE i.username = ?
               GROUP BY i.id
               ORDER BY i.created_at DESC""",
            (username,),
        ).fetchall()
        focus_rows = connection.execute(
            """SELECT q.skill, AVG(q.score) AS average_score, COUNT(q.score) AS scored_answers
               FROM interview_questions q
               JOIN interviews i ON i.id = q.interview_id
               WHERE i.username = ? AND q.status = 'answered'
                 AND q.score IS NOT NULL AND q.skill != 'General'
               GROUP BY q.skill
               ORDER BY average_score ASC, scored_answers DESC
               LIMIT 3""",
            (username,),
        ).fetchall()
        trend_rows = connection.execute(
            """SELECT i.id, i.completed_at, i.mode, AVG(q.score) AS average_score
               FROM interviews i
               JOIN interview_questions q ON q.interview_id = i.id
               WHERE i.username = ? AND i.completed_at IS NOT NULL AND q.score IS NOT NULL
               GROUP BY i.id
               ORDER BY i.completed_at DESC
               LIMIT 8""",
            (username,),
        ).fetchall()
        week_start = (
            datetime.now(timezone.utc) - timedelta(days=7)
        ).strftime("%Y-%m-%d %H:%M:%S")
        weekly_completed = connection.execute(
            """SELECT COUNT(*) FROM interviews
               WHERE username = ? AND completed_at >= ?""",
            (username, week_start),
        ).fetchone()[0]

    history = []
    completed_at_values = []
    streak_activity_by_date = {}
    completed_count = 0
    answered_count = 0
    skipped_count = 0
    for row in interview_rows:
        complete = row["completed_at"] is not None
        if complete:
            completed_count += 1
            completed_at = datetime.fromisoformat(row["completed_at"])
            if completed_at.tzinfo is None:
                completed_at = completed_at.replace(tzinfo=timezone.utc)
            completed_at_values.append(row["completed_at"])
            local_completed_at = completed_at.astimezone()
            date_key = local_completed_at.date().isoformat()
            activity = streak_activity_by_date.setdefault(
                date_key,
                {
                    "date_key": date_key,
                    "date": local_completed_at.strftime("%b %d, %Y"),
                    "session_times": [],
                },
            )
            activity["session_times"].append(
                local_completed_at.strftime("%I:%M %p").lstrip("0")
            )
        answered_count += row["answered_count"] or 0
        skipped_count += row["skipped_count"] or 0
        history.append(
            {
                "id": row["id"],
                "started_at": format_local_datetime(row["created_at"]),
                "complete": complete,
                "answered_count": row["answered_count"] or 0,
                "skipped_count": row["skipped_count"] or 0,
                "question_count": row["question_count"] or 0,
                "skills": sorted(filter(None, (row["skills"] or "").split(","))),
                "url": url_for(
                    "interview_results" if complete else "interview_page",
                    interview_id=row["id"],
                ),
            }
        )

    current_streak, best_streak = calculate_streaks(completed_at_values)
    streak_activity = sorted(
        streak_activity_by_date.values(),
        key=lambda activity: activity["date_key"],
        reverse=True,
    )
    practice_trend = [
        {
            "label": f"Interview {row['id']} · {row['mode']}",
            "score": round(row["average_score"]),
            "completed_at": format_local_datetime(row["completed_at"]),
        }
        for row in reversed(trend_rows)
    ]
    focus_skills = [
        {
            "skill": row["skill"],
            "score": round(row["average_score"]),
            "answers": row["scored_answers"],
        }
        for row in focus_rows
    ]
    next_practice = (
        f"Practice {focus_skills[0]['skill']} in a Technical interview and include "
        "your actions and a truthful result."
        if focus_skills
        else "Complete an interview to get a personalized skill focus."
    )
    completed_today = any(
        datetime.fromisoformat(value).replace(tzinfo=timezone.utc).astimezone().date()
        == datetime.now().astimezone().date()
        for value in completed_at_values
    )
    completed_question_count = answered_count + skipped_count
    answer_rate = (
        round(100 * answered_count / completed_question_count)
        if completed_question_count
        else 0
    )
    return render_template(
        "dashboard.html",
        user=user,
        display_name=user["full_name"].strip() or username,
        history=history,
        completed_count=completed_count,
        answered_count=answered_count,
        skipped_count=skipped_count,
        answer_rate=answer_rate,
        current_streak=current_streak,
        best_streak=best_streak,
        badges=STREAK_BADGES,
        streak_activity=streak_activity,
        completed_today=completed_today,
        weekly_goal=user["weekly_goal"],
        weekly_completed=weekly_completed,
        focus_skills=focus_skills,
        practice_trend=practice_trend,
        next_practice=next_practice,
    )


@app.route("/settings", methods=["GET", "POST"])
@login_required
def settings():
    username = session["username"]
    with closing(sqlite3.connect(app.config["DATABASE"])) as connection:
        connection.row_factory = sqlite3.Row
        user = connection.execute(
            """SELECT username, password_hash, theme, dashboard_reminders, weekly_goal
               FROM users WHERE username = ?""",
            (username,),
        ).fetchone()
    if user is None:
        abort(404)

    if request.method == "POST":
        section = request.form.get("section")
        if section == "preferences":
            theme = request.form.get("theme", "")
            reminders_enabled = int(request.form.get("dashboard_reminders") == "on")
            weekly_goal = request.form.get(
                "weekly_goal",
                default=user["weekly_goal"],
                type=int,
            )
            if theme not in {"light", "dark"}:
                flash("Choose a valid appearance theme.", "error")
                return redirect(url_for("settings"))
            if weekly_goal is None or not 1 <= weekly_goal <= 20:
                flash("Set a weekly interview goal between 1 and 20.", "error")
                return redirect(url_for("settings"))

            with closing(sqlite3.connect(app.config["DATABASE"])) as connection:
                with connection:
                    connection.execute(
                        """UPDATE users SET theme = ?, dashboard_reminders = ?, weekly_goal = ?
                           WHERE username = ?""",
                        (theme, reminders_enabled, weekly_goal, username),
                    )
            flash("Settings saved.", "success")
            return redirect(url_for("settings"))

        if section == "password":
            current_password = request.form.get("current_password", "")
            new_password = request.form.get("new_password", "")
            confirm_password = request.form.get("confirm_password", "")
            if not check_password_hash(user["password_hash"], current_password):
                flash("Your current password is incorrect.", "error")
                return redirect(url_for("settings"))
            if len(new_password) < 8:
                flash("Use a new password with at least 8 characters.", "error")
                return redirect(url_for("settings"))
            if new_password != confirm_password:
                flash("The new passwords do not match.", "error")
                return redirect(url_for("settings"))
            with closing(sqlite3.connect(app.config["DATABASE"])) as connection:
                with connection:
                    connection.execute(
                        "UPDATE users SET password_hash = ? WHERE username = ?",
                        (generate_password_hash(new_password), username),
                    )
            flash("Password changed.", "success")
            return redirect(url_for("settings"))

        if section == "delete_account":
            current_password = request.form.get("current_password", "")
            confirmed_username = request.form.get("confirm_username", "").strip()
            confirmed = request.form.get("confirm_delete") == "on"
            if not check_password_hash(user["password_hash"], current_password):
                flash("Your current password is incorrect.", "error")
                return redirect(url_for("settings"))
            if confirmed_username != username or not confirmed:
                flash("Type your username and confirm deletion to continue.", "error")
                return redirect(url_for("settings"))
            with closing(sqlite3.connect(app.config["DATABASE"])) as connection:
                with connection:
                    connection.execute(
                        """DELETE FROM interview_questions
                           WHERE interview_id IN (
                               SELECT id FROM interviews WHERE username = ?
                           )""",
                        (username,),
                    )
                    connection.execute("DELETE FROM interviews WHERE username = ?", (username,))
                    connection.execute("DELETE FROM users WHERE username = ?", (username,))
            session.clear()
            flash("Your account and interview history were deleted.", "success")
            return redirect(url_for("home"))

        flash("Choose a settings section and try again.", "error")
        return redirect(url_for("settings"))

    return render_template("settings.html", user=user)


@app.route("/interview/<int:interview_id>/delete", methods=["POST"])
@login_required
def delete_interview_history(interview_id):
    with closing(sqlite3.connect(app.config["DATABASE"])) as connection:
        with connection:
            owned = connection.execute(
                "SELECT id FROM interviews WHERE id = ? AND username = ?",
                (interview_id, session["username"]),
            ).fetchone()
            if owned is None:
                abort(404)
            connection.execute(
                "DELETE FROM interview_questions WHERE interview_id = ?",
                (interview_id,),
            )
            connection.execute(
                "DELETE FROM interviews WHERE id = ? AND username = ?",
                (interview_id, session["username"]),
            )
    flash("Interview history deleted.", "success")
    return redirect(url_for("dashboard"))


@app.route("/profile", methods=["GET", "POST"])
@login_required
def profile():
    username = session["username"]
    with closing(sqlite3.connect(app.config["DATABASE"])) as connection:
        connection.row_factory = sqlite3.Row
        user = connection.execute(
            """SELECT username, full_name, theme,
                      profile_picture IS NOT NULL AS has_profile_picture
               FROM users WHERE username = ?""",
            (username,),
        ).fetchone()
    if user is None:
        abort(404)

    if request.method == "POST":
        full_name = request.form.get("full_name", "").strip()
        if not full_name or len(full_name) > 120:
            flash("Enter a full name of up to 120 characters.", "error")
            return redirect(url_for("profile"))

        picture = None
        picture_file = request.files.get("profile_picture")
        if picture_file is not None and picture_file.filename:
            content = picture_file.stream.read(MAX_PROFILE_PICTURE_BYTES + 1)
            if len(content) > MAX_PROFILE_PICTURE_BYTES:
                flash("Profile pictures must be 2 MB or smaller.", "error")
                return redirect(url_for("profile"))
            try:
                picture = normalize_profile_picture(content)
            except ValueError as error:
                flash(str(error), "error")
                return redirect(url_for("profile"))

        with closing(sqlite3.connect(app.config["DATABASE"])) as connection:
            with connection:
                if picture is None:
                    connection.execute(
                        "UPDATE users SET full_name = ? WHERE username = ?",
                        (full_name, username),
                    )
                else:
                    connection.execute(
                        """UPDATE users SET full_name = ?, profile_picture = ?
                           WHERE username = ?""",
                        (full_name, picture, username),
                    )
        flash("Profile updated.", "success")
        return redirect(url_for("profile"))

    return render_template("profile.html", user=user)


@app.route("/profile/photo")
@login_required
def profile_photo():
    with closing(sqlite3.connect(app.config["DATABASE"])) as connection:
        row = connection.execute(
            "SELECT profile_picture FROM users WHERE username = ?",
            (session["username"],),
        ).fetchone()
    if row is None or row[0] is None:
        abort(404)
    response = Response(row[0], mimetype="image/jpeg")
    response.headers["Cache-Control"] = "private, no-store"
    return response


@app.route("/logout", methods=["POST"])
@login_required
def logout():
    session.clear()
    return redirect(url_for("home"))


@app.route("/resume", methods=["GET", "POST"])
@login_required
def resume_upload():
    if request.method == "POST":
        mode = request.form.get("mode", "Mixed")
        difficulty = request.form.get("difficulty", "Medium")
        duration_minutes = request.form.get("duration_minutes", default=30, type=int)
        job_description = request.form.get("job_description", "").strip()
        recording_enabled = int(request.form.get("recording_enabled") == "on")
        if len(job_description) > MAX_JOB_DESCRIPTION_CHARACTERS:
            flash(
                f"Job descriptions must be {MAX_JOB_DESCRIPTION_CHARACTERS:,} characters or fewer.",
                "error",
            )
            return redirect(url_for("resume_upload"))
        if mode not in INTERVIEW_MODES:
            flash("Choose a valid interview mode.", "error")
            return redirect(url_for("resume_upload"))
        if difficulty not in DIFFICULTY_LEVELS:
            flash("Choose a valid difficulty level.", "error")
            return redirect(url_for("resume_upload"))
        if duration_minutes not in DURATION_OPTIONS:
            flash("Choose a valid practice duration.", "error")
            return redirect(url_for("resume_upload"))

        resume_file = request.files.get("resume")
        if resume_file is None or not resume_file.filename:
            flash("Choose a PDF or DOCX resume to continue.", "error")
            return redirect(url_for("resume_upload"))

        content = resume_file.stream.read(MAX_RESUME_BYTES + 1)
        if len(content) > MAX_RESUME_BYTES:
            flash("Resume file must be 5 MB or smaller.", "error")
            return redirect(url_for("resume_upload"))

        try:
            resume_text = extract_resume_text(resume_file.filename, content)
            questions, target_skills, gap_skills = generate_interview_questions(
                resume_text,
                mode,
                difficulty,
                duration_minutes,
                job_description,
            )
        except ValueError as error:
            flash(str(error), "error")
            return redirect(url_for("resume_upload"))

        with closing(sqlite3.connect(app.config["DATABASE"])) as connection:
            with connection:
                cursor = connection.execute(
                    """INSERT INTO interviews
                       (username, mode, difficulty, duration_minutes, target_skills,
                        gap_skills, recording_enabled)
                       VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    (
                        session["username"],
                        mode,
                        difficulty,
                        duration_minutes,
                        json.dumps(target_skills),
                        json.dumps(gap_skills),
                        recording_enabled,
                    ),
                )
                interview_id = cursor.lastrowid
                connection.executemany(
                    """INSERT INTO interview_questions
                       (interview_id, position, category, skill, question, sample_answer,
                        is_follow_up, score, star_feedback)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    [(interview_id, *question) for question in questions],
                )
        return redirect(url_for("interview_page", interview_id=interview_id))

    return render_template("resume.html", username=session["username"])


@app.route("/interview")
@login_required
def index():
    return redirect(url_for("dashboard"))


@app.route("/interview/<int:interview_id>")
@login_required
def interview_page(interview_id):
    interview = owned_interview(interview_id)
    with closing(sqlite3.connect(app.config["DATABASE"])) as connection:
        connection.row_factory = sqlite3.Row
        questions = connection.execute(
            """SELECT id, position, category, skill, question, status, is_follow_up
               FROM interview_questions
               WHERE interview_id = ? ORDER BY position""",
            (interview_id,),
        ).fetchall()
    pending = next((question for question in questions if question["status"] == "pending"), None)
    if pending is None:
        return redirect(url_for("interview_results", interview_id=interview_id))
    pending = dict(pending)
    pending["question"] = concise_interview_question(pending["question"])
    created_at = datetime.fromisoformat(interview[1])
    if created_at.tzinfo is None:
        created_at = created_at.replace(tzinfo=timezone.utc)
    deadline = created_at + timedelta(minutes=interview[4])
    remaining_seconds = max(
        0,
        int((deadline - datetime.now(timezone.utc)).total_seconds()),
    )
    return render_template(
        "index.html",
        username=session["username"],
        interview_id=interview_id,
        question=pending,
        completed_count=sum(question["status"] != "pending" for question in questions),
        question_count=len(questions),
        mode=interview[2],
        difficulty=interview[3],
        duration_minutes=interview[4],
        recording_enabled=bool(interview[7]),
        remaining_seconds=remaining_seconds,
    )


@app.route("/interview/<int:interview_id>/answer", methods=["POST"])
@login_required
def submit_text_answer(interview_id):
    interview = owned_interview(interview_id)
    if get_remaining_seconds(interview) <= 0:
        return jsonify({"error": "The session time has expired. Your interview is being finished."}), 409
    question_id = request.form.get("question_id", type=int)
    answer = request.form.get("answer", "").strip()
    if question_id is None or not answer:
        return jsonify({"error": "Write an answer before submitting it."}), 400
    if len(answer) > 10_000:
        return jsonify({"error": "Answers must be 10,000 characters or fewer."}), 413
    result, error = save_answer(interview_id, question_id, answer)
    if error:
        return error
    return jsonify(result)


@app.route("/interview/<int:interview_id>/skip", methods=["POST"])
@login_required
def skip_question(interview_id):
    interview = owned_interview(interview_id)
    if get_remaining_seconds(interview) <= 0:
        return jsonify({"error": "The session time has expired. Your interview is being finished."}), 409
    question_id = request.form.get("question_id", type=int)
    if question_id is None:
        return jsonify({"error": "Question ID is required."}), 400
    question = get_question(interview_id, question_id)
    if question[7] != "pending":
        return jsonify({"error": "This question has already been completed."}), 409
    with closing(sqlite3.connect(app.config["DATABASE"])) as connection:
        with connection:
            connection.execute(
                """UPDATE interview_questions SET status = 'skipped'
                   WHERE id = ? AND interview_id = ? AND status = 'pending'""",
                (question_id, interview_id),
            )
    return jsonify(advance_response(interview_id))


def get_remaining_seconds(interview):
    created_at = datetime.fromisoformat(interview[1])
    if created_at.tzinfo is None:
        created_at = created_at.replace(tzinfo=timezone.utc)
    deadline = created_at + timedelta(minutes=interview[4])
    return max(0, int((deadline - datetime.now(timezone.utc)).total_seconds()))


@app.route("/interview/<int:interview_id>/finish", methods=["POST"])
@login_required
def finish_interview(interview_id):
    owned_interview(interview_id)
    with closing(sqlite3.connect(app.config["DATABASE"])) as connection:
        with connection:
            connection.execute(
                """UPDATE interview_questions
                   SET status = 'skipped',
                       feedback = 'Skipped because the session time ended.'
                   WHERE interview_id = ? AND status = 'pending'""",
                (interview_id,),
            )
            connection.execute(
                """UPDATE interviews SET completed_at = COALESCE(completed_at, CURRENT_TIMESTAMP)
                   WHERE id = ?""",
                (interview_id,),
            )
    return jsonify(
        {
            "complete": True,
            "result_url": url_for("interview_results", interview_id=interview_id),
        }
    )


@app.route("/interview/<int:interview_id>/results")
@login_required
def interview_results(interview_id):
    interview = owned_interview(interview_id)
    with closing(sqlite3.connect(app.config["DATABASE"])) as connection:
        connection.row_factory = sqlite3.Row
        questions = connection.execute(
            """SELECT position, skill, question, sample_answer, answer, feedback, status,
                      category, score, star_feedback, is_follow_up
               FROM interview_questions WHERE interview_id = ? ORDER BY position""",
            (interview_id,),
        ).fetchall()
    questions = [
        {**dict(question), "question": concise_interview_question(question["question"])}
        for question in questions
    ]
    if any(question["status"] == "pending" for question in questions):
        return redirect(url_for("interview_page", interview_id=interview_id))
    return render_template(
        "feedback.html",
        username=session["username"],
        questions=questions,
        answered_count=sum(question["status"] == "answered" for question in questions),
        skipped_count=sum(question["status"] == "skipped" for question in questions),
        interview_id=interview_id,
        mode=interview[2],
        difficulty=interview[3],
        duration_minutes=interview[4],
        target_skills=json.loads(interview[5]),
        gap_skills=json.loads(interview[6]),
        recording_enabled=bool(interview[7]),
    )


@app.errorhandler(413)
def request_too_large(error):
    message = "Upload is too large. Resume files must be 5 MB or smaller."
    flash(message, "error")
    return redirect(url_for("resume_upload"))


if __name__ == "__main__":
    app.run(debug=os.environ.get("FLASK_DEBUG") == "1")
