# Schema registry

`v1/` contains the authoritative JSON Schema 2020-12 contracts for the six core
event messages plus the Phase 2 `health/1.0` and `twin.snapshot/1.0` supplemental
messages. Every event is an immutable envelope plus a message-specific payload.

Rules:

- `$id` is stable and globally unique.
- `x-wire-schema` matches the envelope `schema` field.
- v1 minor changes may only add optional fields with safe defaults.
- incompatible field, unit or semantic changes require a new major version.
- every valid and invalid fixture is executed by the server test suite.

Validate:

```bash
obstacle-schema validate-examples
```
