"""Pausing whatever is playing while you dictate, and putting it back.

Uses the Windows media session API (SMTC) -- the same channel the hardware media keys
talk to. That matters: broadcasting VK_MEDIA_PLAY_PAUSE hits whichever app the shell
decides owns the keys, and toggles it, so a stray press starts music that was already
stopped. Here the playing sessions are enumerated, only those are paused, and only those
are resumed.

Nothing in this module is allowed to break dictation: every entry point swallows its
errors and reports what it managed to do.
"""

from __future__ import annotations

import asyncio
import logging
import threading
from dataclasses import dataclass

log = logging.getLogger(__name__)

_apartment_ready = threading.local()


@dataclass(frozen=True, slots=True)
class SessionInfo:
    app_id: str
    status: str
    can_pause: bool


def _ensure_apartment() -> None:
    """WinRT needs an initialised apartment on each thread that calls into it."""
    if getattr(_apartment_ready, "done", False):
        return
    try:
        from winrt.system import init_apartment

        init_apartment()
    except Exception:
        # Already initialised, or this build initialises implicitly.
        pass
    _apartment_ready.done = True


def _run(coro):
    _ensure_apartment()
    return asyncio.run(coro)


async def _manager():
    from winrt.windows.media.control import (
        GlobalSystemMediaTransportControlsSessionManager as Manager,
    )

    return await Manager.request_async()


def _status_name(value) -> str:
    try:
        return value.name.title()
    except AttributeError:
        return str(value)


def _is_playing(value) -> bool:
    from winrt.windows.media.control import (
        GlobalSystemMediaTransportControlsSessionPlaybackStatus as Status,
    )

    return value == Status.PLAYING


def _is_paused(value) -> bool:
    from winrt.windows.media.control import (
        GlobalSystemMediaTransportControlsSessionPlaybackStatus as Status,
    )

    return value == Status.PAUSED


class MediaController:
    """Pause/resume media around a dictation. One instance, used from one thread."""

    def __init__(self, apps: tuple[str, ...] = ()) -> None:
        self._apps = tuple(a.lower() for a in apps)
        self._paused: list[str] = []

    def _wanted(self, app_id: str) -> bool:
        if not self._apps:
            return True
        lowered = app_id.lower()
        return any(pattern in lowered for pattern in self._apps)

    # ------------------------------------------------------------------ queries
    def sessions(self) -> list[SessionInfo]:
        try:
            return _run(self._sessions())
        except Exception:
            log.exception("listing media sessions failed")
            return []

    async def _sessions(self) -> list[SessionInfo]:
        manager = await _manager()
        out = []
        for session in manager.get_sessions():
            info = session.get_playback_info()
            out.append(
                SessionInfo(
                    app_id=session.source_app_user_model_id,
                    status=_status_name(info.playback_status),
                    can_pause=bool(info.controls.is_pause_enabled),
                )
            )
        return out

    # ------------------------------------------------------------------ actions
    def pause(self) -> list[str]:
        """Pause every matching session that is playing. Returns their app ids."""
        try:
            self._paused = _run(self._pause())
        except Exception:
            log.exception("pausing media failed")
            self._paused = []
        if self._paused:
            log.info("paused: %s", ", ".join(self._paused))
        return list(self._paused)

    async def _pause(self) -> list[str]:
        manager = await _manager()
        paused = []
        for session in manager.get_sessions():
            app_id = session.source_app_user_model_id
            if not self._wanted(app_id):
                continue
            info = session.get_playback_info()
            if not _is_playing(info.playback_status):
                continue
            if not info.controls.is_pause_enabled:
                log.info("%s is playing but does not allow pausing", app_id)
                continue
            if await session.try_pause_async():
                paused.append(app_id)
        return paused

    def resume(self) -> list[str]:
        """Resume exactly the sessions this controller paused."""
        wanted, self._paused = self._paused, []
        if not wanted:
            return []
        try:
            resumed = _run(self._resume(wanted))
        except Exception:
            log.exception("resuming media failed")
            return []
        if resumed:
            log.info("resumed: %s", ", ".join(resumed))
        return resumed

    async def _resume(self, app_ids: list[str]) -> list[str]:
        manager = await _manager()
        resumed = []
        for session in manager.get_sessions():
            app_id = session.source_app_user_model_id
            if app_id not in app_ids:
                continue
            # If the user restarted it by hand in the meantime, leave it alone.
            if not _is_paused(session.get_playback_info().playback_status):
                continue
            if await session.try_play_async():
                resumed.append(app_id)
        return resumed
