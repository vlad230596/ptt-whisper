"""The one file the app writes settings to, and the only exception to "everything is
in config.py".

config.py is still the file you edit by hand, and it still holds the default for every
tunable. What it cannot be is the destination of a *runtime* choice: picking a
microphone from a list and then having to open Python to make the choice stick is not a
setting flow, and a program that rewrites its own source is worse than a program with
two settings files. So `settings.json` holds the handful of values that are properties of
*this machine* rather than of the project -- the microphone, the recognition
`compute_type` (2026-08-16, for a machine whose GPU does not share the RTX 5080's int8
limitation), and `batching` (2026-08-17, for a machine that measures a real speed win
from it -- see BACKLOG item 9 for why it defaults off) -- and overrides the matching
name in config.py.

Two rules make the split debuggable rather than confusing:

* `effective_mic()`, `effective_compute_type()` and `effective_batching()` each return
  the value *and* where it came from, and app.py logs that at startup while
  `ptt devices` and `ptt doctor` print it. "Why is it recording from the wrong device" or
  "why is it running int8 on the RTX 5080" is then one line in the log instead of a guess
  between two files.
* nothing here raises. A settings.json that is missing, empty, truncated by a power cut
  or hand-edited into invalid JSON degrades to the config.py default and says so in the
  log. Dictation must not become unstartable because of a file that only exists to hold
  a preference.

Writes go through a temporary file and `os.replace`, so an interrupted save leaves the
previous settings intact rather than a half-written file that then fails to parse.
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
from pathlib import Path
from typing import Any

from . import config as cfg

log = logging.getLogger(__name__)

CONFIG_SOURCE = "config.py"
SETTINGS_SOURCE = "settings.json"


def path(override: str | Path | None = None) -> Path:
    return Path(override) if override is not None else Path(cfg.SETTINGS_FILE)


def load(override: str | Path | None = None) -> dict[str, Any]:
    """Everything in the settings file, or {} if there is nothing usable there."""
    file = path(override)
    try:
        raw = file.read_text(encoding="utf-8")
    except FileNotFoundError:
        return {}
    except OSError as exc:
        log.warning("could not read %s (%s); using the config.py defaults", file, exc)
        return {}
    if not raw.strip():
        return {}
    try:
        values = json.loads(raw)
    except ValueError as exc:
        log.warning("%s is not valid JSON (%s); using the config.py defaults", file, exc)
        return {}
    if not isinstance(values, dict):
        log.warning("%s does not contain a JSON object; using the config.py defaults", file)
        return {}
    return values


def save(values: dict[str, Any], override: str | Path | None = None) -> bool:
    """Merge `values` into the settings file. False if it could not be written.

    Merging rather than replacing means a key this version does not know about -- one
    written by a newer build, or by hand -- survives a save from here.
    """
    file = path(override)
    merged = load(override) | values
    try:
        file.parent.mkdir(parents=True, exist_ok=True)
        # Same directory, so os.replace is an atomic rename rather than a copy.
        handle, temp_name = tempfile.mkstemp(dir=str(file.parent), prefix=".settings-",
                                             suffix=".tmp")
        try:
            with os.fdopen(handle, "w", encoding="utf-8") as stream:
                json.dump(merged, stream, ensure_ascii=False, indent=2)
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temp_name, file)
        except BaseException:
            Path(temp_name).unlink(missing_ok=True)
            raise
    except OSError as exc:
        log.error("could not write %s (%s)", file, exc)
        return False
    log.info("wrote %s: %s", file, ", ".join(sorted(values)))
    return True


# ------------------------------------------------------------------ microphone
def effective_mic(override: str | Path | None = None) -> tuple[str, str]:
    """(device name, which file it came from).

    A blank or non-string value in the settings file is treated as "not set" rather
    than as "match every device": an empty MIC would resolve to the first input in the
    list, which is exactly the silent wrong-device failure this is meant to prevent.
    """
    chosen = load(override).get("mic")
    if isinstance(chosen, str) and chosen.strip():
        return chosen.strip(), SETTINGS_SOURCE
    if chosen is not None:
        log.warning("ignoring mic=%r in %s: not a non-empty string", chosen, path(override))
    return cfg.MIC, CONFIG_SOURCE


def set_mic(name: str, override: str | Path | None = None) -> bool:
    name = name.strip()
    if not name:
        raise ValueError("the microphone name must not be empty")
    return save({"mic": name}, override)


def clear_mic(override: str | Path | None = None) -> bool:
    """Go back to whatever config.py says."""
    values = load(override)
    if "mic" not in values:
        return True
    del values["mic"]
    file = path(override)
    try:
        file.write_text(json.dumps(values, ensure_ascii=False, indent=2) + "\n",
                        encoding="utf-8")
    except OSError as exc:
        log.error("could not write %s (%s)", file, exc)
        return False
    return True


# ------------------------------------------------------------------ compute type
def effective_compute_type(override: str | Path | None = None) -> tuple[str, str]:
    """(compute type, which file it came from).

    Same shape and the same reasons as `effective_mic()`: `COMPUTE_TYPE = "float16"` in
    config.py is a hardware fact on the reference RTX 5080 (see CLAUDE.md), not a
    universal one -- a machine whose GPU has real int8 support can override it here
    instead of hand-editing config.py, and a blank or non-string value is treated as
    "not set" rather than passed to WhisperModel, which would fail late inside
    CTranslate2 instead of here.
    """
    chosen = load(override).get("compute_type")
    if isinstance(chosen, str) and chosen.strip():
        return chosen.strip(), SETTINGS_SOURCE
    if chosen is not None:
        log.warning("ignoring compute_type=%r in %s: not a non-empty string",
                    chosen, path(override))
    return cfg.COMPUTE_TYPE, CONFIG_SOURCE


def set_compute_type(value: str, override: str | Path | None = None) -> bool:
    value = value.strip()
    if not value:
        raise ValueError("the compute type must not be empty")
    return save({"compute_type": value}, override)


def clear_compute_type(override: str | Path | None = None) -> bool:
    """Go back to whatever config.py says."""
    values = load(override)
    if "compute_type" not in values:
        return True
    del values["compute_type"]
    file = path(override)
    try:
        file.write_text(json.dumps(values, ensure_ascii=False, indent=2) + "\n",
                        encoding="utf-8")
    except OSError as exc:
        log.error("could not write %s (%s)", file, exc)
        return False
    return True


# ------------------------------------------------------------------ batching
def effective_batching(override: str | Path | None = None) -> tuple[bool, str]:
    """(batching enabled, which file it came from).

    Same shape and the same reasons as `effective_compute_type()`: `BATCHING_ENABLED =
    False` in config.py is the default measured on one machine (see BACKLOG item 9), not
    a universal answer -- a machine that measures a real speed win from batching can
    override it here instead of hand-editing config.py. Anything other than an actual
    bool (a string "true", a number) is treated as "not set" rather than coerced, since a
    coerced typo would silently flip decoding behaviour.
    """
    chosen = load(override).get("batching")
    if isinstance(chosen, bool):
        return chosen, SETTINGS_SOURCE
    if chosen is not None:
        log.warning("ignoring batching=%r in %s: not a bool", chosen, path(override))
    return cfg.BATCHING_ENABLED, CONFIG_SOURCE


def set_batching(value: bool, override: str | Path | None = None) -> bool:
    return save({"batching": bool(value)}, override)


def clear_batching(override: str | Path | None = None) -> bool:
    """Go back to whatever config.py says."""
    values = load(override)
    if "batching" not in values:
        return True
    del values["batching"]
    file = path(override)
    try:
        file.write_text(json.dumps(values, ensure_ascii=False, indent=2) + "\n",
                        encoding="utf-8")
    except OSError as exc:
        log.error("could not write %s (%s)", file, exc)
        return False
    return True
