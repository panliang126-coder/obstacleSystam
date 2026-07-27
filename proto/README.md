# protobuf v1

These definitions map the six core JSON wire contracts and the Phase 2 Health/Twin
Snapshot contracts to protobuf without changing domain semantics.

Compatibility rules:

- field numbers are permanent;
- removed numbers/names must be `reserved`;
- enum value `0` is always `*_UNSPECIFIED`;
- new fields are optional/repeated and require safe defaults;
- JSON Schema `schema` remains explicit in `EventMetadata`;
- the server contract suite compiles every `.proto` file.
