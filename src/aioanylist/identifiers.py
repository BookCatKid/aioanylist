from __future__ import annotations

from uuid import UUID, uuid4, uuid5


def uuid4_hex() -> str:
    """Return the compact 32-hex UUID form emitted by AnyList Web's bundled uuid4()."""
    return uuid4().hex


def uuid5_hex(name: str, namespace: str | UUID) -> str:
    """Return AnyList Web-compatible UUIDv5 output (compact lowercase 32-hex)."""
    ns = namespace if isinstance(namespace, UUID) else UUID(namespace)
    return uuid5(ns, name).hex
