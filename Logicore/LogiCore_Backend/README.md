# LogiCore: A Rule-Based Scholarship Decision System

A Flask + SQLite web system. Each rule has a score (its priority). The system checks every rule for an applicant, the matching rule with the highest score decides, and the page shows which rule decided and why.

## Run it

1. Install Python 3.10 or newer.
2. In this folder, run: `pip install -r requirements.txt`
3. Run: `python app.py`
4. Open http://127.0.0.1:5000

The database (`instance/logicore.db`) is created automatically on the first run.

**Demo accounts:** `admin` / `admin123` (administrator) and `staff` / `staff123` (staff).
Change them before real use: `flask --app app create-user admin admin` asks for a new password.

## Features

- Login with hashed passwords, two roles (administrator and staff), login rate limit, CSRF protection
- Evaluate an applicant with a rule trace that shows which rule decided
- Administrator page to add, edit, delete, and reset rules (stored in the database)
- History of every decision, summary report, and CSV export
- Test cases page that runs six sample applicants against the current rules

## Files

| File | Job |
|---|---|
| `app.py` | Web app and JSON API (Flask) |
| `engine.py` | Rule engine (no database or web code) |
| `database.py` | SQLite schema, seed rules, and queries |
| `templates/index.html` | Page structure |
| `static/style.css` | Design |
| `static/app.js` | Front end, calls the API |
| `tests/` | Automated tests (`python -m unittest discover -s tests`) |

`logicore.py` (one level up in the download) is the same system in a single file.

## Database tables

`users`, `rules`, `rule_conditions`, `applicants`, `decisions`, `decision_trace`.
`rules` 1 to many `rule_conditions`; `applicants` 1 to many `decisions`; `decisions` 1 to many `decision_trace`; `users` 1 to many `decisions`.

## Limits

The rules are sample criteria. Replace them with the school's official criteria on the Manage rules page. This runs on a development server for demonstration, not a production deployment.
