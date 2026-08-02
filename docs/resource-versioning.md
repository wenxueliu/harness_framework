# Independent Resource Versioning

Harness versions four workflow resources independently: `requirement`,
`workflow_spec`, `dag`, and `plan`. Updating one resource does not increment or
replace any other resource's current revision.

Each publication writes an immutable JSON document and metadata first, then
moves that resource's current pointer with CAS:

```text
workflows/<req_id>/versions/<kind>/
├── current
└── revisions/<version_id>/
    ├── document
    └── metadata
```

Metadata contains the resource kind, monotonic revision, unique version ID,
SHA-256 checksum, creator, and UTC creation timestamp. Version IDs combine the
logical revision with a UUID, preventing concurrent publishers from overwriting
one another's immutable candidate. A losing CAS publication can leave an
unreferenced candidate, but it cannot corrupt the selected version.

Callers may supply `expected_revision` for optimistic concurrency. A mismatch
or concurrent pointer update raises `VersionConflict`; callers must reload and
recompute rather than silently overwriting a newer resource.

`sync_to_consul.py` publishes revision 1 for all four resources. During the
migration period it also writes the legacy `dependencies`, task metadata, and
workflow metadata keys as compatibility projections. New code should use
`VersionedResourceStore` for authoritative version reads and publications.
