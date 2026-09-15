from .runtime import PB, assign, decode, encode, message_class, new

__all__ = ["PB", "assign", "decode", "encode", "message_class", "new"]


def __getattr__(name: str):
    """Expose official dynamic message/enum classes as normal module attributes.

    Runtime classes still come from the embedded schema; the generated ``__init__.pyi`` gives
    static analyzers the exact field surface for those same names.
    """
    return getattr(PB, name)
