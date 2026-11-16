# AI-powered virtual HR interview

A small Flask practice app with account registration, a personal dashboard, resume-based technical questions, webcam preview, local answer-audio recording, and end-of-interview feedback.

## Run on Windows

Use Python 3.12 (or another version supported by the packages):

```powershell
cd "C:\path\to\AI-POWERED-VIRTUAL-HR-INTERVIEW-WITH-REALTIME-FEEDBACK-main"
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python app.py
```

Open <http://127.0.0.1:5000>. After login, the dashboard provides a mock-interview entry point, weekly goals, skill focus areas, recent answer-quality trends, and interview history that is hidden until expanded. The top-right streak button shows current and longest streaks; opening it reveals badge progress and completed-session dates, times, and counts. The round profile menu links to Edit profile, Settings, and Log out. Edit profile manages your full name and picture; Settings manages your password, light/dark theme, in-app reminder, weekly goal, local-recording cleanup, and guarded account deletion.

From the dashboard, upload a text-based PDF or DOCX resume (up to 5 MB). Choose Technical, Behavioral, HR, or Mixed mode, Easy/Medium/Hard difficulty, and a 15/30/45/60-minute session. The duration determines the number of initial questions (5/10/15/20); a timer marks remaining questions skipped when time expires. You can paste an optional job description to prioritize matching supported skills and see skills to practice. Technical prompts stay focused on one skill instead of quoting long resume excerpts. Resume and job-description text are analyzed in memory and are not saved. Questions, answers, feedback, interview settings, and detected skill names are stored in the local SQLite database. Answered questions get one follow-up based on the STAR checklist gaps. The final review shows the answers, practice-only STAR score, feedback, and a framework for skipped questions.

Registration collects your full name; existing accounts can edit it from Edit profile. Account deletion requires the current password, typing the account username, and explicit confirmation. The older `login.py` launcher delegates to the same application as `app.py`.

The browser shows a live webcam preview during practice. Optional session video/audio recording remains off unless you opt in. Each answer also has a separate microphone control: click to start recording, then click the red microphone to stop and save locally. Choose a microphone input from the selector and watch the live level meter to confirm sound reaches it. Listen and type or edit a transcript. Feedback is based on the typed transcript; audio is not automatically transcribed or sent to Flask. Both kinds of recording stay in the browser's IndexedDB and can be deleted from the interview feedback page or Settings. Deleting a history item from the dashboard also removes its local recordings. Local recordings are separate from the server account and are not removed by account deletion. Copy, cut, and paste are blocked during interviews; switching tabs triggers a warning when you return, and the timer continues. These are browser-side deterrents, not a secure browser lockdown.

Question generation, job-description matching, follow-ups, and answer guidance run locally using templates and detected technologies; they do not call an AI API. The STAR checklist is a transparent practice aid, not an AI hiring assessment or a hiring recommendation. The app stores user records and a generated Flask session key under `instance/`. Set `FLASK_SECRET_KEY` before deployment, and do not use Flask's development server for a public production deployment.

## Tests

```powershell
python -m unittest discover -s tests -v
```
