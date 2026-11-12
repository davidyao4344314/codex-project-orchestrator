# Worker smoke: Notes

A disposable Python starter for reviewing all worker profiles through one small project.
Use BRIEF.md as the planning request. The current app provides the starter screen; assigned
workers will implement storage, service logic, UI, integration tests, and documentation.

Prepare the copied project before starting workers:

```sh
python -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
```

Run the screen:

```sh
python -m streamlit run app.py
```

After workers add the tests:

```sh
python -m unittest discover -s tests -v
```

Keep test databases in temporary directories. Use only disposable notes during browser tests.
