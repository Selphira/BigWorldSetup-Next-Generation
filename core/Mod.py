"""
Type-safe classes for representing mods and their components.

Optimized for handling 2000 mods and 15000 components with lazy instantiation
and minimal memory footprint.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Iterable, Union, cast


class ComponentType(Enum):
    """Component type classification."""
    STD = "std"  # Standard single component
    MUC = "muc"  # Mutually Exclusive Choices
    SUB = "sub"  # Component with sub-prompts
    DWN = "dwn"  # Download component

    def __str__(self) -> str:
        return self.value


@dataclass(frozen=True, slots=True)
class Prompt:
    """
    Represents a prompt in a SUB component.

    Attributes:
        key: Prompt identifier
        default: Default option key
        options: Available options for this prompt
    """
    key: str
    default: str
    options: tuple[str, ...]  # Tuple for immutability and memory efficiency

    def has_option(self, option: str) -> bool:
        """Check if an option exists in this prompt."""
        return option in self.options


@dataclass(slots=True)
class Component:
    """
    Base class for all component types.

    Attributes:
        key: Unique component identifier
        text: Translated component text
        comp_type: Component type (std, muc, sub, dwn)
        forced: Whether component is forced (always installed)
    """
    key: str
    text: str
    category: str
    comp_type: ComponentType
    games: list[str]
    mod: Mod
    forced: bool

    def get_name(self):
        return self.text

    def is_forced(self) -> bool:
        """Check if component is forced."""
        return self.forced

    def is_standard(self) -> bool:
        """Check if component is standard type."""
        return self.comp_type == ComponentType.STD

    def is_muc(self) -> bool:
        """Check if component is MUC type."""
        return self.comp_type == ComponentType.MUC

    def is_sub(self) -> bool:
        """Check if component is SUB type."""
        return self.comp_type == ComponentType.SUB

    def supports_game(self, game: str) -> bool:
        """Check if mod supports a game."""
        if self.games:
            return game in self.games

        return game in self.mod.games

    def __hash__(self) -> int:
        """Make component hashable for use in sets/dicts."""
        return hash(self.key)

    def __eq__(self, other: object) -> bool:
        """Compare components by key."""
        if not isinstance(other, Component):
            return NotImplemented
        return self.key == other.key


@dataclass(slots=True)
class MucComponent(Component):
    """
    Component with mutually exclusive choices.

    Additional attributes:
        default: Default option key
        options: List of sub-component keys
        _option_texts: Cached option texts
    """
    default: str
    options: list[str]
    _option_texts: dict[str, str]

    def get_option_text(self, option_key: str) -> str:
        """Get translated text for an option."""
        return self._option_texts.get(option_key, "")

    def get_all_options(self) -> list[str]:
        """Return all option keys (returns reference, do not modify)."""
        return self.options

    def has_option(self, option_key: str) -> bool:
        """Check if an option exists."""
        return option_key in self.options


@dataclass(slots=True)
class SubComponent(Component):
    """
    Component with sub-prompts (question/answer pairs).

    Additional attributes:
        prompts: Dictionary of available prompts
        _prompt_texts: Cached prompt texts (key: "prompt_key" or "prompt_key.option")
    """
    prompts: dict[str, Prompt]
    _prompt_texts: dict[str, str]

    def get_prompt(self, prompt_key: str) -> Prompt | None:
        """Get a prompt by its key."""
        return self.prompts.get(prompt_key)

    def get_prompt_text(self, prompt_key: str) -> str:
        """Get translated text for a prompt."""
        return self._prompt_texts.get(prompt_key, "")

    def get_prompt_option_text(self, prompt_key: str, option: str) -> str:
        """Get translated text for a prompt option."""
        cache_key = f"{prompt_key}.{option}"
        return self._prompt_texts.get(cache_key, "")

    def get_all_prompts(self) -> list[str]:
        """Return all prompt keys."""
        return list(self.prompts.keys())

    def has_prompt(self, prompt_key: str) -> bool:
        """Check if a prompt exists."""
        return prompt_key in self.prompts


@dataclass(frozen=True, slots=True)
class ModFile:
    """Represents information about a downloadable mod file."""
    filename: str
    size: int
    sha256: str

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ModFile":
        return cls(
            filename=data.get("filename", ""),
            size=int(data.get("size", 0)),
            sha256=data.get("sha256", "")
        )


class Mod:
    """
    Represents a mod with all its data and components.

    Uses lazy loading for components to optimize performance and memory usage.
    Components are only instantiated when accessed.
    """
    __slots__ = (
        'id', 'name', 'tp2', 'categories', 'games', 'languages', 'version',
        'description', 'download', 'readme', 'homepage', 'safe', 'authors',
        '_components_raw', '_translations', '_components_cache', 'file'
    )

    def __init__(self, data: dict[str, Any]) -> None:
        """
        Construct a Mod from JSON cache data.

        Args:
            data: Dictionary containing mod data from cache
        """
        # Core data (required)
        self.id: str = data.get("id", "")
        self.name: str = data.get("name", "")
        self.tp2: str = data.get("id", "")
        self.version: str = data.get("version", "")

        # Lists (convert to tuples for immutability where appropriate)
        self.games: tuple[str, ...] = tuple(data.get("games", []))
        self.languages: dict[str, int] = data.get("languages", {})
        self.authors: tuple[str, ...] = tuple(data.get("authors", {}))

        # Translations
        translations = data.get("translations", {})
        self.description: str = translations.get("description", "")
        self._translations: dict[str, str] = translations.get("components", {})

        # Optional links
        links = data.get("links")
        self.homepage: str | None = links.get("homepage")
        self.download: str | None = links.get("download")
        self.readme: str | None = links.get("readme")

        # Other
        self.safe: int = data.get("safe", 2)

        # Components (stored raw, instantiated on demand)
        self._components_raw: dict[str, Any] = data.get("components", {})
        self._components_cache: dict[str, Component] = {}

        self.categories: tuple[str, ...] = self._get_all_categories(data.get("categories", []))

        self.file: ModFile | None = ModFile.from_dict(file_data) if (file_data := data.get("file")) else None

    def get_component(self, key: str) -> Component | None:
        """
        Get a component by key (with lazy instantiation and nested component support).

        Supports:
        - Simple keys: "comp1" → Standard/MUC/SUB component
        - MUC option key: "opt_key" → Searches all MUC components to find the parent
        - SUB nested: "comp1.2.1" → Prompt 2, Option 1 of SUB component 1

        Args:
            key: Component key

        Returns:
            Component instance with appropriate text concatenation, or None if not found
        """
        # Check if it's a SUB nested key (contains dots)
        if '.' in key:
            return self._get_sub_component(key)

        # Check cache first for direct key
        if key in self._components_cache:
            return self._components_cache[key]

        # Check if it's a direct component key
        if key in self._components_raw:
            component = self._create_component(key, self._components_raw[key])
            self._components_cache[key] = component
            return component

        # Key not found directly - search in all MUC components
        return self._search_in_muc_components(key)

    def _get_sub_component(self, key: str) -> Component | None:
        """
        Handle SUB component with notation "parent.prompt_idx.option_idx".

        Args:
            key: Component key with dots (e.g., "comp1.2.1")

        Returns:
            Component with concatenated text, or None if not found
        """
        parts = key.split('.')

        if len(parts) != 3:
            return None

        parent_key = parts[0]
        prompt_idx = parts[1]
        option_idx = parts[2]

        # Get or create parent component
        if parent_key in self._components_cache:
            parent_comp = self._components_cache[parent_key]
        elif parent_key in self._components_raw:
            parent_comp = self._create_component(parent_key, self._components_raw[parent_key])
            self._components_cache[parent_key] = parent_comp
        else:
            return None

        # Verify it's a SUB component
        if not parent_comp.is_sub():
            return None

        parent_comp = cast(SubComponent, parent_comp)
        # Get all prompts to find the one at the given index
        prompt_keys = parent_comp.get_all_prompts()

        try:
            prompt_index = int(prompt_idx)
            if prompt_index < 0 or prompt_index >= len(prompt_keys):
                return None

            prompt_key = prompt_keys[prompt_index]
            prompt = parent_comp.get_prompt(prompt_key)

            if not prompt:
                return None

            # Check if option index is valid
            option_index = int(option_idx)
            if option_index < 0 or option_index >= len(prompt.options):
                return None

            option_key = prompt.options[option_index]

        except (ValueError, IndexError):
            return None

        # Get texts
        prompt_text = parent_comp.get_prompt_text(prompt_key)
        option_text = parent_comp.get_prompt_option_text(prompt_key, option_key)

        # Create a new Component with concatenated text
        # Format: "Parent text -> Prompt text -> Option text"
        combined_text = f"{parent_comp.text} -> {prompt_text} -> {option_text}"

        return Component(
            key=key,  # Use full key including prompt and option
            text=combined_text,
            category=parent_comp.category,
            comp_type=parent_comp.comp_type,
            games=parent_comp.games,
            mod=self,
            forced=parent_comp.forced
        )

    def _search_in_muc_components(self, option_key: str) -> Component | None:
        """
        Search for an option key in all MUC components.

        Args:
            option_key: The option key to search for

        Returns:
            Component with concatenated text if found in a MUC, None otherwise
        """
        # Iterate through all raw components to find MUC parents
        for parent_key, component_data in self._components_raw.items():
            comp_type_str = component_data.get("type", "std")

            # Only check MUC components
            if comp_type_str != "muc":
                continue

            # Check if this MUC contains our option
            options = component_data.get("components", [])
            if option_key not in options:
                continue

            # Found it! Get or create the parent MUC component
            if parent_key in self._components_cache:
                parent_comp = self._components_cache[parent_key]
            else:
                parent_comp = self._create_component(parent_key, component_data)
                self._components_cache[parent_key] = parent_comp

            parent_comp = cast(MucComponent, parent_comp)
            # Get option text
            option_text = parent_comp.get_option_text(option_key)

            # Create a new Component with concatenated text
            # Format: "Parent text -> Option text"
            combined_text = f"{parent_comp.text} -> {option_text}"

            # Cache with the option key for faster subsequent access
            result = Component(
                key=option_key,  # Use option key
                text=combined_text,
                category=parent_comp.category,
                comp_type=parent_comp.comp_type,
                games=parent_comp.games,
                mod=self,
                forced=parent_comp.forced
            )

            self._components_cache[option_key] = result
            return result

        # Option not found in any MUC
        return None

    def _create_component(self, key: str, raw_data: dict[str, Any]) -> Component:
        """
        Create a Component instance of the appropriate type.

        Args:
            key: Component key
            raw_data: Raw component data

        Returns:
            Appropriate Component instance
        """
        comp_type_str = raw_data.get("type", "std")
        comp_type = ComponentType(comp_type_str)

        # Common attributes
        text = self._translations.get(key, "")
        category = raw_data.get("category", "")
        forced = raw_data.get("forced", False)
        games = raw_data.get("games", [])

        # MUC Component
        if comp_type == ComponentType.MUC:
            options = raw_data.get("components", [])
            option_texts = {
                opt: self._translations.get(opt, "")
                for opt in options
            }
            default = raw_data.get("default", "")

            return MucComponent(
                key=key,
                text=text,
                category=category,
                comp_type=comp_type,
                games=games,
                mod=self,
                forced=forced,
                default=default,
                options=options,
                _option_texts=option_texts
            )

        # SUB Component
        if comp_type == ComponentType.SUB:
            prompts_raw = raw_data.get("prompts", {})

            # Build immutable Prompt objects
            prompts = {
                prompt_key: Prompt(
                    key=prompt_key,
                    options=tuple(prompt_data.get("options", [])),
                    default=prompt_data.get("default", "")
                )
                for prompt_key, prompt_data in prompts_raw.items()
            }

            # Cache prompt texts
            prompt_texts = {}
            for prompt_key, prompt_data in prompts_raw.items():
                # Prompt text
                prompt_texts[prompt_key] = self._translations.get(
                    f"{key}.{prompt_key}", ""
                )

                # Option texts
                for option in prompt_data.get("options", []):
                    full_key = f"{key}.{prompt_key}.{option}"
                    cache_key = f"{prompt_key}.{option}"
                    prompt_texts[cache_key] = self._translations.get(full_key, "")

            return SubComponent(
                key=key,
                text=text,
                category=category,
                comp_type=comp_type,
                games=games,
                mod=self,
                forced=forced,
                prompts=prompts,
                _prompt_texts=prompt_texts
            )

        # Standard Component
        return Component(
            key=key,
            text=text,
            category=category,
            comp_type=comp_type,
            games=games,
            mod=self,
            forced=forced
        )

    def _get_all_categories(self, categories: list[str]) -> tuple[str, ...]:
        """
        Get all unique categories from mod and its components.

        Combines categories defined at mod level with categories from individual
        components, returning a sorted tuple of unique category names.

        Returns:
            Sorted tuple of unique category names
        """
        # Start with mod-level categories
        all_categories = categories

        # Add component-level categories
        for component_data in self._components_raw.values():
            category = component_data.get("category")
            if category:
                all_categories.append(category)

        # Remove duplicates while preserving order, then sort
        unique_categories = dict.fromkeys(all_categories)

        return tuple(sorted(unique_categories.keys()))

    def get_component_text(self, key: str) -> str:
        """
        Quickly get component text without instantiating the object.

        Args:
            key: Component key

        Returns:
            Translated text or empty string
        """
        return self._translations.get(key, "")

    def has_component(self, key: str) -> bool:
        """Check if a component exists."""
        return key in self._components_raw

    def get_component_keys(self) -> list[str]:
        """Return all component keys."""
        return list(self._components_raw.keys())

    def get_all_components(self) -> list[Component]:
        """
        Return all components (instantiates those not yet cached).

        Returns:
            List of all components
        """
        return [
            self.get_component(key)
            for key in self._components_raw.keys()
        ]

    def get_forced_components(self) -> list[Component]:
        """Return only forced components."""
        return [
            comp for comp in self.get_all_components()
            if comp.is_forced()
        ]

    def has_category(self, category: str) -> bool:
        """Check if mod belongs to a category."""
        return category in self.categories

    def supports_game(self, game: str) -> bool:
        """Check if mod supports a game."""
        return game in self.games

    def get_language_index(self, lang_code: str) -> int | None:
        if lang_code in self.languages:
            return self.languages[lang_code]

        if "all" in self.languages:
            return self.languages["all"]

        return None

    def supports_language(self, languages: Union[str, Iterable[str]]) -> bool:
        """
        Return True if the mod supports at least one of the given languages.

        Accepts:
            - a single language code (str)
            - multiple language codes (list, set, tuple...)
        """
        if isinstance(languages, str):
            # Single language
            return languages in self.languages

        # Case: iterable of languages
        langs = list(languages)
        if not langs:  # empty iterable
            return False

        return any(lang in self.languages for lang in langs)

    def __repr__(self) -> str:
        return (
            f"Mod(id={self.id!r}, name={self.name!r}, "
            f"tp2={self.tp2!r}, components={len(self._components_raw)})"
        )

    def __str__(self) -> str:
        return f"{self.name} ({self.tp2})"

    def __hash__(self) -> int:
        """Make mod hashable for use in sets/dicts."""
        return hash(self.id)

    def __eq__(self, other: object) -> bool:
        """Compare mods by ID."""
        if not isinstance(other, Mod):
            return NotImplemented
        return self.id == other.id
