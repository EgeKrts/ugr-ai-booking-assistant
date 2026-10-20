"""Independent invented fixtures. Private dataset e-mail bodies are never committed."""
from datetime import datetime
import json
from pathlib import Path
import zipfile
import pytest
from pydantic import ValidationError
from ugr_booking.dataset import DatasetError, DatasetSource, load_manifest
from ugr_booking.master_data import expected_booking_contract, load_dataset
from ugr_booking.schemas import BookingDraft, MessageIntent, ProcessingStatus

@pytest.fixture
def package(tmp_path):
    root = tmp_path / "dataset"
    (root / "emails").mkdir(parents=True)
    (root / "master_data").mkdir()
    manifest = []
    for i in range(34):
        email_id = f"EXAMPLE-{i:03}"
        path = f"emails/{email_id}.txt"
        (root / path).write_text("Invented fixture, no real business data.", encoding="utf-8")
        manifest.append({
            "email_id": email_id, "file": path,
            "received_at": f"2026-10-{i//24+1:02} {i%24:02}:00",
            "subject": "Invented sample",
        })
    write_json(root / "manifest.json", list(reversed(manifest)))
    customers = [{"customer_id": f"C{i}", "name": f"Example Carrier {i}", "aliases": [f"Alias {i}"]} for i in range(4)]
    ports = [{"port_code": f"P{i}", "name": f"Example Port {i}", "aliases": [f"PORT{i}"]} for i in range(12)]
    write_json(root / "master_data/customers.json", customers)
    write_json(root / "master_data/ports.json", ports)
    write_json(root / "master_data/booking_schema.json", expected_booking_contract())
    return root

def write_json(path, obj):
    path.write_text(json.dumps(obj), encoding="utf-8")

def alter_manifest(root, change):
    path = root / "manifest.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    change(data)
    write_json(path, data)

def test_manifest_loads_34_unique_emails_in_time_order(package):
    result = load_dataset(package)
    assert len(result.manifest) == 34
    assert len({item.email_id for item in result.manifest}) == 34
    assert result.manifest[0].email_id == "EXAMPLE-000"
    assert result.manifest[-1].email_id == "EXAMPLE-033"
    assert result.manifest[0].received_at == datetime(2026,10,1,0,0)
    assert result.manifest[0].received_at.tzinfo is None

def test_master_records_and_aliases_are_preserved(package):
    result = load_dataset(package)
    assert len(result.customers) == 4
    assert len(result.ports) == 12
    assert result.customers[0].aliases == ["Alias 0"]
    assert result.ports[0].aliases == ["PORT0"]

def test_duplicate_email_id_is_rejected(package):
    alter_manifest(package, lambda data: data[0].update(email_id=data[1]["email_id"]))
    with pytest.raises(DatasetError, match="duplicate e-mail"):
        load_dataset(package)

def test_duplicate_source_file_is_rejected(package):
    alter_manifest(package, lambda data: data[0].update(file=data[1]["file"]))
    with pytest.raises(DatasetError, match="same file"):
        load_dataset(package)

def test_missing_referenced_file_is_rejected(package):
    (package / "emails/EXAMPLE-001.txt").unlink()
    with pytest.raises(DatasetError):
        load_dataset(package)

@pytest.mark.parametrize("unsafe", ["../outside.txt", "/outside.txt", "C:/outside.txt", "..\\outside.txt"])
def test_directory_path_escape_is_rejected(package, unsafe):
    alter_manifest(package, lambda data: data[0].update(file=unsafe))
    with pytest.raises(DatasetError):
        load_dataset(package)

def test_zip_path_escape_is_rejected_before_reading(tmp_path):
    archive_path = tmp_path / "unsafe.zip"
    with zipfile.ZipFile(archive_path,"w") as archive:
        archive.writestr("../outside.txt","do not extract")
    with pytest.raises(DatasetError):
        with DatasetSource(archive_path):
            pass
    assert not (tmp_path / "outside.txt").exists()

def test_zip_and_directory_have_the_same_metadata(package, tmp_path):
    archive_path = tmp_path / "valid.zip"
    with zipfile.ZipFile(archive_path,"w") as archive:
        for file in package.rglob("*"):
            if file.is_file():
                archive.write(file,file.relative_to(package).as_posix())
    folder_result = load_dataset(package)
    zip_result = load_dataset(archive_path)
    assert folder_result.manifest == zip_result.manifest
    assert folder_result.customers == zip_result.customers
    assert folder_result.ports == zip_result.ports

def test_impossible_manifest_date_is_rejected(package):
    alter_manifest(package, lambda data: data[0].update(received_at="2026-02-31 10:00"))
    with pytest.raises(DatasetError, match="Manifest entry"):
        load_dataset(package)

def test_incomplete_dataset_is_rejected(package):
    alter_manifest(package, lambda data: data.pop())
    with pytest.raises(DatasetError, match="Expected 34"):
        load_dataset(package)

def test_booking_schema_drift_is_visible(package):
    path = package / "master_data/booking_schema.json"
    schema = json.loads(path.read_text())
    schema["quantity"] = "string|null"
    write_json(path,schema)
    with pytest.raises(DatasetError, match="schema differs"):
        load_dataset(package)

@pytest.mark.parametrize("quantity", ["3", True, 2.5])
def test_booking_quantity_is_not_silently_coerced(quantity):
    with pytest.raises(ValidationError):
        BookingDraft(source_email_id="INVENTED",quantity=quantity)

@pytest.mark.parametrize("date_text", ["2026-02-31", "next week", "20261020"])
def test_booking_date_must_be_real_and_explicit(date_text):
    with pytest.raises(ValidationError):
        BookingDraft(source_email_id="INVENTED",requested_date=date_text)

def test_missing_business_fields_remain_missing():
    draft=BookingDraft(source_email_id="INVENTED")
    assert draft.customer is None and draft.pol is None and draft.requested_date is None
    assert draft.message_intent == MessageIntent.UNKNOWN
    assert draft.processing_status == ProcessingStatus.RECEIVED
