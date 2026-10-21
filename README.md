# Tumaini Senior Secondary School Timetable Management System

Desktop timetable-management application built with Flet and SQLite.

## Requirements

- Python 3.11 or later
- Packages in `requirements.txt`

## Run

From this directory, create and activate a virtual environment, install the dependencies, then start the app:

```powershell
py -3 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python main.py
```

The SQLite database is stored locally as `database.db`. The app creates its schema when it starts. School records, accounts, and timetables are not included in this repository; restore an authorized private database backup to this directory to use an existing school installation. Do not commit school data or database backups.

## Timetable curriculum

The requirements setup follows the published Tanzania Institute of Education curricula:

- [Ordinary Secondary Education, Form I–IV curriculum](https://www.tie.go.tz/publications/curriculum-for-lower-secondary-academic)
- [Advanced Secondary Education, Form V–VI curriculum](https://www.tie.go.tz/publications/curriculum-for-upper-secondary)

The app schedules 40-minute lessons on weekdays. Requirements include the published compulsory core, configured A-Level combinations, and the school's specified Amali modules. O-Level elective subjects must be configured for the school. The three Amali module allocations are stored as 290 annual hours each; their weekly period counts are scheduling approximations.
