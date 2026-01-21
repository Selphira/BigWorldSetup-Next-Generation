"""
Type-safe classes for representing mods and their components.

Optimized for handling 2000 mods and 15000 components with lazy instantiation
and minimal memory footprint.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Iterable, Union, cast

from core.Platform import Platform


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
    """

    key: str
    text: str
    category: str
    comp_type: ComponentType
    games: list[str]
    mod: Mod

    def get_name(self):
        return self.text

    def is_standard(self) -> bool:
        """Check if component is standard type."""
        return self.comp_type == ComponentType.STD

    def is_muc(self) -> bool:
        """Check if component is MUC type."""
        return self.comp_type == ComponentType.MUC

    def is_sub(self) -> bool:
        """Check if component is SUB type."""
        return self.comp_type == ComponentType.SUB

    def is_dwn(self) -> bool:
        """Check if component is DWN type."""
        return self.comp_type == ComponentType.DWN

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
    components: dict[str, Component]
    _option_texts: dict[str, str]
    _option_texts: dict[str, str]

    def get_option_text(self, option_key: str) -> str:
        """Get translated text for an option."""
        return self._option_texts.get(option_key, "")

    def get_options(self) -> list[str]:
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

    def has_prompt(self, prompt_key: str) -> bool:
        """Check if a prompt exists."""
        return prompt_key in self.prompts


@dataclass(frozen=True, slots=True)
class ModFile:
    """Represents information about a downloadable mod file."""

    filename: str
    size: int
    sha256: str | None
    download: str | None
    platforms: tuple[str, ...] = ("windows", "linux", "macos")

    def supports_platform(self, platform: str) -> bool:
        """Check if this file supports the given platform."""
        return platform in self.platforms

    def has_download_url(self) -> bool:
        """Check if file has a download URL."""
        return self.download is not None and self.download != ""

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ModFile":
        platforms = data.get("platforms")
        if platforms is None:
            platforms = ["windows", "linux", "macos"]

        return cls(
            filename=data.get("filename", ""),
            size=int(data.get("size", 0)),
            sha256=data.get("sha256"),
            download=data.get("download"),
            platforms=tuple(platforms),
        )


class Mod:
    """
    Represents a mod with all its data and components.

    Uses lazy loading for components to optimize performance and memory usage.
    Components are only instantiated when accessed.
    """

    __slots__ = (
        "id",
        "name",
        "tp2",
        "categories",
        "games",
        "languages",
        "version",
        "description",
        "readme",
        "homepage",
        "forums",
        "safe",
        "authors",
        "_components_raw",
        "_translations",
        "_components_cache",
        "files",
    )

    def __init__(self, data: dict[str, Any]) -> None:
        """
        Construct a Mod from JSON cache data.

        Args:
            data: Dictionary containing mod data from cache
        """
        # Core data (required)
        self.id: str = data.get("id", "").lower()
        self.name: str = data.get("name", "")
        self.tp2: str = data.get("tp2", "").lower()
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
        self.readme: str | None = links.get("readme")
        self.forums: tuple[str, ...] = tuple(links.get("forums", []))

        # Other
        self.safe: int = data.get("safe", 2)

        # Components (stored raw, instantiated on demand)
        self._components_raw: dict[str, Any] = data.get("components", {})
        self._components_cache: dict[str, Component] = {}

        self.categories: tuple[str, ...] = self._get_all_categories(data.get("categories", []))

        self.files: list[ModFile] = [ModFile.from_dict(f) for f in data.get("files", [])]

        self._create_components()

    def get_file_for_platform(self, platform: str) -> ModFile | None:
        """Get the first compatible file for the given platform."""
        for file in self.files:
            if file.supports_platform(platform):
                return file
        return None

    def get_all_files_for_platform(self, platform: str) -> list[ModFile]:
        """Get all compatible files for the given platform."""
        return [f for f in self.files if f.supports_platform(platform)]

    def has_file(self) -> bool:
        """Check if mod has at least one file."""
        return len(self.files) > 0

    def get_download_url(self, platform: str | None = None) -> str | None:
        """Get download URL for the mod, optionally filtered by platform."""
        if not self.files:
            return None

        if platform is None:
            platform = Platform.get_current()

        file = self.get_file_for_platform(platform)

        if file and file.has_download_url():
            return file.download

        return None

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
        if "." in key:
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
        Handle SUB component with notation "parent.option1.option2...optionN".

        Each number after the parent key represents the selected option index
        for each prompt in order. There must be at least one prompt.

        Examples:
            "1.2.4" → Component 1, prompt 1 = option 2, prompt 2 = option 4
            "5.0.1.3" → Component 5, prompt 1 = option 0, prompt 2 = option 1, prompt 3

        Args:
            key: Component key with dots (e.g., "comp1.2.1")

        Returns:
            Component with concatenated text, or None if not found
        """
        parts = key.split(".")

        if len(parts) < 2:
            return None

        parent_key = parts[0]
        option_values = parts[1:]  # All remaining parts are option VALUES (not indices)

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

        # Get all prompts in order
        prompt_keys = list(parent_comp.prompts.keys())

        # Verify we have the right number of values for the number of prompts
        if len(option_values) != len(prompt_keys):
            return None

        # Build the combined text: "Parent -> Prompt1: Option1 -> Prompt2: Option2 -> ..."
        text_parts = [parent_comp.text]

        for prompt_idx, option_value in enumerate(option_values):
            # Get the prompt at this index
            prompt_key = prompt_keys[prompt_idx]
            prompt = parent_comp.get_prompt(prompt_key)

            if not prompt:
                return None

            # Verify the option value exists in this prompt's options
            if not prompt.has_option(option_value):
                return None

            # Get texts
            prompt_text = parent_comp.get_prompt_text(prompt_key)
            option_text = parent_comp.get_prompt_option_text(prompt_key, option_value)

            # Add to combined text
            text_parts.append(f"{prompt_text}: {option_text}")

        # Combine all parts with " -> "
        combined_text = " -> ".join(text_parts)

        return Component(
            key=key,  # Use full key including all option values
            text=combined_text,
            category=parent_comp.category,
            comp_type=parent_comp.comp_type,
            games=parent_comp.games,
            mod=self,
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
            )

            self._components_cache[option_key] = result
            return result

        # Option not found in any MUC
        return None

    def _create_components(self) -> None:
        """
        Create all root components and fully build component indexes.
        """
        if self._components_cache:
            return

        for key in self._components_raw.keys():
            self.get_component(key)

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
        games = raw_data.get("games", [])

        # MUC Component
        if comp_type == ComponentType.MUC:
            options = raw_data.get("components", [])
            option_texts = {opt: self._translations.get(opt, "") for opt in options}
            default = raw_data.get("default", "")
            components = {}

            for option in options:
                components[option] = self._create_component(
                    option,
                    {
                        "type": "std",
                        "category": category,
                        "games": games,
                    },
                )

            return MucComponent(
                key=key,
                text=text,
                category=category,
                comp_type=comp_type,
                games=games,
                mod=self,
                default=default,
                options=options,
                components=components,
                _option_texts=option_texts,
            )

        # SUB Component
        if comp_type == ComponentType.SUB:
            prompts_raw = raw_data.get("prompts", {})

            # Build immutable Prompt objects
            prompts = {
                prompt_key: Prompt(
                    key=prompt_key,
                    options=tuple(prompt_data.get("options", [])),
                    default=prompt_data.get("default", ""),
                )
                for prompt_key, prompt_data in prompts_raw.items()
            }

            # Cache prompt texts
            prompt_texts = {}
            for prompt_key, prompt_data in prompts_raw.items():
                # Prompt text
                prompt_texts[prompt_key] = self._translations.get(f"{key}.{prompt_key}", "")

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
                prompts=prompts,
                _prompt_texts=prompt_texts,
            )

        # Standard Component
        return Component(
            key=key,
            text=text,
            category=category,
            comp_type=comp_type,
            games=games,
            mod=self,
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

    def get_components(self) -> list[Component]:
        """
        Return all components (instantiates those not yet cached).

        Returns:
            List of all components
        """
        return [self.get_component(key) for key in self._components_raw.keys()]

    def has_category(self, category: str) -> bool:
        """Check if mod belongs to a category."""
        return category in self.categories

    def supports_game(self, game: str) -> bool:
        """Check if mod supports a game."""
        return game in self.games

    def get_language_index(self, lang_codes: str | Iterable[str]) -> int | None:
        if isinstance(lang_codes, str):
            lang_codes = (lang_codes,)

        for lang_code in lang_codes:
            if lang_code in self.languages:
                return self.languages[lang_code]

        return self.languages.get("all")

    def supports_language(self, languages: Union[str, Iterable[str]]) -> bool:
        """
        Return True if the mod supports at least one of the given languages.

        Accepts:
            - a single language code (str)
            - multiple language codes (list, set, tuple...)
        """
        if "all" in self.languages:
            return True

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
