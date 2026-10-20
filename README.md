# UGR AI Booking Intake & Operations Assistant

The intended application turns booking e-mails into validated booking records,
then provides an interactive dashboard and natural-language queries over the same
stored data.

**Current scope: step 1.1, project setup.** No dataset reader, LLM extraction,
booking database, dashboard or chatbot has been implemented in this project yet.
The next step is 1.2, dataset and master-data loading.

## Prerequisites

- Python 3.11 or 3.12 (development environment: Python 3.12).
- Git.
- An LLM provider will be selected during the extraction milestone.

## Setup (Windows PowerShell)

From the repository directory:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-lock.txt
.\.venv\Scripts\python.exe -m pip install -e .
.\.venv\Scripts\python.exe -m pip check
```

If `python` is not on PATH, use the full path of an available Python 3.11/3.12
executable for the first command. Explicitly invoking the virtual environment's
Python avoids PowerShell activation-policy changes.

`requirements.txt` pins the five direct dependencies. `requirements-lock.txt`
records the complete installed development environment, excluding this editable
package; it is generated and verified on Windows with Python 3.12. Other operating
systems/Python versions may need a fresh resolution from `requirements.txt`.

Verify the package installation:

```powershell
.\.venv\Scripts\python.exe -c "import ugr_booking; print(ugr_booking.__file__)"
```

There is no application command or UI to run at this setup milestone. Automated
business tests will be added alongside the relevant functionality; an empty test
directory is not a successful test suite.

## Configuration and private data

`.env.example` contains placeholder names only. When a configuration loader is
implemented, copy it to `.env` and fill in the required local values. Never put a
real API key in tracked files or chat. Provider/model selection is still pending.
The environment variables are not consumed by application code yet.

The intended dataset location is:

```text
data/private/dataset.zip
```

The dataset has not been copied or processed in this milestone. Dataset archives,
raw inputs, generated databases and logs are excluded from Git. Tests will use
invented fixtures rather than committed dataset e-mail bodies.

## Project layout

```text
src/ugr_booking/   Python application package; modules added step by step
ui/               Future Streamlit entry point
tests/            Future automated tests
docs/             Decision notes and later architecture/demo documents
data/private/     Local inputs, excluded from Git
```

## Working agreement

Follow the root development checklist. Complete and verify one agreed step,
record what changed and why, then stop before the next step. The initial local
Git repository is not a published GitHub repository.

See [the decision log](docs/decisions.md) for the rationale behind the setup.
