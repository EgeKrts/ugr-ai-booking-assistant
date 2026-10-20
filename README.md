# UGR AI Booking Intake & Operations Assistant

The intended application turns booking e-mails into validated booking records,
then provides an interactive dashboard and natural-language queries over the same
stored data.

**Current scope: backend complete through step 4.** E-mails are parsed, read by a local
LLM, checked by deterministic rules and applied to a SQLite database through controlled
tools. An interactive dashboard and a read-only chatbot answer from the same database.

```text
E-mail -> preprocess -> LLM extraction -> validation -> tool policy gate -> SQLite
```

## Prerequisites

- Python 3.11 or 3.12 (development environment: Python 3.12).
- Git.
- [Ollama](https://ollama.com) with the `qwen3:8b` model (`ollama pull qwen3:8b`); about 6 GB of GPU memory is enough.

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

## Commands

```powershell
$py = ".\.venv\Scripts\python.exe"
& $py -m ugr_booking.cli inspect-dataset          # manifest + master data checks
& $py -m ugr_booking.cli parse-emails             # read and clean all e-mails (no model)
& $py -m ugr_booking.cli extract  EMAIL-017       # model output for one e-mail, nothing saved
& $py -m ugr_booking.cli validate EMAIL-019       # model + business rules, nothing saved
& $py -m ugr_booking.cli ingest                   # all e-mails into data/booking.sqlite3
& $py -m ugr_booking.cli ingest --planner llm --only EMAIL-008 EMAIL-012 EMAIL-013
& $py -m ugr_booking.cli list-bookings --events   # what is stored
& $py -m ugr_booking.cli report                   # Markdown: every e-mail's outcome, bookings and events
& $py -m ugr_booking.cli record-extractions       # save the model's output for all e-mails (data/private/extractions)
& $py -m ugr_booking.cli ingest --extractions data/private/extractions   # offline replay, labelled as not live AI
& $py -m streamlit run ui/app.py                  # dashboard on http://localhost:8501
$env:UGR_CHAT_PLANNER = "keywords"                 # optional: chat without the language model
& $py -m pytest -q                                # tests need no model
& $py -m pytest --live -q                          # also the regression checks that call the real model
```

Example chat questions: *How many bookings does ABC Shipping have?* - *Show bookings with Izmir as the port of
loading.* - *Which bookings require validation?* - *Summarize the latest three bookings.* - *What is the status of
BOOKINGREF-D100?* - *Show cancelled bookings.*

Definitions used by the dashboard and the chatbot: **latest** = the booking whose request e-mail arrived most
recently (not processing time); **cancelled bookings are counted** and the answer says so; **needs validation** =
status `VALIDATION_REQUIRED`; non-booking e-mails are never counted as bookings.

`ingest` is repeatable: e-mails already stored are skipped, so a second run adds nothing.
Details: [extraction](docs/step_2.md), [validation](docs/step_3.md), [persistence and tools](docs/step_4.md), [dashboard](docs/step_5.md), [chatbot](docs/step_6.md), [test and batch run record](docs/run_record.md).

## Configuration and private data

`.env.example` contains placeholder names only. Dataset selection follows
`--dataset`, then `DATASET_PATH`, then the default directory below. The command
loads only the working directory's `.env`; existing environment variables win.
`OLLAMA_URL` / `OLLAMA_MODEL` select the local model and `DATABASE_URL` the database
(default `sqlite:///data/booking.sqlite3`, ignored by Git). No API key is needed anywhere.

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
src/ugr_booking/   Python application package (ingest, extractor, validators, tools, orchestrator)
ui/               Streamlit dashboard (ui/app.py)
tests/            Unit and end-to-end tests with invented fixtures (no model needed)
docs/             Decision notes (architecture diagram lives in this README)
data/private/     Local inputs, excluded from Git
```

## Working agreement

Follow the root development checklist. Complete and verify one agreed step,
record what changed and why, then stop before the next step. The initial local
Git repository is not a published GitHub repository.

See [the decision log](docs/decisions.md) for the rationale behind the setup.
