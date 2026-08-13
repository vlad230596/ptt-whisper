# Improvement list

Ideas worth doing, with enough notes to start without re-deriving the problem. Newest
first; nothing here is in progress.

---

## 1. Adaptive spacing on paste

**What:** when the caret sits on a line that already has text, insert a leading space
before the dictated text. On an empty line (or right after a space), insert nothing.
Today [`paste.deliver()`](pushtotalk/paste.py) always pastes the text verbatim, so
dictating onto the end of a half-written line runs the words together.

**The hard part** is finding out what is to the left of the caret without disturbing it.
Options, best first:

- **UI Automation.** `IUIAutomation` → `GetFocusedElement` → `TextPattern` →
  the caret range, expanded to `TextUnit_Line`, gives the line's text; look at what
  precedes the caret. Non-destructive and works in anything that implements
  `TextPattern` (Word, browsers, most native edit controls, VS Code). Reachable from
  ctypes but COM-heavy — this is the one place where pulling in `comtypes` may be worth
  it, since hand-rolling IUIAutomation vtables is a lot of boilerplate.
- **Shift+Home, read the selection, restore.** Works everywhere but is destructive: the
  selection has to be collapsed again afterwards, and a mis-step eats a line of the
  user's text. Only acceptable as a fallback, and probably not even then.
- **Heuristic, no inspection at all.** Always prepend a space unless the pasted text
  starts with punctuation, and let the editor's own behaviour sort it out. Wrong on empty
  lines, which is exactly the case this item is about. Mentioned only to be dismissed.

**Design notes:**

- Make it a config switch (`ADAPTIVE_SPACING = True`) with a documented fallback when
  the target exposes no `TextPattern`: currently that should mean "paste as today".
- The decision has to be made *after* the target window is re-activated and *before*
  `send_ctrl_v()`, because the focused element is only meaningful once focus is back.
- Same question applies at the end of the text, not just the start — worth deciding
  whether a trailing space is ever wanted (probably not; the next dictation adds its own
  leading one).
- Testable in the existing harness: [`tests/test_paste.py`](tests/test_paste.py) already
  builds a real window with an `EDIT` control, so a case can pre-fill the control with
  `"уже есть текст"`, put the caret at the end, and assert the result has exactly one
  space at the join.

---

## 2. Live elapsed-seconds counter while recording

**What:** the OSD currently shows a static `REC ...` for the whole recording. Show how
long has been spoken so far — `REC 4.2s` — updating as it goes.

**Where:** [`app.py`](pushtotalk/app.py) sets the status once in `_start_recording()`.
The worker thread is idle during a recording (it is blocked on the action queue), so the
tick can come from either side; the cleaner one is the OSD's own thread, which already
owns a `SetTimer` for auto-hide.

**Design notes:**

- Prefer a repeating Win32 timer inside [`osd.py`](pushtotalk/osd.py) over a
  `threading.Timer` loop in `app.py`: the OSD window is on the message-pump thread
  already, so a timer there needs no cross-thread marshalling and stops cleanly when the
  window hides. Something like `show_live(lambda: f"REC {elapsed():.1f}s", interval=100)`,
  cancelled by the next ordinary `show()`.
- **Fixed-width slot, monospace digits — decided.** Do not re-measure the window on
  every tick: the width changes when the text goes from `9.9s` to `10.1s`, and a window
  that jitters next to the pointer is worse than no counter. Measure once against the
  widest template the format can produce (`REC 00:00.0`), keep that width for the whole
  recording, and draw the number in a monospace face so the digits do not shuffle
  sideways within the fixed slot either.
  - Format: `REC 00:00.0` — minutes always present, one decimal. A dictation is rarely
    over a minute, but a counter that changes shape at 60 s is exactly the jitter this
    note is about.
  - The face for the number needs to be separate from `OSD_FONT`: Segoe UI is not
    monospace, and its digits are not tabular. Either add `OSD_FONT_MONO` (Cascadia Mono
    / Consolas) and draw the label and the number as two `DrawTextW` calls, or keep one
    font and rely on a fixed slot alone — the two-font version is what actually stops
    the digits moving.
- 100 ms is smooth enough and cheap; the repaint is one `FillRect` plus one `DrawTextW`.
- Worth carrying the same idea into the tray tooltip (`PushToTalk — recording 4.2s`),
  which is a one-line addition once the tick exists.
- While here: `MIN_REC_MS` (350 ms) silently discards taps shorter than that. A counter
  that shows `00:00.2` and then reports "Too short — cancelled" is honest, so no change
  is needed — just do not be surprised by it.

---

## 3. Show the status at the caret, not only at the mouse pointer

**What:** `REC` currently appears wherever the mouse happens to be
(`OSD_POSITION = "cursor"` in [config.py](pushtotalk/config.py)), which is often nowhere
near where you are looking. Put it next to the text insertion point in the target window
— where the words are about to land.

**How to find the caret in another process:**

- **`GetGUIThreadInfo`** on the target window's thread returns `rcCaret` plus
  `hwndCaret`; convert with `ClientToScreen` on `hwndCaret`. Cheap, pure ctypes, no COM.
  Works in native edit controls, Notepad++, Win32 apps generally.
- **It returns nothing useful in Chromium, Electron and VS Code**, which draw their own
  caret and never create a Win32 one. For those, the caret rectangle comes from UI
  Automation: the degenerate caret range's `GetBoundingRectangles()`. This is the same
  plumbing item 1 needs, so the two should be built together — one UIA helper module
  serving both "what is left of the caret" and "where is the caret on screen".
- Fallback chain, in order: UIA rectangle → `GetGUIThreadInfo` → centre-bottom of the
  target window → mouse pointer (today's behaviour).

**Design notes:**

- Make it a third value of the existing setting: `OSD_POSITION = "caret"`, keeping
  `"cursor"` and `"bottom-right"` working.
- The target window handle is already captured at key-down and sits in
  `App._target_hwnd`, so the position can be resolved at the same moment `REC` is first
  shown. Resolve it **once** at the start of the recording and keep it: chasing a moving
  caret during a recording is pointless (the caret is not moving — you are talking) and
  would make the live counter from item 2 flicker between positions.
- Offset it below-right of the caret rectangle so it never covers the line being typed
  into, and clamp to the monitor work area as `_place()` already does.
- Nothing should break when the target window is gone or the caret cannot be found;
  falling back silently to the pointer is the correct behaviour, not an error.

---

## 4. Refuse to record when there is nowhere to paste

**What:** check *before* recording that the target really has a live text input — an
editable field with keyboard focus. Today the check happens far too late: the target
window is only tested for existence after transcription, so it is entirely possible to
dictate for a minute into a window with no caret in it and be told at the end that the
text went to the clipboard instead. The failure should arrive in the first 200 ms, not
after the sentence.

**How to decide, before the recording starts:**

- UI Automation on the focused element: `IsEnabled`, not `IsReadOnly`, and it supports
  `TextPattern` or is one of the editable control types. That is the same UIA helper
  items 1 and 3 need — three features now want it, which settles the question of whether
  it is worth pulling in.
- Cheaper Win32 pre-filter, useful as a fast path and as a fallback:
  `GetGUIThreadInfo(target_thread).hwndCaret` being non-zero means there is a real caret;
  `GetFocus` (after `AttachThreadInput`) gives the focused control, and its class name
  rules a lot in or out.
- Whatever the mechanism, it must be **fast and fail-open**: if the answer cannot be
  determined in a few milliseconds, record anyway. A false refusal is much worse than the
  problem being solved — losing a thought because the tool got clever is unacceptable,
  while an occasional paste-to-clipboard is merely annoying.

**Design notes:**

- Config switch `REQUIRE_INPUT_FIELD` with three states rather than a bool:
  `"off"` (today), `"warn"` (record, but say so in the OSD up front), `"refuse"` (do not
  start, beep/flash instead). Default to `"warn"` — it tells you immediately without ever
  costing you a dictation.
- The refusal message must say *which* window was rejected, or it is impossible to debug.
- Interacts with item 3: if the caret cannot be located for positioning, that is already
  strong evidence there is no input field, so the two checks share their answer.

---

## 5. A pinned control panel — next version

**What:** the direction all of the above is pointing. Instead of a transient status
tooltip and a `config.py` you edit and restart, a small always-available window that is
both the status display and the control surface:

- **Settings, changed live:** pin the recognition language instead of relying on
  `multilingual`; pick which hotword list is active, per project, rather than maintaining
  the one global `HOTWORDS` string; toggle media pausing, adaptive spacing, test mode.
- **Key bindings, rebound without editing Python.** `HOTKEY_REC = "F8"` is a bad default
  that only survives because it was convenient on the machine this was written on: F8 is
  swallowed globally, so debugger step-over and VS Code's "Go to Next Problem" die while
  the app runs. Rebinding it should be a click, and the panel should say when the chosen
  chord is already claimed by something else. `hotkeys.py` already parses chords from
  strings, so the parsing side is done — what is missing is somewhere to put the answer
  and a way to re-register the hook without a restart.
- **Performance metrics:** per-utterance record/inference times, sequential vs batched,
  where the time actually goes. All of it is already logged — this is a view over it.
- **History:** the last dictations with their audio, replayable. Which turns the
  `dataset/` archive from a write-only pile into something you can browse, correct and
  curate — i.e. actually build a fine-tuning dataset rather than just accumulate one.

**Notes for whoever starts this:**

- This is where the current architecture stops being enough, and that is fine — it is
  also where the choice of Python pays off. The modules are already separated along the
  right seams (`config`, `asr`, `text`, `archive`), so the panel is a consumer of them,
  not a rewrite of them.
- The one real structural change needed first: `config.py` is module-level constants read
  at import time, which cannot express "changed live" or "per project". That wants a
  settings object with a file behind it (`settings.json` next to the venv) and a reload
  path — worth doing as its own step *before* any UI, because every item above depends on
  it and it touches every module.
- Hotword sets per project also want a notion of "current project", which is most likely
  derived from the target window (process name, or the folder open in the editor). That
  detection is the same window/process plumbing items 1 and 3 introduce.
- GUI toolkit is an open question: a Win32 panel in the existing ctypes style keeps the
  zero-dependency property but is a lot of hand-written layout for something this
  interactive. This is the point at which a real toolkit (Qt, or a webview) earns its
  place. Deliberately not decided here.
