# Step 1.2 verification

Command: python -m pytest tests/test_dataset.py -q

.....................                                                    [100%]
21 passed in 0.74s

Actual dataset inspection:

```json
{
  "dataset_kind": "directory",
  "email_count": 34,
  "unique_email_ids": 34,
  "matched_email_files": 34,
  "customer_count": 4,
  "port_count": 12,
  "booking_schema_fields": 20,
  "message_intents": [
    "CREATE_BOOKING",
    "UPDATE_BOOKING",
    "CANCEL_BOOKING",
    "STATUS_INQUIRY",
    "NON_BOOKING",
    "UNKNOWN"
  ],
  "processing_statuses": [
    "RECEIVED",
    "PARSED",
    "VALIDATION_REQUIRED",
    "READY",
    "ASSIGNED",
    "CANCELLED"
  ],
  "first_email": "EMAIL-006",
  "first_received_at": "2026-08-20T08:55:00",
  "last_email": "EMAIL-034",
  "last_received_at": "2026-10-09T16:45:00"
}
```

Only metadata was loaded. Step 1.3 has not started.
