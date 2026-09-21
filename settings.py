"""Helpers for viewing and editing FinMan JSON settings files.

The Settings page treats each JSON file generically:
- SETTINGS_FILES defines the supported files and which sections are editable.
- The navigation is built from the JSON structure; only selected keys are expanded.
- Display formatting is kept separate from save formatting so the viewer can be
  compact without changing how configuration files are written.
"""
import json
from pathlib import Path

from fastapi import HTTPException

from ingestion.json_io import read_json
from ingestion.config import load_config, FinManConfig

BASE_DIR = Path(__file__).resolve().parent

# ------------------------------------------------------------
# Generic JSON Settings
# ------------------------------------------------------------
# Central definition of the JSON files exposed by the Settings page.
# "editable" controls which top-level sections can be changed from the UI.
SETTINGS_FILES = {
    "config": {
        "title": "config.json",
        "path": Path("config.json"),
        "editable": {"frontend", "ai", "external_links"},
        "validate_config": True,
        "reload_config": True,
    },

    "config_dev": {
        "title": "config.dev.json",
        "path": Path("config.dev.json"),
        "editable": {"server", "datasets", "consolidateddata"},
        "validate_config": True,
        "reload_config": True,
    },

    "config_prod": {
        "title": "config.prod.json",
        "path": Path("config.prod.json"),
        "editable": {"server", "datasets", "consolidateddata"},
        "validate_config": True,
        "reload_config": True,
    },

    "mf_master": {
        "title": "mf_master.json",
        "path": Path("static") / "mf_master.json",
        "editable": set(),
        "validate_config": False,
        "reload_config": False,
    },
}

# Only these top-level sections are expanded into child navigation items.
# This keeps the left navigation useful without recursively displaying every key.
SETTINGS_EXPAND_KEYS = {"datasets", "funds"}

# Keys whose values must never be shown in clear text on the Settings page.
MASKED_KEYWORDS = {"api_key", "password", "secret"}
MASKED_VALUE = "********"   # The value to be displayed once masked

# How much JSON is kept on one line in the viewer.
# Optimize between desktop and mobile view
DISPLAY_INLINE_LENGTH = 100  

def mask_secrets(value):
    # If it is a dictionary, walk through every key/value.
    # If the key is a MASKED_KEYWORDS then callmask_secrets
    if isinstance(value, dict):
        return {
            key: (
                MASKED_VALUE
                if key in MASKED_KEYWORDS
                else mask_secrets(item)
            )
            for key, item in value.items()
        }

    # Similar in case of a list, walk through every item
    if isinstance(value, list):
        return [mask_secrets(item) for item in value]

    # return as-is if its  a string, number, boolean, etc. i.e mnot a dict or list
    return value

def format_settings_for_display(value, level=0):
    """
    Format JSON data compactly for the Settings viewer.
    Short dictionaries and lists remain on one line. Larger structures are expanded only when necessary.
    """

    # The viewer uses a shorter threshold than the file writer so that
    # simple values remain readable while larger structures are expanded.
    compact = json.dumps(value, ensure_ascii=False, separators=(", ", ": ") )

    # Keep short values compact.
    if len(compact) <= DISPLAY_INLINE_LENGTH:
        return compact

    indent = "  " * level
    child_indent = "  " * (level + 1)

    if isinstance(value, dict):
        lines = ["{"]

        items = list(value.items())

        for index, (key, item) in enumerate(items):
            rendered = format_settings_for_display(item, level + 1)
            item_lines = rendered.splitlines()    # splitlines splits the dict into separate strings that be assembled later

            lines.append(
                f'{child_indent}{json.dumps(str(key), ensure_ascii=False)}: '
                f'{item_lines[0]}'
            )    # The first line get the key

            lines.extend(item_lines[1:])

            if index < len(items) - 1:   # Add a comma to every item except the last one.
                lines[-1] += ","

        lines.append(f"{indent}}}")
        return "\n".join(lines)

    if isinstance(value, list):
        lines = ["["]

        for index, item in enumerate(value):
            rendered = format_settings_for_display(item, level + 1)
            item_lines = rendered.splitlines()   # splitlines splits the list into separate strings that be assembled later

            lines.append(f"{child_indent}{item_lines[0]}")
            lines.extend(item_lines[1:])

            if index < len(value) - 1:
                lines[-1] += ","

        lines.append(f"{indent}]")
        return "\n".join(lines)

    return compact

def get_settings_file(file_key):
    """Return the settings-file definition for the selected JSON file."""
    try:
        return SETTINGS_FILES[file_key]
    except KeyError:
        raise ValueError("Settings file not found.")

def read_settings_file(file_key):
    """Read the selected JSON settings file."""
    settings_file = get_settings_file(file_key)
    return read_json(settings_file["path"], default={})

def get_settings_navigation(data):
    """
    Build the left-hand navigation.

    Top-level keys are shown.
    Keys listed in SETTINGS_EXPAND_KEYS also expose their children.
    """
    navigation = []

    # Preserve the JSON file's top-level key order in the navigation.
    for key, value in data.items():
        item = {
            "key": key,
            "title": key.replace("_", " ").title(),    # Replace "_" with spaces, title capitalizes the first letter of each word
            "children": [],
        }

        if key in SETTINGS_EXPAND_KEYS and isinstance(value, dict):
            item["children"] = [
                {
                    "key": f"{key}.{child_key}",
                    "title": child_key.replace("_", " ").title(),
                }
                for child_key in value
            ]

        navigation.append(item)

    return navigation

def get_settings_section(data, section):
    """Return a top-level or nested settings section."""
    value = data

    for key in section.split("."):
        if not isinstance(value, dict) or key not in value:
            raise HTTPException(status_code=404, detail="Settings section not found.")
        value = value[key]

    return value

def restore_masked_values(original, updated):
    """
    Restore existing secret values when the Settings editor submits
    the masked placeholder instead of a real secret.
    """
    if isinstance(original, dict) and isinstance(updated, dict):
        for key, value in updated.items():
            if key in MASKED_KEYWORDS and value == MASKED_VALUE:
                if key in original:
                    updated[key] = original[key]
            elif key in original:
                restore_masked_values(original[key], value)

    elif isinstance(original, list) and isinstance(updated, list):
        for original_item, updated_item in zip(original, updated):
            restore_masked_values(original_item, updated_item)

def save_settings_section(file_key, section, section_data, config):
    """Save an editable section of a JSON settings file.

    The selected section is validated first, then written back to the original JSON file. Config files are 
    reloaded afterwards so the running application receives the validated configuration object.
    """
    settings_file = get_settings_file(file_key)

    if section not in settings_file["editable"]:
        raise ValueError("This configuration section cannot be edited.")

    file_path = settings_file["path"]

    # --------------------------------------------------------
    # FinMan config files
    # The actual JSON file may not be identical to config. config could have been assembled for e.g. as 
    # config.json + confg.dev.json. So here we take config.nodel.dump for validation. 
    # We still want to validate the changed section not with just the file it belongs but to the entire FinMan configuration
    # --------------------------------------------------------
    if settings_file["validate_config"]:

        proposed_config = config.model_dump(mode="json", by_alias=True)   # This will be used validate

        original_section = get_settings_section(proposed_config, section)

        restore_masked_values(original_section, section_data)

        proposed_config[section] = section_data

        FinManConfig.model_validate(proposed_config)

    # --------------------------------------------------------
    # Read current file and replace the selected section
    # Once the validation suceeds we again read only the specific file and update it there
    # --------------------------------------------------------
    file_data = read_json(file_path, default={})

    # Support future nested sections such as datasets.stocks.
    target = file_data

    path_parts = section.split(".")

    for key in path_parts[:-1]:
        if key not in target or not isinstance(target[key], dict):
            target[key] = {}
        target = target[key]

    target[path_parts[-1]] = section_data

    # --------------------------------------------------------
    # Atomic save
    # --------------------------------------------------------
    # Write to a temporary file first, then replace the original. This avoids
    # leaving a partially written JSON file if the write is interrupted.
    # --------------------------------------------------------    
    temp_file = file_path.with_suffix(".tmp")
    temp_file.write_text(_format_config_json(file_data) + "\n", encoding="utf-8")
    temp_file.replace(file_path)

    # Reload the FinMan configuration only for config files.
    if settings_file["reload_config"]:
        return load_config(BASE_DIR)

    return config

def _format_config_json(data):
    """Format config JSON compactly where values are small and simple."""
    COMPACT_MAX_LENGTH = 100  # If a nested structure can fit in approximately 100 characters, keep it on one line.

    # Return FALSE if it is a dictionary or list i.e what we call a scalar
    # "Ticker" → True, ["RSN"] → False, {"a": "b"} → False
    def is_scalar(value):
        return not isinstance(value, (dict, list))

    # Wrapper around Python's existing JSON encoder. Add space between mentioned seperators
    # between items: , between key and value: : + space
    # ensure_ascii=False, asks python not to represent Unicode characters using escaped sequences.
    def compact_json(value):
        return json.dumps(value, ensure_ascii=False, separators=(",", ": "))

    # Checks if I wrote this entire thing on one line, would it still be reasonably short?
    # level is the depth of indentation (starting at 0)
    def should_compact(value, level):
        # Never compact the root object.
        if level == 0:
            return False
        # Scalars are naturally compact.
        if is_scalar(value):
            return True
        # Try the entire object/list in compact form.
        compact = compact_json(value)
        # Keep short structures on one line.
        return len(compact) <= COMPACT_MAX_LENGTH

    def format_value(value, level=0):
        indent = "  " * level  # Indentation for the current level increases with level number
        next_indent = "  " * (level + 1)  # Indentation for the next level is +1 to the previous level
        # Scalars
        if is_scalar(value):
            return json.dumps(value, ensure_ascii=False)
        # Compact small dictionaries/lists
        if should_compact(value, level):
            return compact_json(value)
        # Lists
        if isinstance(value, list):
            if not value:
                return "[]"
            lines = ["["]
            for i, item in enumerate(value):
                formatted = format_value(item, level + 1)
                lines.append(f"{next_indent}{formatted}" + ("," if i < len(value) - 1 else ""))
            lines.append(f"{indent}]")
            return "\n".join(lines)
        # Dictionaries
        lines = ["{"]
        items = list(value.items())
        for i, (key, val) in enumerate(items):
            formatted = format_value(val, level + 1)
            lines.append(
                f'{next_indent}{json.dumps(key, ensure_ascii=False)}: '
                f'{formatted}' + ("," if i < len(items) - 1 else "") )
        lines.append(f"{indent}}}") # Attach the final } for the dictionary
        return "\n".join(lines) # Convert the list to a string with newlines between each line froming the dictionary

    return format_value(data)



