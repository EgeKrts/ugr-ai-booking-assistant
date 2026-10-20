"""Command-line entry points for dataset inspection and e-mail parsing."""
import argparse
import json
from pathlib import Path
import sys
import zipfile
from .config import get_dataset_path
from .dataset import DatasetError
from .ingest import read_dataset_emails
from .master_data import load_dataset

def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="UGR booking utilities")
    commands = parser.add_subparsers(dest="command", required=True)
    inspect = commands.add_parser("inspect-dataset", help="Validate package metadata without processing e-mail bodies")
    inspect.add_argument("--dataset", type=Path, help="Extracted dataset directory or original ZIP")
    parse = commands.add_parser("parse-emails", help="Parse and clean every manifest e-mail; prints IDs and warning codes only")
    parse.add_argument("--dataset", type=Path, help="Extracted dataset directory or original ZIP")
    args = parser.parse_args(argv)
    try:
        path = get_dataset_path(args.dataset)
        if args.command == "parse-emails":
            report = read_dataset_emails(path)
            print(json.dumps(report.summary(), indent=2, ensure_ascii=True))
            return 0 if report.manifest_total == len(report.records) + len(report.failures) else 1
        result = load_dataset(path)
    except (DatasetError, OSError, ValueError, KeyError, RuntimeError, zipfile.BadZipFile, NotImplementedError) as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=True), file=sys.stderr)
        return 1
    print(json.dumps(result.summary(), indent=2, ensure_ascii=True))
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
