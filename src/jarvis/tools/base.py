"""A tool is a name, a JSON schema, and a function that returns text."""

from __future__ import annotations


class Tool:
    name: str
    description: str
    parameters: dict

    def schema(self) -> dict:
        return {
            "type": "function",
            "name": self.name,
            "description": self.description,
            "parameters": self.parameters,
        }

    def run(self, arguments: dict) -> str:
        raise NotImplementedError


def object_schema(properties: dict | None = None, required: list[str] | None = None) -> dict:
    return {
        "type": "object",
        "properties": properties or {},
        "required": required or [],
        "additionalProperties": False,
    }
