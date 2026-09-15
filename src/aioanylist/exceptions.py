from __future__ import annotations


class AnyListError(Exception):
    """Base SDK exception."""


class AuthenticationError(AnyListError):
    """Authentication or token refresh failed."""


class PermissionDeniedError(AnyListError):
    """The server rejected the operation due to permissions/subscription state."""


class ProtocolError(AnyListError):
    """The server response did not match the official client protocol."""


class TransportError(AnyListError):
    """Network transport failure."""


class NotModifiedError(TransportError):
    """HTTP 304 response from a timestamped AnyList read."""


class SyncError(AnyListError):
    """Synchronization or operation acknowledgement failure."""


class TagDataError(AnyListError):
    """Official tag-data resource could not be loaded or validated."""


class VisualDataError(AnyListError):
    """Official visual metadata resource could not be loaded or validated."""
