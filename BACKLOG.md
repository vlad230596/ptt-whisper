# Improvement list

Ideas worth doing, with enough notes to start without re-deriving the problem. Nothing
here is in progress. The numbers are for cross-referencing between items, not priority —
items refer to each other by number, so they are never renumbered.

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

---

## 6. Corrections in the archive, and regression tests built on them

**What:** every utterance is already kept as `dataset\ptt_<stamp>.mp3` + `.txt` (plus
`.raw.txt` when the hallucination filter changed something). What is missing is a
*verdict*: was that transcription actually right? Add one more file next to the pair —
`<stamp>.fixed.txt`, the text as it should have been — and its presence becomes the label:

| Files present | Means |
| --- | --- |
| `.mp3` + `.txt` | not reviewed |
| ...plus `.fixed.txt` identical to `.txt` | confirmed correct |
| ...plus `.fixed.txt` differing | a recorded failure, with the ground truth attached |

**Why the good ones matter as much as the bad ones.** A pile of failures tells you what is
broken; it cannot tell you whether a fix broke something else. Every change this project is
likely to make next — a term added to `HOTWORDS`, a different `initial_prompt`, a VAD
threshold, `condition_on_previous_text`, another model — is global and affects every
utterance. Without a corpus of *confirmed-correct* transcriptions to decode again, such
changes are evaluated the way they are today: on the next thing said out loud, once. That
is how a term list quietly starts eating something that used to work.

**Entering the correction.** The natural home is the history view in item 5, but it must
not wait for it — a correction only ever gets made in the ten seconds after the wrong
paste, not in a review session later:

```
ptt fix           # open the last dictation's .txt in the editor; saving writes .fixed.txt
ptt fix <stamp>   # a specific one
ptt fix --ok      # mark the last one correct as it stands (copies .txt to .fixed.txt)
```

`--ok` is the one that would be used dozens of times a day, and it is what makes the
confirmed-correct corpus grow at all, so it should end up on a hotkey rather than in a
console. Resist any bulk "mark everything unreviewed as correct": an unreviewed pile
labelled correct is worse than an unlabelled one, because it looks like evidence.

**The regression run.** For every pair that has a `.fixed.txt`, decode the `.mp3` with the
current settings and compare against the correction:

- Report **character error rate**, not equality. An exact match on 500 characters of
  Russian is not a realistic bar, and a CER threshold is what separates "worded slightly
  differently" from "regressed".
- Report **two** numbers: CER as-is, and CER after stripping case and punctuation. The gap
  between them isolates formatting errors from recognition errors — which is exactly the
  shape of the unpunctuated-lower-case bug in README's known limitations, and would turn it
  from an anecdote into something with a number attached.
- Per-file output, not only an aggregate. A change that fixes three utterances and breaks
  one is the normal case, and a mean hides it.
- Mark it `dataset` (or `slow`) and keep it out of the default run: it loads the model and
  decodes N files, whereas the point of `uv run pytest` finishing in 11 seconds is that it
  gets run constantly. `uv run pytest -m dataset` is then a deliberate act before and after
  a change.

**Design notes:**

- `dataset/` is gitignored and always will be — it is the owner's voice — so this suite can
  never run in CI. It is a local quality gate, and must skip cleanly when the directory is
  absent, exactly as [`tests/test_text.py`](tests/test_text.py) already does for its two
  dataset-driven cases. Those two are the precedent for this whole item and should end up
  sharing its loader.
- Normalise whitespace the way the paste does (one line, stripped) before comparing, but do
  **not** normalise case or punctuation away — see the two-number point above.
- Storage is not a constraint: mp3 costs ~3.2 KB per second of audio, so a year of heavy
  daily use is a few hundred megabytes.
- The same corrected corpus is what fine-tuning would need later (item 5's ambition), but
  the regression suite is the part that pays for itself immediately, and it pays using
  recordings that already exist.

---

## 7. A release you can run without installing anything

**Where it stands:** `.github/workflows/release.yml` publishes a source zip on every `v*`
tag, and `Install.cmd` makes it a double-click. That removes git, Python and uv from the
prerequisites, but the machine still downloads ~5 GB on first run and still needs the
install to succeed. A true "download one file and it works" build is a different thing, and
the numbers are why it has not been done.

**The size problem, measured on this machine** (`.venv\Lib\site-packages`, 2,239 MB total):

| Part | Size |
| --- | --- |
| `nvidia\cudnn` | 1,071 MB |
| `nvidia\cublas` | 736 MB |
| `nvidia\cuda_nvrtc` | 178 MB |
| `ctranslate2`, `onnxruntime`, `numpy`, the rest | ~250 MB |
| the model, separately in `models\` | 2,880 MB |

A bundle that runs offline is therefore ~5.1 GB uncompressed. **GitHub caps a release asset
at 2 GB**, so that artifact cannot be published as one file at all — and the model half is
already a public download from Hugging Face, which is why `ptt setup` fetches it rather than
this project redistributing someone else's weights.

**Which leaves one honest option to evaluate:** a PyInstaller (or `uv`-based) bundle with
the code, CPython and the CUDA DLLs but *without* the model, published as an asset, with the
model still fetched on first run. Raw that is ~2.2 GB; zip compresses DLLs well, so it
plausibly lands near 1.1–1.4 GB — under the cap, but not by much, and unverified. Pruning is
the real lever: `cudnn` ships precompiled engines for every architecture, and this project
targets exactly one GPU generation at a time.

**What makes it real work rather than a build flag:**

- Hidden imports and data files for `ctranslate2`, `av`, `dearpygui` (its built-in font),
  `onnxruntime` (the Silero VAD model) and the `winrt` extension modules — each is a
  separate discovery, and each failure appears only at run time, on someone else's machine.
- `cudalibs.preload()` resolves the CUDA DLLs by absolute path *inside site-packages*
  (see CLAUDE.md for why the bare `LoadLibrary` cannot find them). A frozen layout moves
  those files, so that path logic has to learn about `sys._MEIPASS`, and getting it wrong
  reproduces exactly the bug that function exists to prevent.
- Antivirus and SmartScreen treat an unsigned one-file exe that installs a global keyboard
  hook roughly as you would expect. Code signing is a real cost with a real certificate.
- The end result would still not be testable in CI: no GPU on a runner means the bundle can
  be built there but never proven to decode.

**So the sequencing is:** keep the source zip as the supported path; do the bundle only if
someone other than the owner actually needs it, and start by measuring a pruned `cudnn`,
because if that does not get comfortably under the cap the rest is wasted effort.
