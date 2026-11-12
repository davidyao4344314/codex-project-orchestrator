Build a small local notes app in Python with Streamlit, SQLite, and built-in unittest.

Requirements:
- Add a note with a required title and optional body. Trim the title and reject blank titles.
- List notes newest first, showing the title and body as text.
- Persist notes across application and browser restarts in data/notes.sqlite3.
- Keep storage separate from service validation and the Streamlit screen.
- Test storage and validation using temporary databases; never touch personal data in tests.
- Show a useful empty state, validation feedback, and a helpful storage-error message.
- Provide accurate setup/run/test instructions.

This is a worker acceptance project. Make these explicit assignments so every profile can be
reviewed (agents may be reused across assignments; do not create unnecessary idle agents):
1. Database task (workstream database): storage.py and tests/test_storage.py. Define a small
   parameterized SQLite storage API with an injectable database path. Verify persistence
   using a fresh connection. Own only source/test files, not generated SQLite databases.
2. Backend task (workstream backend): service.py and tests/test_service.py. Implement title
   validation and call the approved storage API. Depend on the verified storage handoff.
3. Frontend task (workstream frontend): app.py and tests/test_app.py. Add the form, list,
   empty/error states, and focused Streamlit AppTest checks. Use NOTES_DB_PATH to inject
   a temporary test database. Leave visual usability and process-restart checks to the user.
4. Integration task (workstream integration): tests/test_integration.py. Exercise the full
   service/storage flow and verify the screen's contract; depend on the earlier handoffs.
5. General task (workstream general): README.md. Document the final local commands,
   database location, and user checklist, with automated command/test verification.
6. Read-only verification task: own no files; require the implementation outputs and run
   the complete automated suite using temporary/cache-safe locations. Report defects rather
   than changing code. All worker reports need actual command evidence and remaining user tests.

Use delivery stages and explicit verification gates. Do not automatically merge, commit,
push, or deploy. Keep writes sequential and require user acceptance before dependent stages.
Exclude accounts, cloud services, external APIs, deletion, editing notes, background jobs,
notifications, a separate web server framework, and extra product features.
