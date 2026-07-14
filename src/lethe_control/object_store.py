from __future__ import annotations

import hashlib
import os
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class StoredObject:
    content_ref: str
    kind: str
    storage_id: str
    payload: bytes


class LocalObjectStore:
    """Application-addressed source/chunk payload store.

    Caller values are hashed before becoming paths. `content_ref` resolution is
    constrained to this root, so event payloads can never become filesystem paths.
    """

    def __init__(self, root: Path) -> None:
        self.root = root.resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def _digest(*parts: str) -> str:
        value = "\0".join(parts).encode("utf-8")
        return hashlib.sha256(value).hexdigest()

    def _scope_dir(self, tenant_id: str, workspace_id: str, environment_id: str) -> Path:
        return self.root / self._digest(tenant_id, workspace_id, environment_id)[:24]

    def _payload_path(
        self,
        tenant_id: str,
        workspace_id: str,
        environment_id: str,
        kind: str,
        storage_id: str,
    ) -> Path:
        scope = self._scope_dir(tenant_id, workspace_id, environment_id)
        kind_dir = self._digest(kind)[:16]
        filename = f"{self._digest(storage_id)}.payload"
        return scope / kind_dir / filename

    def put(
        self,
        *,
        tenant_id: str,
        workspace_id: str,
        environment_id: str,
        kind: str,
        storage_id: str,
        payload: bytes,
    ) -> str:
        path = self._payload_path(tenant_id, workspace_id, environment_id, kind, storage_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".tmp")
        temporary.write_bytes(payload)
        os.replace(temporary, path)
        return self._to_ref(path)

    def content_ref_for(
        self,
        *,
        tenant_id: str,
        workspace_id: str,
        environment_id: str,
        kind: str,
        storage_id: str,
    ) -> str:
        return self._to_ref(
            self._payload_path(tenant_id, workspace_id, environment_id, kind, storage_id)
        )

    def _to_ref(self, path: Path) -> str:
        return f"local://objects/{path.relative_to(self.root).as_posix()}"

    def _resolve_ref(self, content_ref: str) -> Path:
        prefix = "local://objects/"
        if not content_ref.startswith(prefix):
            raise ValueError("unregistered content reference")
        relative = content_ref.removeprefix(prefix)
        candidate = (self.root / relative).resolve()
        if candidate != self.root and self.root not in candidate.parents:
            raise ValueError("content reference escapes object store")
        return candidate

    def get(self, content_ref: str) -> bytes:
        return self._resolve_ref(content_ref).read_bytes()

    def exists(self, content_ref: str) -> bool:
        return self._resolve_ref(content_ref).is_file()

    def delete(self, content_ref: str) -> bool:
        path = self._resolve_ref(content_ref)
        if not path.exists():
            return False
        path.unlink()
        return True

    def inventory(
        self,
        *,
        tenant_id: str,
        workspace_id: str,
        environment_id: str,
    ) -> Iterable[tuple[str, bytes]]:
        scope = self._scope_dir(tenant_id, workspace_id, environment_id)
        if not scope.exists():
            return ()
        return tuple(
            (self._to_ref(path), path.read_bytes())
            for path in sorted(scope.rglob("*.payload"))
            if path.is_file()
        )
