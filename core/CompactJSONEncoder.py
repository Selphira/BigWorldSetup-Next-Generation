import json
from typing import Any, Tuple

Scalar = (str, int, float, bool, type(None))


class CompactJSONEncoder:
    """Custom JSON encoder with smart formatting for mod definitions."""

    def __init__(self, indent: int = 2, ensure_ascii: bool = False):
        self.indent = indent
        self.ensure_ascii = ensure_ascii

    def encode(self, obj: Any) -> str:
        """Encode object with custom formatting rules."""
        return self._encode(obj, path=(), level=0)

    def _encode(self, obj: Any, path: Tuple[str, ...], level: int) -> str:
        """Encode object with custom formatting rules."""
        if isinstance(obj, dict):
            return self._encode_dict(obj, path, level)

        if isinstance(obj, list):
            return self._encode_list(obj, path, level)

        return json.dumps(obj, ensure_ascii=self.ensure_ascii)

    def _encode_dict(self, data: dict, path: Tuple[str, ...], level: int) -> str:
        """Encode dictionary with appropriate indentation."""
        if not data:
            return "{}"

        indent = " " * self.indent * level
        next_indent = " " * self.indent * (level + 1)

        items = []

        for key, value in data.items():
            key_json = json.dumps(key, ensure_ascii=self.ensure_ascii)

            # Special case: root-level components
            if path == () and key == "components" and isinstance(value, dict):
                value_json = self._encode_components(value, level + 1)
            else:
                value_json = self._encode(value, path + (key,), level + 1)

            items.append(f"{next_indent}{key_json}: {value_json}")

        return "{\n" + ",\n".join(items) + f"\n{indent}}}"

    def _encode_list(self, data: list, path: Tuple[str, ...], level: int) -> str:
        if not data:
            return "[]"

        # Scalar lists are always compact
        if self._is_scalar_list(data):
            return (
                "["
                + ", ".join(json.dumps(v, ensure_ascii=self.ensure_ascii) for v in data)
                + "]"
            )

        indent = " " * self.indent * level
        next_indent = " " * self.indent * (level + 1)

        items = [self._encode(item, path, level + 1) for item in data]

        return "[\n" + ",\n".join(f"{next_indent}{i}" for i in items) + f"\n{indent}]"

    def _encode_components(self, components: dict, level: int) -> str:
        indent = " " * self.indent * level
        next_indent = " " * self.indent * (level + 1)

        items = []
        for key, component in components.items():
            key_json = json.dumps(key, ensure_ascii=self.ensure_ascii)
            component_json = json.dumps(component, ensure_ascii=self.ensure_ascii)
            items.append(f"{next_indent}{key_json}: {component_json}")

        return "{\n" + ",\n".join(items) + f"\n{indent}}}"

    @staticmethod
    def _is_scalar_list(values: list) -> bool:
        return all(isinstance(v, Scalar) for v in values)
