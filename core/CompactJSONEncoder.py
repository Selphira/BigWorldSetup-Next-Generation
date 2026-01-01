import json


class CompactJSONEncoder(json.JSONEncoder):
    """Custom JSON encoder with smart formatting for mod definitions."""

    # Component types that support compact formatting
    COMPACT_TYPES = {"std", "muc", "sub"}

    # Maximum keys for compact type objects
    MAX_COMPACT_KEYS = 10

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.current_indent_level = 0

    def encode(self, obj):
        """Encode object with custom formatting rules."""
        if isinstance(obj, dict):
            return self._encode_dict(obj, indent_level=0)
        return super().encode(obj)

    def _encode_dict(self, data: dict, indent_level: int) -> str:
        """Encode dictionary with appropriate indentation."""
        if not data:
            return "{}"

        if self._should_format_compact(data):
            return self._format_compact(data)

        return self._format_indented(data, indent_level)

    def _format_indented(self, data: dict, indent_level: int) -> str:
        """Format dictionary with indentation and line breaks."""
        indent = "  " * indent_level
        next_indent = "  " * (indent_level + 1)

        items = []
        for key, value in data.items():
            key_str = json.dumps(key)
            value_str = self._encode_value(value, indent_level + 1)
            items.append(f"{next_indent}{key_str}: {value_str}")

        return "{\n" + ",\n".join(items) + f"\n{indent}}}"

    def _encode_value(self, value, indent_level: int) -> str:
        """Encode a value based on its type."""
        if isinstance(value, dict):
            if self._should_format_compact(value):
                return self._format_compact(value)
            return self._encode_dict(value, indent_level)

        if isinstance(value, list):
            return self._format_list(value)

        return json.dumps(value, ensure_ascii=False)

    def _format_compact(self, data: dict) -> str:
        """Format dictionary as compact single-line JSON."""
        items = []
        for key, value in data.items():
            value_str = (
                self._format_list(value)
                if isinstance(value, list)
                else json.dumps(value, ensure_ascii=False)
            )
            items.append(f'"{key}": {value_str}')

        return "{" + ", ".join(items) + "}"

    @staticmethod
    def _format_list(items: list) -> str:
        """Format list as compact single-line JSON."""
        if not items:
            return "[]"

        encoded_items = [json.dumps(item, ensure_ascii=False) for item in items]
        return "[" + ", ".join(encoded_items) + "]"

    def _should_format_compact(self, data: dict) -> bool:
        """Determine if dictionary should use compact formatting."""
        keys = set(data.keys())

        # Single-key dictionaries with specific keys
        if keys in ({"type"}, {"options"}, {"components"}):
            return True

        # Component type objects with reasonable number of keys
        if "type" in keys:
            component_type = data.get("type")
            if component_type in self.COMPACT_TYPES and len(data) <= self.MAX_COMPACT_KEYS:
                return True

        return False
