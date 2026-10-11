"""Job-flow secret references and API-output redaction."""
from __future__ import annotations

import json
import re
from typing import Any

from .api_errors import APIError
from .kv_store_protocol import KVStore


SECRET_REFERENCE_PATTERN = re.compile(r"(?:\$ref:)?secret:([A-Za-z0-9][A-Za-z0-9_.-]*)")
SECRET_REFERENCE = r"^(?:\$ref:)?secret:([A-Za-z0-9][A-Za-z0-9_.-]*)$"


def parse_secret_reference(value: Any) -> str | None:
    """Return the secret name when value is a canonical reference."""
    if not isinstance(value, str):
        return None
    match = re.match(SECRET_REFERENCE, value)
    return match.group(1) if match else None


def redact_text(value: str, replacements: dict[str, str] | None = None) -> str:
    for plaintext, reference in (replacements or {}).items():
        if plaintext:
            value = value.replace(plaintext, reference)
    return value


def redact_value(value: Any, replacements: dict[str, str] | None = None) -> Any:
    """Return an API-safe copy while preserving non-secret structure."""
    reference = parse_secret_reference(value)
    if reference:
        return value
    if isinstance(value, dict):
        declared = value.get("$ref") or value.get("secret_ref") or value.get("reference")
        name = parse_secret_reference(declared)
        if name:
            return f"$ref:secret:{name}"
        return {
            key: redact_value(item, replacements)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [redact_value(item, replacements) for item in value]
    if isinstance(value, str):
        return redact_text(value, replacements)
    return value


class SecretStore:
    """Persist material separately from instance context and return references."""

    PREFIX = "secrets/jobflows/instances"

    def __init__(self, store: KVStore):
        self.store = store

    def _key(self, instance_id: str, name: str) -> str:
        return f"{self.PREFIX}/{instance_id}/{name}"

    def put(self, instance_id: str, name: str, value: str) -> None:
        if not instance_id or "/" in instance_id or chr(92) in instance_id:
            raise APIError("INSTANCE_ID_INVALID", "实例标识无效", 422)
        self.store.kv_put(self._key(instance_id, name), value)

    def get(self, instance_id: str, name: str) -> str | None:
        value, _ = self.store.kv_get(self._key(instance_id, name))
        return value if isinstance(value, str) else None

    def references(self, instance_id: str) -> list[str]:
        return [
            f"$ref:secret:{name}" for name in sorted(self.names(instance_id))
        ]

    def names(self, instance_id: str) -> set[str]:
        prefix = f"{self.PREFIX}/{instance_id}/"
        items, cursor = self.store.kv_list(prefix, limit=1000)
        names: set[str] = set()
        while items:
            for item in items:
                key = item.get("key", item.get("Key", ""))
                if key.startswith(prefix) and "/" not in key[len(prefix):]:
                    names.add(key[len(prefix):])
            if cursor is None:
                break
            items, cursor = self.store.kv_list(prefix, cursor=cursor, limit=1000)
        return names

    def list_all(self) -> list[tuple[str, str, str]]:
        items: list[dict] = []
        cursor = None
        while True:
            page, cursor = self.store.kv_list(self.PREFIX, cursor=cursor, limit=1000)
            items.extend(page)
            if cursor is None:
                break
        result: list[tuple[str, str, str]] = []
        for item in items:
            key = item.get("key", item.get("Key", ""))
            if not key.startswith(f"{self.PREFIX}/"):
                continue
            parts = key.split("/")
            if len(parts) != 5:
                continue
            value = item.get("value", item.get("_decoded", ""))
            if isinstance(value, str):
                result.append((parts[3], parts[4], value))
        return result

    def _replacements(self, instance_id: str | None = None) -> dict[str, str]:
        return {
            value: f"$ref:secret:{name}"
            for scoped, name, value in self.list_all()
            if instance_id is None or scoped == instance_id
        }

    def redact(self, instance_id: str, payload: Any) -> Any:
        return redact_value(payload, self._replacements(instance_id))

    def redact_payload(self, payload: Any) -> Any:
        return redact_value(payload, self._replacements())

    def resolve(self, instance_id: str, value: Any) -> Any:
        name = parse_secret_reference(value)
        if name:
            return self.get(instance_id, name)
        if isinstance(value, dict):
            declared = value.get("$ref") or value.get("secret_ref") or value.get("reference")
            name = parse_secret_reference(declared)
            if name:
                return self.get(instance_id, name)
        return value

    def normalize_parameters(
        self, instance_id: str, parameters: Any, schema: Any = None,
    ) -> dict:
        if parameters is None:
            return {}
        if not isinstance(parameters, dict):
            raise APIError("PARAMETERS_INVALID", "参数必须是对象", 422)
        properties = schema.get("properties", {}) if isinstance(schema, dict) else {}
        normalized: dict[str, Any] = {}
        for field, raw in parameters.items():
            definition = properties.get(field, {}) if isinstance(properties, dict) else {}
            sensitive = any(definition.get(key) for key in ("secret", "sensitive"))
            reference = parse_secret_reference(raw)
            if isinstance(raw, dict):
                declared = raw.get("$ref") or raw.get("secret_ref") or raw.get("reference")
                reference = parse_secret_reference(declared)
                if reference is None:
                    raise APIError(
                        "SECRET_REFERENCE_REQUIRED",
                        f"参数 {field} 的引用值必须包含有效的 Secret Reference",
                        422,
                        {"location": {"field": field}},
                    )
                plaintext = raw.get("value", raw.get("secret_value"))
            else:
                plaintext = raw
            if sensitive and plaintext not in {None, ""} and reference is None:
                raise APIError(
                    "SECRET_REFERENCE_REQUIRED",
                    f"敏感参数 {field} 必须使用 Secret Reference",
                    422,
                    {"location": {"field": field}},
                )
            if reference is None:
                normalized[field] = raw
                continue
            if plaintext not in {None, ""}:
                if not isinstance(plaintext, str):
                    raise APIError(
                        "SECRET_VALUE_INVALID",
                        f"Secret 值 {field} 必须是字符串",
                        422,
                        {"location": {"field": field}},
                    )
                self.put(instance_id, reference, plaintext)
            normalized[field] = f"$ref:secret:{reference}"
        return normalized


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
