"""Independent immutable revisions for workflow resources."""
from __future__ import annotations

from dataclasses import asdict, dataclass
import datetime
import hashlib
import json
import uuid
from typing import Any

from .kv_store_protocol import KVStore


RESOURCE_KINDS = frozenset({"requirement", "workflow_spec", "dag", "plan"})


class VersionConflict(RuntimeError):
    """The resource current pointer changed during publication."""


@dataclass(frozen=True)
class ResourceVersion:
    kind: str
    revision: int
    version_id: str
    checksum: str
    created_at: str
    created_by: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class VersionedResourceStore:
    """Publish JSON-compatible documents using immutable blobs and CAS pointers."""

    def __init__(self, store: KVStore):
        self.store = store

    def publish(
        self,
        req_id: str,
        kind: str,
        document: Any,
        *,
        actor: str,
        expected_revision: int | None = None,
    ) -> ResourceVersion:
        _validate_kind(kind)
        encoded = json.dumps(
            document, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        )
        pointer_key = f"workflows/{req_id}/versions/{kind}/current"
        raw_pointer, pointer_index = self.store.kv_get(pointer_key)
        current = _parse_pointer(raw_pointer)
        current_revision = int(current.get("revision", 0))
        if expected_revision is not None and current_revision != expected_revision:
            raise VersionConflict(
                f"expected {kind} revision {expected_revision}, found {current_revision}"
            )

        revision = current_revision + 1
        version_id = f"v{revision}-{uuid.uuid4().hex}"
        created_at = _now_iso()
        metadata = ResourceVersion(
            kind=kind,
            revision=revision,
            version_id=version_id,
            checksum="sha256:" + hashlib.sha256(encoded.encode("utf-8")).hexdigest(),
            created_at=created_at,
            created_by=actor,
        )
        revision_base = f"workflows/{req_id}/versions/{kind}/revisions/{version_id}"
        self.store.kv_put(f"{revision_base}/document", encoded)
        self.store.kv_put(
            f"{revision_base}/metadata",
            json.dumps(metadata.to_dict(), ensure_ascii=False, sort_keys=True),
        )
        pointer = json.dumps(metadata.to_dict(), ensure_ascii=False, sort_keys=True)
        pointer_cas = pointer_index if raw_pointer is not None else 0
        if not self.store.kv_put(pointer_key, pointer, cas=pointer_cas):
            raise VersionConflict(f"concurrent {kind} publication detected")
        return metadata

    def get_current(self, req_id: str, kind: str) -> tuple[Any | None, ResourceVersion | None]:
        _validate_kind(kind)
        raw_pointer, _ = self.store.kv_get(
            f"workflows/{req_id}/versions/{kind}/current"
        )
        pointer = _parse_pointer(raw_pointer)
        version_id = pointer.get("version_id")
        if not version_id:
            return None, None
        document_raw, _ = self.store.kv_get(
            f"workflows/{req_id}/versions/{kind}/revisions/{version_id}/document"
        )
        if document_raw is None:
            raise ValueError(f"missing immutable document for {kind} {version_id}")
        return json.loads(document_raw), ResourceVersion(**{
            field: pointer[field]
            for field in ResourceVersion.__dataclass_fields__
        })


def _validate_kind(kind: str) -> None:
    if kind not in RESOURCE_KINDS:
        raise ValueError(f"unsupported versioned resource: {kind}")


def _parse_pointer(raw: str | None) -> dict[str, Any]:
    if not raw:
        return {}
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError("invalid resource version pointer") from exc
    if not isinstance(value, dict):
        raise ValueError("invalid resource version pointer")
    return value


def _now_iso() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat().replace("+00:00", "Z")
