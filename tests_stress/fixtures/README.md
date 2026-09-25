# Historical backup schema fixtures

These are small sanitized **release-schema fixtures**, not claimed byte-for-byte exports from real users. Their top-level formats and storage section shapes were reviewed against the tagged `custom_components/extended_openai_conversation_responses/backup.py` and `tests/test_backup.py` sources. User content, IDs, and timestamps are deterministic fictional values.

| Fixture | Tagged source | Structural boundary |
| --- | --- | --- |
| `backup-v4.9.0-format1.json` | `v4.9.0` | Format 1, before Guest Mode and Request Rule backup sections; current restore must default them. |
| `backup-v5.2.1-format2.json` | `v5.2.1` | Format 2 with the then-supported Guest Mode schedule shape, before Request Rules became a required backup section. |
| `backup-v5.3.0-format2.json` | `v5.3.0` | Format 2 fixture already used by the enhanced suite, with Memory and Knowledge content. |

Each fixture is inspected, restored, reloaded, exercised through public Assist, exported to the current schema, and re-imported. The tests assert only fields those tagged schemas actually represented.
