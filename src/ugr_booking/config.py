"""Dataset settings for local inspection."""
import os
from pathlib import Path
from dotenv import load_dotenv

DEFAULT_DATASET_PATH = Path("data/private/dataset")

def get_dataset_path(explicit_path: Path | None = None) -> Path:
    if explicit_path is not None:
        return explicit_path
    load_dotenv(Path.cwd() / ".env", override=False, encoding="utf-8-sig")
    return Path(os.environ.get("DATASET_PATH") or DEFAULT_DATASET_PATH)
