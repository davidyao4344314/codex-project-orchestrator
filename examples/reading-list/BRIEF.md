Build a personal reading-list app for one person, using Python, Streamlit, and SQLite.

The starter app.py contains the initial screen. Implement these requirements:
- Add a book with a required title and author. Trim whitespace and reject blank values.
- List saved books, newest first. Each book has a status: To read, Reading, or Finished.
- Change a book's status and filter the list by status.
- Persist the list in SQLite so it survives app and browser restarts.
- Show useful empty states and validation errors.
- Use focused automated tests for storage and validation, then a manual browser check.

Keep the first version local and single-user. Exclude accounts, cloud deployment,
external book APIs, recommendations, notifications, cover-image uploads, and deletion.

Plan this as delivery stages with explicit agent responsibilities, context, models,
handoffs, and a verification gate before merging each stage.
