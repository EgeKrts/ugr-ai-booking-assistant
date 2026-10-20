"""Command-line entry points for dataset inspection and e-mail parsing."""
import argparse
import json
from pathlib import Path
import sys
import zipfile
from .config import get_dataset_path
from .dataset import DatasetError
from .extractor import ExtractionError, Extractor
from .ingest import read_dataset_emails
from .master_data import MasterIndex, load_dataset
from .validators import validate_extraction

def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="UGR booking utilities")
    commands = parser.add_subparsers(dest="command", required=True)
    inspect = commands.add_parser("inspect-dataset", help="Validate package metadata without processing e-mail bodies")
    inspect.add_argument("--dataset", type=Path, help="Extracted dataset directory or original ZIP")
    parse = commands.add_parser("parse-emails", help="Parse and clean every manifest e-mail; prints IDs and warning codes only")
    parse.add_argument("--dataset", type=Path, help="Extracted dataset directory or original ZIP")
    extract = commands.add_parser("extract", help="Run the LLM on one e-mail and print the unvalidated result (nothing is saved)")
    extract.add_argument("email_id", help="For example EMAIL-017")
    extract.add_argument("--dataset", type=Path, help="Extracted dataset directory or original ZIP")
    validate = commands.add_parser("validate", help="Extract with the LLM, then apply the business rules (nothing is saved)")
    validate.add_argument("email_id", help="For example EMAIL-019")
    validate.add_argument("--dataset", type=Path, help="Extracted dataset directory or original ZIP")
    args = parser.parse_args(argv)
    try:
        path = get_dataset_path(args.dataset)
        if args.command == "validate":
            dataset = load_dataset(path)
            records = {record.email_id: record for record in read_dataset_emails(path).records}
            if args.email_id not in records:
                raise ValueError(f"Unknown or unreadable e-mail: {args.email_id}")
            record = records[args.email_id]
            try:
                extraction = Extractor().extract(record)
            except ExtractionError as exc:
                print(json.dumps({"email_id": record.email_id, "extraction_error": str(exc)}, indent=2, ensure_ascii=False))
                return 1
            result = validate_extraction(record, extraction, MasterIndex.from_dataset(dataset))
            print(json.dumps(result.model_dump(mode="json", exclude={"bookings": {"__all__": {"raw": True}}}, exclude_none=True),
                             indent=2, ensure_ascii=False))
            return 0
        if args.command == "extract":
            records = {record.email_id: record for record in read_dataset_emails(path).records}
            if args.email_id not in records:
                raise ValueError(f"Unknown or unreadable e-mail: {args.email_id}")
            record = records[args.email_id]
            output = {"email_id": record.email_id, "warnings": list(record.warnings)}
            try:
                output["extraction"] = Extractor().extract(record).model_dump(mode="json")
            except ExtractionError as exc:
                output["extraction_error"] = str(exc)
            print(json.dumps(output, indent=2, ensure_ascii=False))
            return 1 if "extraction_error" in output else 0
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
