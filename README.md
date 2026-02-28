# Nagme Desktop

Desktop companion app for the Nagme Supabase data store.

## What It Does

- Logs into Supabase with email/password.
- Shows a Supabase auth light:
  - Green = signed in
  - Red = not signed in
- Downloads readable nag rows from `nag` table (falls back to `events` if needed).
- Reconstructs current nag state from event rows.
- Shows nag bars with due/progress coloring and time/percent indicators.
- Supports bucket filtering, sort modes, recurring window modes.
- Shows add/edit/delete controls but runs in view-only mode right now (write actions are disabled).

## Setup

1. Open a terminal in this folder.
2. Install dependencies:

```bash
pip install -r requirements.txt
```

3. Run:

```bash
python nagme_desktop.py
```

## Notes

- Works on Windows and Linux (Tkinter UI).
- Uses project URL/key already configured in the script.
- If you get insert/read permission errors, update Supabase RLS policies for authenticated users.
