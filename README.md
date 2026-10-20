# UGR AI Booking Intake & Operations Assistant

The intended application turns booking e-mails into validated booking records,
then provides an interactive dashboard and natural-language queries over the same
stored data.

**Current scope: step 1.2, dataset metadata and typed schemas.** The inspection
command validates the manifest, referenced file paths and master data. E-mail
body parsing starts in step 1.3; LLM, booking persistence and UI are later steps.

## Prerequisites

- Python 3.11 or 3.12 (development environment: Python 3.12).
- Git.
- An LLM provider will be selected during the extraction milestone.

## Setup (Windows PowerShell)

From the repository directory:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m pip install --no-build-isolation -e .
.\.venv\Scripts\python.exe -m pip check
```

If `python` is not on PATH, use the full path of an available Python 3.11/3.12
executable for the first command. Explicitly invoking the virtual environment's
Python avoids PowerShell activation-policy changes.

`requirements.txt` pins the five direct dependencies and is the single dependency
source (also read by `pyproject.toml`). Tested on Windows with Python 3.12.

Verify the package installation:

```powershell
.\.venv\Scripts\python.exe -c "import ugr_booking; print(ugr_booking.__file__)"
```

Inspect the supplied dataset (run from the repository directory):

```powershell
.\.venv\Scripts\python.exe -m ugr_booking.cli inspect-dataset
.\.venv\Scripts\python.exe -m pytest tests/test_dataset.py -q
```

Use `--dataset "C:\path\dataset"` or `--dataset "C:\path\dataset.zip"` to
choose an explicit source. The command prints a JSON summary and exits with 0
on success or 1 on a controlled package-validation failure. It does not read
e-mail bodies, create bookings or call an external service.

## Configuration and private data

`.env.example` contains placeholder names only. Dataset selection follows
`--dataset`, then `DATASET_PATH`, then the default directory below. The command
loads only the working directory's `.env`; existing environment variables win.
Provider/model selection is still pending; API/database variables are placeholders
for later steps. No API key is needed for metadata inspection.

The intended dataset location is:

```text
data/private/dataset/
```

Provide the extracted package with `manifest.json`, `emails/` and `master_data/`
at its root; an original ZIP is also supported. Dataset archives, raw inputs,
generated databases and logs are excluded from Git. Tests use invented fixtures.

The dataset inspector expects 34 unique e-mail IDs, 4 customers and 12 ports. These
are package acceptance checks, not fixed operational booking counts. Inputs are
read as UTF-8 (BOM accepted); no ZIP extraction or e-mail execution occurs.
Manifest dates are sorted without inventing a timezone. Suggested booking fields
are mapped to static Pydantic types; schema validity alone never approves a booking.
See [step 1.2 and its limits](docs/step_1_2.md).

## Project layout

```text
src/ugr_booking/   Python application package; modules added step by step
ui/               Future Streamlit entry point
tests/            Metadata, path safety and schema tests
docs/             Decision notes (architecture diagram lives in this README)
data/private/     Local inputs, excluded from Git
```

## Working agreement

Follow the root development checklist. Complete and verify one agreed step,
record what changed and why, then stop before the next step. The initial local
Git repository is not a published GitHub repository.

See [the decision log](docs/decisions.md) for the rationale behind the setup.
