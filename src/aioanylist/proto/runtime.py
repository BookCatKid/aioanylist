from __future__ import annotations

import json
from functools import cache, lru_cache
from importlib.resources import files
from types import SimpleNamespace
from typing import Any, cast

from google.protobuf import descriptor_pb2, descriptor_pool, message_factory
from google.protobuf.internal.enum_type_wrapper import EnumTypeWrapper
from google.protobuf.message import Message

PACKAGE = "pcov.proto"

_SCALARS = {
    "double": descriptor_pb2.FieldDescriptorProto.TYPE_DOUBLE,
    "float": descriptor_pb2.FieldDescriptorProto.TYPE_FLOAT,
    "int64": descriptor_pb2.FieldDescriptorProto.TYPE_INT64,
    "uint64": descriptor_pb2.FieldDescriptorProto.TYPE_UINT64,
    "int32": descriptor_pb2.FieldDescriptorProto.TYPE_INT32,
    "fixed64": descriptor_pb2.FieldDescriptorProto.TYPE_FIXED64,
    "fixed32": descriptor_pb2.FieldDescriptorProto.TYPE_FIXED32,
    "bool": descriptor_pb2.FieldDescriptorProto.TYPE_BOOL,
    "string": descriptor_pb2.FieldDescriptorProto.TYPE_STRING,
    "bytes": descriptor_pb2.FieldDescriptorProto.TYPE_BYTES,
    "uint32": descriptor_pb2.FieldDescriptorProto.TYPE_UINT32,
    "sfixed32": descriptor_pb2.FieldDescriptorProto.TYPE_SFIXED32,
    "sfixed64": descriptor_pb2.FieldDescriptorProto.TYPE_SFIXED64,
    "sint32": descriptor_pb2.FieldDescriptorProto.TYPE_SINT32,
    "sint64": descriptor_pb2.FieldDescriptorProto.TYPE_SINT64,
}

_LABELS = {
    "optional": descriptor_pb2.FieldDescriptorProto.LABEL_OPTIONAL,
    "required": descriptor_pb2.FieldDescriptorProto.LABEL_REQUIRED,
    "repeated": descriptor_pb2.FieldDescriptorProto.LABEL_REPEATED,
}


def _camel(name: str) -> str:
    return "".join(part[:1].upper() + part[1:] for part in name.split("_"))


def _load_schema() -> dict[str, Any]:
    with files(__package__).joinpath("schema.json").open("r", encoding="utf-8") as fh:
        return cast(dict[str, Any], json.load(fh))


def _add_enum(target: Any, enum: dict[str, Any]) -> None:
    out = target.enum_type.add()
    out.name = enum["name"]
    for value in enum.get("values", []):
        v = out.value.add()
        v.name = value["name"]
        v.number = int(value["id"])


def _resolve_type(
    field: descriptor_pb2.FieldDescriptorProto,
    raw_type: str,
    message_name: str,
    message_names: set[str],
    top_enums: set[str],
    nested_enums: dict[str, set[str]],
) -> None:
    scalar = _SCALARS.get(raw_type)
    if scalar is not None:
        field.type = scalar
        return
    if raw_type in nested_enums.get(message_name, set()):
        field.type = descriptor_pb2.FieldDescriptorProto.TYPE_ENUM
        field.type_name = f".{PACKAGE}.{message_name}.{raw_type}"
        return
    if raw_type in top_enums:
        field.type = descriptor_pb2.FieldDescriptorProto.TYPE_ENUM
        field.type_name = f".{PACKAGE}.{raw_type}"
        return
    if raw_type in message_names:
        field.type = descriptor_pb2.FieldDescriptorProto.TYPE_MESSAGE
        field.type_name = f".{PACKAGE}.{raw_type}"
        return
    raise ValueError(f"Unknown protobuf type {raw_type!r} in {message_name}")


@lru_cache(maxsize=1)
def descriptor_pool_for_official_schema() -> descriptor_pool.DescriptorPool:
    schema = _load_schema()
    file_proto = descriptor_pb2.FileDescriptorProto()
    file_proto.name = "anylist_official.proto"
    file_proto.package = schema.get("package", PACKAGE)
    file_proto.syntax = "proto2"

    message_names = {m["name"] for m in schema["messages"]}
    top_enums = {e["name"] for e in schema.get("enums", [])}
    nested_enums = {m["name"]: {e["name"] for e in m.get("enums", [])} for m in schema["messages"]}

    for enum in schema.get("enums", []):
        _add_enum(file_proto, enum)

    for message in schema["messages"]:
        out = file_proto.message_type.add()
        out.name = message["name"]
        for enum in message.get("enums", []):
            _add_enum(out, enum)

        for raw in message.get("fields", []):
            if raw["rule"] == "map":
                entry = out.nested_type.add()
                entry.name = _camel(raw["name"]) + "Entry"
                entry.options.map_entry = True

                key = entry.field.add()
                key.name = "key"
                key.number = 1
                key.label = descriptor_pb2.FieldDescriptorProto.LABEL_OPTIONAL
                _resolve_type(
                    key,
                    raw["keytype"],
                    message["name"],
                    message_names,
                    top_enums,
                    nested_enums,
                )

                value = entry.field.add()
                value.name = "value"
                value.number = 2
                value.label = descriptor_pb2.FieldDescriptorProto.LABEL_OPTIONAL
                _resolve_type(
                    value,
                    raw["type"],
                    message["name"],
                    message_names,
                    top_enums,
                    nested_enums,
                )

                field = out.field.add()
                field.name = raw["name"]
                field.number = int(raw["id"])
                field.label = descriptor_pb2.FieldDescriptorProto.LABEL_REPEATED
                field.type = descriptor_pb2.FieldDescriptorProto.TYPE_MESSAGE
                field.type_name = f".{PACKAGE}.{message['name']}.{entry.name}"
                continue

            field = out.field.add()
            field.name = raw["name"]
            field.number = int(raw["id"])
            field.label = _LABELS[raw["rule"]]
            _resolve_type(
                field,
                raw["type"],
                message["name"],
                message_names,
                top_enums,
                nested_enums,
            )
            default = raw.get("options", {}).get("default")
            if default is not None:
                field.default_value = (
                    str(default).lower() if isinstance(default, bool) else str(default)
                )

    pool = descriptor_pool.DescriptorPool()
    pool.Add(file_proto)
    return pool


@cache
def message_class(name: str) -> type[Message]:
    desc = descriptor_pool_for_official_schema().FindMessageTypeByName(f"{PACKAGE}.{name}")
    return message_factory.GetMessageClass(desc)


@cache
def enum_type(name: str) -> EnumTypeWrapper:
    desc = descriptor_pool_for_official_schema().FindEnumTypeByName(f"{PACKAGE}.{name}")
    return EnumTypeWrapper(desc)


def new(name: str, **values: Any) -> Message:
    msg = message_class(name)()
    for key, value in values.items():
        assign(msg, key, value)
    return msg


def assign(msg: Message, field_name: str, value: Any) -> Message:
    """Assign Python/protobuf values to a dynamic proto2 message, including repeated/map fields."""
    field = msg.DESCRIPTOR.fields_by_name[field_name]
    container = getattr(msg, field_name)
    if field.is_repeated:
        if field.message_type and field.message_type.GetOptions().map_entry:
            container.clear()
            for k, v in value.items():
                if field.message_type.fields_by_name["value"].message_type:
                    container[k].CopyFrom(v)
                else:
                    container[k] = v
        elif field.message_type:
            del container[:]
            for item in value:
                container.add().CopyFrom(item)
        else:
            del container[:]
            container.extend(value)
    elif field.message_type:
        container.CopyFrom(value)
    else:
        setattr(msg, field_name, value)
    return msg


def decode(name: str, payload: bytes) -> Message:
    msg = message_class(name)()
    msg.ParseFromString(payload)
    return msg


def encode(msg: Message) -> bytes:
    return msg.SerializeToString()


class _ProtoNamespace(SimpleNamespace):
    def __getattr__(self, name: str) -> Any:
        try:
            value: Any = message_class(name)
        except KeyError:
            try:
                value = enum_type(name)
            except KeyError as exc:
                raise AttributeError(name) from exc
        setattr(self, name, value)
        return value


PB = _ProtoNamespace()
