"""Load and apply the offline source-channel classification map."""

import json
from pathlib import Path
from typing import Dict, Optional, Union


DEFAULT_CHANNEL_MAP_PATH = Path(__file__).resolve().with_name("channel_map.json")
SUPPORTED_SPORTS = {"Football", "Tennis", "CS2", "Dota 2"}
SUPPORTED_CHANNEL_TYPES = {"prematch", "live"}


def _normalize_name(value: str) -> str:
    return " ".join(value.split()).casefold()


def load_channel_mapping(
    path: Optional[Union[str, Path]] = None,
) -> Dict[str, Dict[str, str]]:
    """Load the user-editable channel map and validate each entry."""
    config_path = Path(path) if path is not None else DEFAULT_CHANNEL_MAP_PATH
    with config_path.open("r", encoding="utf-8") as config_file:
        raw_mapping = json.load(config_file)

    if not isinstance(raw_mapping, dict):
        raise ValueError("Channel map must be a JSON object")

    mapping: Dict[str, Dict[str, str]] = {}
    for key, value in raw_mapping.items():
        if not isinstance(key, str) or not key.strip():
            raise ValueError("Channel map keys must be non-empty IDs or names")
        if not isinstance(value, dict):
            raise ValueError("Each channel map entry must be an object")
        sport = value.get("sport")
        channel_type = value.get("channel_type")
        if sport not in SUPPORTED_SPORTS:
            raise ValueError(
                "Unsupported sport for channel {!r}: {!r}".format(key, sport)
            )
        if channel_type not in SUPPORTED_CHANNEL_TYPES:
            raise ValueError(
                "Unsupported channel_type for channel {!r}: {!r}".format(
                    key, channel_type
                )
            )
        mapping[key.strip()] = {
            "sport": sport,
            "channel_type": channel_type,
        }
    return mapping


def classify_channel(
    source_channel_id: Optional[Union[str, int]],
    source_channel_name: Optional[str],
    mapping: Optional[Dict[str, Dict[str, str]]] = None,
) -> Dict[str, str]:
    """Classify only from an explicit configured ID or exact channel name."""
    mapping = mapping if mapping is not None else load_channel_mapping()
    if source_channel_id is not None:
        channel_id = str(source_channel_id).strip()
        if channel_id and channel_id in mapping:
            return dict(mapping[channel_id])

    if source_channel_name:
        normalized_name = _normalize_name(source_channel_name)
        for configured_key, classification in mapping.items():
            if _normalize_name(configured_key) == normalized_name:
                return dict(classification)

    return {"sport": "unknown", "channel_type": "unknown"}
