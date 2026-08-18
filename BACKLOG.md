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

---

## 8. Re-check int8 on the RTX 5080 now that CTranslate2 auto-disables it

**Why this is worth revisiting:** the "float16 is mandatory" rule in CLAUDE.md was written
around `CUBLAS_STATUS_NOT_SUPPORTED` crashing outright. The upstream fix
([OpenNMT/CTranslate2#1865](https://github.com/OpenNMT/CTranslate2/issues/1865),
[PR #1937](https://github.com/OpenNMT/CTranslate2/pull/1937)) landed in CTranslate2
**v4.6.2** — older than the **4.8.1** this project already pins — and makes CTranslate2
itself detect sm_120 and refuse int8 before it ever reaches the broken cuBLAS call. That
was confirmed on a second machine (GTX 1650 Ti, Turing) where int8 is not broken at all and
is a measured 4.2×-4.8× win (see the CLAUDE.md decision) — it has **not** been re-tried on
the RTX 5080 since the pinned version changed.

**What to check:**

- Set `compute_type` (via `settings.json`, the same override the Turing machine uses — see
  `settings.effective_compute_type()`) to `"int8"` or `"int8_float16"` on the RTX 5080 and
  see what actually happens now: a clean automatic fallback to float16 (in which case
  `int8` becomes a safe default everywhere and the machine-specific override could
  disappear), the same old crash (in which case the pinned 4.8.1 does not actually carry the
  fix the way the changelog suggests, which would itself be worth a note), or a fallback
  that is silent in a way that hides which compute type is really running (in which case
  `ptt doctor`/`ptt devices` need to report the *effective* type CTranslate2 chose, not just
  the one requested).
- If the fallback is clean, it's still worth comparing decode time against `float16`
  explicitly on the 5080 — CLAUDE.md's own numbers (17x real-time) were measured before this
  question existed, so there's no baseline yet for "int8-that-fell-back-to-float16" vs.
  "float16 requested directly": they should be identical, but that is an assumption, not
  something measured.
- Do this deliberately (not as a background surprise): flipping `COMPUTE_TYPE`'s *effective*
  default away from a hard-set `"float16"` on the reference machine is exactly the kind of
  change CLAUDE.md's hard-constraints section says is "not up for silent revision."

---

## 9. Batching's speed win doesn't hold up against real usage — now gated by `BATCHING_ENABLED`

**Why this is here:** CLAUDE.md's "Adaptive batching above 15 s" decision rests on one test
clip (35.4 s, RTX 5080, float16): 2.06 s sequential → 1.54 s batched, a clear 25% win, plus
"slightly better sentence punctuation." Investigating the unpunctuated-run-on bug (see
CLAUDE.md's "Known limitations") found the opposite of that second claim — batching can lose
punctuation, not improve it — which made the first claim worth re-checking against more than
one clip.

**What was measured (2026-08-17, this machine: GTX 1650 Ti, `int8_float16`):** every dataset
recording over `BATCH_ABOVE_SEC` (44 of them, 1450.6 s of audio total) decoded twice — once
through `WhisperModel.transcribe` (sequential), once through `BatchedInferencePipeline`
(batched, `BATCH_SIZE=8`), both with production options (`hotwords`, `initial_prompt`,
`vad_filter=True`).

| | Sequential | Batched |
| --- | --- | --- |
| Total decode time | 1024.06 s | 964.40 s |
| Realtime factor | 1.4x | 1.5x |
| **Overall speedup** | — | **1.06x** |

A 6% average win, not 25% — and it isn't a uniform 6%: per-file speedup ranged from 0.85x
(batched *slower*) to 2.00x, with no obvious predictor from duration alone. The 35.4 s
reference clip's 25% number was one sample from that same spread, not the typical case.

**Punctuation cost, measured the same way:** comparing punctuation-marks-per-letter between
the two transcripts of each file, **8 of 44 utterances (18%)** had batched-mode punctuation
density less than half of sequential's — the same failure this item exists because of, not
limited to the one utterance that surfaced it (`ptt_20260817_181701_22.mp3`, in CLAUDE.md).
Text differed between modes on 43 of 44 files overall (most differences minor — a comma
here, a case there — the 8-file figure above is the one that isolates the severe cases).

**Reading it:** on this machine, batching buys ~6% average speed for an 18% chance that a
long dictation comes back with its punctuation and casing broken. That trade doesn't look
worth it here. What's *not* measured is whether the RTX 5080 (the reference machine
CLAUDE.md's original numbers came from, on `float16` rather than `int8_float16`) shows a
larger speed gain that would change the calculus — the punctuation cost is a property of VAD
chunking and shouldn't depend on the GPU, but the speedup side of the trade might. Re-run
this same comparison there before changing the *default* project-wide; the script used is
`decode_audio()` + `Engine._model.transcribe()` vs `Engine._batched.transcribe()` over
`dataset/*.mp3`, filtered to `duration > BATCH_ABOVE_SEC` — cheap enough to hand-run against
any machine's own `dataset/` before deciding.

**Acted on, 2026-08-17:** rather than deleting the batched path outright, `BATCHING_ENABLED`
in config.py now gates it, defaulting to `False`, with a per-machine override in
`settings.json`'s `"batching"` key — same mechanism and the same tests as `compute_type`
(`settings.effective_batching()`, `ptt doctor` / `ptt devices` report which is in force and
where it came from). This is deliberately the same shape as the RTX-5080-vs-Turing split for
`compute_type` above: if the RTX 5080 measurement shows batching is worth it *there*,
`settings.json` on that machine is where that goes, without touching the shared default that
the rest of this item's evidence argues against.

**Full per-file data**, sequential vs. batched, every `dataset/*.mp3` over `BATCH_ABOVE_SEC`
(15 s) on this machine at the time of measurement:

| file | dur (s) | seq (s) | batch (s) | speedup | seq punct % | batch punct % | text identical |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | :---: |
| ptt_20260816_190728_2 | 20.9 | 16.11 | 15.16 | 1.06 | 4.3 | 3.2 | ✗ |
| ptt_20260816_191051_4 | 75.5 | 64.29 | 53.35 | 1.21 | 3.5 | 3.2 | ✗ |
| ptt_20260816_191418_6 | 40.1 | 27.80 | 26.03 | 1.07 | 0.0 | 0.9 | ✗ |
| ptt_20260816_191510_7 | 17.9 | 14.65 | 13.87 | 1.06 | 4.7 | 2.3 | ✗ |
| ptt_20260816_192115_9 | 46.6 | 40.66 | 38.19 | 1.06 | 1.8 | 2.2 | ✗ |
| ptt_20260816_212349_2 | 16.4 | 11.16 | 12.38 | 0.90 | 5.1 | 3.7 | ✗ |
| ptt_20260816_214426_3 | 21.5 | 14.70 | 13.01 | 1.13 | 4.8 | 3.3 | ✗ |
| ptt_20260816_214809_5 | 32.9 | 22.63 | 19.92 | 1.14 | 0.0 | 0.8 | ✗ |
| ptt_20260816_214938_6 | 31.0 | 29.11 | 14.56 | 2.00 | 4.9 | 0.0 | ✗ |
| ptt_20260816_215359_7 | 53.9 | 40.90 | 40.03 | 1.02 | 4.0 | 4.0 | ✗ |
| ptt_20260816_215432_8 | 15.2 | 8.79 | 8.66 | 1.01 | 3.4 | 0.0 | ✗ |
| ptt_20260816_220839_9 | 33.9 | 22.47 | 21.67 | 1.04 | 4.8 | 0.5 | ✗ |
| ptt_20260816_221103_10 | 31.9 | 25.85 | 26.19 | 0.99 | 5.3 | 6.0 | ✗ |
| ptt_20260816_222404_11 | 19.7 | 12.86 | 13.63 | 0.94 | 3.9 | 3.8 | ✗ |
| ptt_20260816_224209_14 | 61.3 | 48.20 | 46.99 | 1.03 | 6.8 | 6.1 | ✗ |
| ptt_20260816_224305_15 | 28.5 | 14.25 | 13.76 | 1.04 | 3.0 | 2.6 | ✗ |
| ptt_20260816_224445_16 | 49.0 | 38.40 | 37.73 | 1.02 | 3.3 | 3.5 | ✗ |
| ptt_20260816_224518_17 | 23.1 | 12.93 | 12.19 | 1.06 | 5.3 | 5.3 | ✗ |
| ptt_20260816_225329_19 | 28.9 | 13.40 | 13.30 | 1.01 | 4.4 | 5.5 | ✗ |
| ptt_20260816_225703_21 | 48.2 | 30.44 | 31.84 | 0.96 | 5.4 | 6.2 | ✗ |
| ptt_20260816_230704_2 | 16.2 | 8.51 | 9.21 | 0.92 | 6.3 | 0.0 | ✗ |
| ptt_20260816_230920_3 | 47.8 | 24.37 | 28.71 | 0.85 | 0.0 | 4.1 | ✗ |
| ptt_20260816_231641_1 | 48.1 | 35.10 | 27.78 | 1.26 | 5.4 | 6.0 | ✗ |
| ptt_20260816_231826_2 | 18.7 | 9.74 | 10.39 | 0.94 | 2.9 | 2.8 | ✗ |
| ptt_20260816_231932_3 | 21.0 | 13.37 | 14.37 | 0.93 | 3.7 | 3.7 | ✗ |
| ptt_20260816_234226_1 | 40.5 | 29.68 | 28.27 | 1.05 | 5.4 | 3.5 | ✗ |
| ptt_20260816_235409_2 | 36.0 | 20.98 | 20.81 | 1.01 | 3.1 | 2.8 | ✗ |
| ptt_20260817_140957_1 | 37.8 | 27.14 | 25.06 | 1.08 | 6.1 | 6.1 | ✓ |
| ptt_20260817_141200_2 | 21.8 | 12.08 | 12.34 | 0.98 | 2.9 | 2.9 | ✗ |
| ptt_20260817_141601_3 | 34.8 | 29.23 | 26.96 | 1.08 | 5.5 | 1.2 | ✗ |
| ptt_20260817_142114_4 | 15.6 | 11.53 | 11.43 | 1.01 | 5.3 | 5.3 | ✗ |
| ptt_20260817_142609_6 | 33.4 | 24.53 | 26.16 | 0.94 | 3.1 | 3.1 | ✗ |
| ptt_20260817_144338_7 | 23.9 | 13.20 | 13.27 | 0.99 | 2.8 | 2.8 | ✗ |
| ptt_20260817_151144_8 | 21.6 | 11.83 | 12.37 | 0.96 | 5.2 | 4.6 | ✗ |
| ptt_20260817_151244_9 | 31.0 | 24.46 | 18.86 | 1.30 | 3.7 | 3.4 | ✗ |
| ptt_20260817_153358_10 | 47.8 | 34.87 | 37.72 | 0.92 | 4.2 | 4.0 | ✗ |
| ptt_20260817_163227_12 | 24.4 | 16.11 | 15.69 | 1.03 | 5.7 | 5.7 | ✗ |
| ptt_20260817_164222_15 | 33.9 | 25.72 | 24.39 | 1.05 | 3.1 | 3.1 | ✗ |
| ptt_20260817_170459_17 | 45.6 | 27.01 | 21.36 | 1.26 | 2.0 | 1.8 | ✗ |
| ptt_20260817_172047_19 | 33.2 | 26.26 | 27.51 | 0.95 | 4.7 | 4.4 | ✗ |
| ptt_20260817_173301_20 | 33.8 | 24.15 | 21.71 | 1.11 | 4.1 | 4.0 | ✗ |
| ptt_20260817_181701_22 | 38.4 | 29.23 | 26.65 | 1.10 | 4.7 | 1.4 | ✗ |
| ptt_20260817_181843_23 | 30.8 | 24.88 | 19.93 | 1.25 | 4.1 | 4.4 | ✗ |
| ptt_20260817_183315_24 | 18.3 | 10.50 | 10.97 | 0.96 | 1.6 | 0.0 | ✗ |

`ptt_20260817_181701_22` is the utterance in CLAUDE.md's "Known limitations" entry.
"punct %" is punctuation marks per 100 letters in that file's transcript — a rough proxy,
not a quality score; the 8 rows where the batched column is under half the sequential one
are the severe cases (`_191510_7`, `_214938_6`, `_215432_8`, `_220839_9`, `_230704_2`,
`_141601_3`, `_181701_22`, `_183315_24`).

**If the RTX 5080 confirms the same picture:** the simplest fix is to stop batching entirely
(drop the `BatchedInferencePipeline` branch in `asr.py`, or set `BATCH_ABOVE_SEC` past any
real dictation length) rather than try to tune `VadOptions.min_silence_duration_ms` inside the
batched path — that was tried against the reference utterance and turned out fragile (the
same threshold value produced a different, still-broken segmentation depending on how it was
passed), so it isn't a safe dial.

---

## 10. Queue overlapping dictations instead of dropping the key press

**Where it stands today:** [`app.py`](pushtotalk/app.py)'s `_enqueue()` drops a `PTT_DOWN`
outright while `_busy` is set — i.e. for the whole tail of one utterance: VAD grace period,
transcription, archiving, paste. The OSD says "Still finishing the previous dictation ..."
and that recording never happened; whatever was said during it is gone. This was a deliberate
choice (see the docstring on `_enqueue`) to avoid a worse bug — queuing the key press so it
silently starts recording *after* the previous one finishes, seconds after the words were
actually spoken — but the fix it chose was "refuse the second press," not "make the second
press free."

**What it should do instead:** a spoken thought that runs long enough to want two breaths
should be splittable into two (or more) key presses, each captured immediately, each queued
for transcription, and each pasted in order once its turn comes — without the second press
ever being rejected or having to wait for the first press's decode to finish. Concretely:

- Pressing the hotkey starts a *new* recording immediately, regardless of whether an earlier
  recording is still being transcribed. The only thing that should block a new recording is
  another recording literally in progress (mic already open) — never "the GPU is busy."
- Each finished recording (audio + its own captured `_target_hwnd`, the same per-utterance
  target capture that already exists) is pushed onto a FIFO queue rather than transcribed
  inline.
- A single consumer drains the queue one item at a time — decode, then paste — so utterances
  land in the order they were *spoken*, not the order their decode happened to finish in
  (irrelevant today since there is one GPU and one model instance to serialize on anyway, but
  worth stating as the invariant the design must preserve).
- Nothing about "record now, decode later" changes single-GPU serialization of the actual
  inference — only the *recording* stops being gated on the *previous decode*.

**What has to change to get there — this is the hard part, not the queue itself:**

- The single `_busy` boolean has to split into two independent states: "is the mic capturing
  right now" (still exclusive — one Recorder, one stream) and "is there a backlog of
  unfinished transcriptions" (no longer exclusive with recording). `_start_recording` should
  only check the first.
- `self._target_hwnd` is a single field today because there was only ever one utterance in
  flight; with a queue, the paste target has to travel with each queued item instead of living
  on `self`, or utterance 2's paste target overwrites utterance 1's before utterance 1 gets to
  use it.
- **Media pause/resume needs reference counting, not edge-triggering.** Today pause fires once
  per `_start_recording`, resume once per `_finish`. With two overlapping utterances, a naive
  port would resume the paused music between utterance 1 finishing and utterance 2 still being
  transcribed — audible flicker. Pause on the first recording to start while the queue is
  otherwise empty; resume only when both the queue is drained and no recording is active.
- **The OSD/tray need a queued-depth state**, not just "recording / transcribing": what shows
  while recording #2 is captured but #1 is still decoding, and what shows while #1's paste
  happens and #2 is still recording. "Transcribing ... (1 queued)" is the minimum; item 5's
  panel is the natural long-term home for a real queue view.
- **Errors must not wedge the queue.** `_worker`'s existing catch-and-log-and-continue around
  each action is the right shape to extend to the transcribe/paste step — one bad item should
  not stop the rest of the queue from draining.
- **Archive ordering** (`_next_seq`, the stamp used for `dataset/*.mp3`) already increments
  per utterance regardless of when transcription happens, so this should fall out for free —
  worth double-checking once the queue exists rather than assuming.
- No cap on queue depth is needed: it is bounded by how many times a human can press a key
  before the GPU catches up, which in practice is very small.

---

## 11. Warn early that nothing is actually being captured, instead of only after decoding

**The incident this is from:** dictating for 30 s-1 min into what turned out to be a dead
input — a mic muted or disconnected as a side effect of troubleshooting something else
(unplugged, wrong device left selected, physically muted) — with no way to know until the
whole recording had been spoken, captured, and sent to the model, only to get back "no
audio captured." The waste is entirely avoidable: whether the input is live is knowable
within a second or two, not only at the end.

**What exists already and is directly reusable:**

- `recorder.rms()` and the `MIN_RMS = 0.0002` threshold in
  [config.py](pushtotalk/config.py) already draw exactly this line — a silent room on the
  reference machine measures `0.00086` (a real, if quiet, room), so `MIN_RMS` is tuned to
  catch a truly dead device, not just a quiet speaker. Today it is only checked once, in
  `App._finish()`, *after* the whole recording is over.
- `recorder.LevelMonitor` already does incremental, low-cost level tracking for the
  microphone chooser's meter: it accumulates `sum_squares`/`count`/`peak` in the capture
  callback and hands back a windowed RMS on `read()`, resetting for the next window. That
  is the right shape for a periodic check *during* a live `Recorder` capture too — cheap
  per callback, no re-concatenating a growing minute-long buffer on every tick the way a
  naive "call `rms()` on everything captured so far" would be.
- The live-elapsed-counter idea in item 2 already establishes where a repeating tick during
  recording should live (an OSD-owned `SetTimer`, not a `threading.Timer` in `app.py`) —
  this wants the same kind of tick, so the two are worth building together if either one
  gets picked up first.

**What it should do:** while recording is in progress, sample the running level every
second or so; if it stays under `MIN_RMS` continuously for some short window (5 s is what
was asked for — long enough that a normal breath or a thoughtful pause before speaking
doesn't false-positive, short enough that it's still much cheaper than losing the whole
utterance), surface it in the OSD next to the `REC` status — something like
`REC ... — no sound detected, check the mic` — without stopping or discarding the
recording. The person holding the key decides what to do with that information; the
program must never auto-cancel a recording on this signal alone, because "quiet speech
that is nonetheless real audio" and "dead mic" are only reliably told apart at the
`MIN_RMS` boundary that already exists, and a false auto-cancel would lose a real dictation
outright, which is a worse failure than a spurious warning.

**Design notes:**

- This is a level check, not VAD/speech detection — "are there any fluctuations at all"
  is right, matching what the user asked for; it should not try to guess whether *speech*
  is present, only whether the input is alive. `vad_filter`/hallucination filtering already
  own the harder "was there meaningful speech" question post-decode, and this item
  shouldn't duplicate that.
- Reset the continuous-silence timer whenever the level crosses back above `MIN_RMS`, so a
  brief dead patch followed by real speech doesn't leave a stale warning on screen — the
  same warning should also need to clear itself once sound resumes.
- The end-of-recording check in `_finish()` (`level < cfg.MIN_RMS` → "No audio captured")
  stays as the authoritative final word regardless — this item is purely about surfacing
  the same fact earlier, not replacing that check.
- Worth deciding whether the 5 s threshold is a new `config.py` constant
  (`SILENCE_WARNING_MS`, alongside the comment-with-measurement convention every other
  constant in that file already follows) or reuses `MIN_REC_MS`/`TAIL_MS`'s neighborhood —
  it is a genuinely different knob from either, so a new one is more likely right.

---

## 12. A live level meter in the `REC` OSD instead of the plain `REC ...` text

**What:** while recording, show the microphone's actual level moving in real time — a
filling bar, à la the meter Windows' own volume mixer draws, rather than a static
`REC ...` string. Two birds: it is a nicer thing to glance at than plain text, and it
*is* item 11's silence warning without needing a sentence — a bar that visibly isn't
moving tells you the mic is dead faster and more plainly than any words would.

**The pieces for this already exist, on both sides:**

- [`osd.py`](pushtotalk/osd.py)'s `StatusWindow.show()` already takes a `progress: float`
  and paints a filled bar under the text (`_track_brush`/`_fill_brush`, drawn in
  `_on_paint`) — built for the transcribe-percentage bar, but the widget itself has no
  idea what the fraction *means*. `REC` and `Transcribing` never overlap, so the same bar
  can serve double duty: a live level while recording, a decode fraction while
  transcribing — no new paint code needed for a first, rectangular version.
- [`micui.py`](pushtotalk/micui.py)'s microphone-chooser meter has already solved the
  "level to bar" mapping this wants to copy, not reinvent: `recorder.dbfs()` for a
  floor-clamped dB value, `(level_db - floor) / -floor` to normalize it to 0..1, and
  `cfg.METER_CLIP_DB` to flag when the input is clipping. Match that curve instead of
  inventing a second one — a linear RMS-to-bar mapping (no dB) would make ordinary speech
  barely move the needle, which is the opposite of legible.
- The cheap incremental level read this needs from a live `Recorder` capture is the exact
  same thing item 11 needs — `recorder.LevelMonitor`'s windowed
  `sum_squares`/`count`/`peak` accumulated in the capture callback, read and reset on a
  timer, rather than re-running `rms()` over the whole growing buffer every tick. Build
  that plumbing once and both items use it.
- The tick that drives the repaint is the same mechanism item 2 already proposes for a
  live elapsed-seconds counter: an OSD-owned `SetTimer`, ~100 ms, not a `threading.Timer`
  in `app.py`. If item 2 lands first, this is "feed the same timer a level instead of a
  clock"; if this lands first, item 2 is the reverse.

**Design notes:**

- A real icon-shaped fill (the actual little-microphone-glyph-filling-from-the-bottom look
  Windows uses) is meaningfully more paint work than the rectangle bar already in
  `osd.py` — GDI has no cheap vector fill-clip primitive, so that would mean either a
  bitmap mask or stacking small shapes to fake the icon outline. Worth doing only as a
  second pass, once the plain rectangle bar is confirmed to feel good — item 2's own
  `_ESTIMATE_CAP`/monospace-digit notes are a reminder of how many small legibility details
  a bar like this accumulates once you actually look at it moving.
- Item 11's continuous-silence timer is not wasted once the bar exists — it is still the
  right way to decide when to change the bar's *color* (e.g. the same red clip-highlight
  micui uses, repurposed as a "nothing for 5 s" cue) rather than only relying on the user
  to notice a flat bar on their own. The text warning in item 11 becomes a fallback for
  that same signal, not the primary one.
- Before any level sample has arrived (the first ~100 ms of a recording), there is nothing
  to draw a fraction for — same situation `_ProgressTicker` already handles for
  transcription, and the same fix applies: show plain `REC ...` text for that first tick,
  then switch to the bar once real data exists, rather than drawing a fake empty bar.
- Keep the label minimal while the bar is up (`REC`, not a full sentence) — the whole
  point is to stop fighting the bar for space in a small tooltip window.

---

## 13. A second, distinct run-on-tail degeneration — ALL-CAPS on the *sequential* path, not reproducible from the archived clip

**Why this is here:** the owner reported `ptt_20260817_230856_13` (`dataset/`, 162.5 s,
23:07 on 2026-08-17) coming back correctly transcribed up through "...и так мы
двигаемся. Но..." and then flipping to a run-on, almost unpunctuated, **ALL-CAPS** tail
for the rest of the utterance (log: `1451 chars | rec 162.5s | asr 75.0s`). This looks
like the run-on-lowercase bug already in CLAUDE.md's "Known limitations", but that entry's
root cause is specific to `BatchedInferencePipeline` skipping resegmentation on a single
huge VAD chunk — and this utterance did **not** go through the batched path.

**Confirmed from code + settings, not assumed:** `settings.json` on this machine has no
`"batching"` key, so `settings.effective_batching()` resolves to `config.py`'s
`BATCHING_ENABLED = False` (set earlier the same day by item 9). `Engine.transcribe()`
only takes the `BatchedInferencePipeline` branch when `self.batching and duration >
BATCH_ABOVE_SEC`; with `self.batching` false, this utterance — despite being 10x over
`BATCH_ABOVE_SEC` — went through plain `WhisperModel.transcribe` (sequential). So the
already-documented batching root cause does not apply here; this is a different failure
reachable from the path that was supposed to be the safe one.

**What was measured (2026-08-17, this machine, GTX 1650 Ti):** the archived
`dataset/ptt_20260817_230856_13.mp3` decoded through `WhisperModel.transcribe` with the
exact production options (`hotwords`, `initial_prompt` from `HOTWORDS`, `vad_filter=True`,
`compute_type=int8_float16` from `settings.json`, everything else at faster-whisper's
defaults, i.e. `condition_on_previous_text=True` and the built-in `(0.0, 0.2, 0.4, 0.6,
0.8, 1.0)` temperature-fallback schedule):

- **6 repeats, byte-identical every time, no degeneration.** 17 segments, 1625 chars,
  46–52 s each (one outlier at 81 s, same text — a scheduling hiccup, not a different
  decode). Every segment's `temperature` was `0.0` (the fallback schedule never had to
  fall back) and the upper-case letter fraction never exceeded 0.07 outside single
  interjections ("Вот.", "Но...") that are legitimately capitalised. `VAD filter removed
  0.3 s` of the 162.7 s clip — essentially the same "no real pause anywhere" shape as
  production's own log line (`VAD filter removed 00:00.272`).
- The bug **did not reproduce**, on the exact code path, the exact options, and (per
  `settings.json`) the exact compute type production used.

**What that non-reproduction means:** VAD applies before either branch and found almost
no silence in both the buggy run and every local repeat — consistent, not the
differentiator. The sequential path's own ~30 s internal windowing (timestamp tokens,
`condition_on_previous_text`) is exactly the mechanism CLAUDE.md credits with fixing the
batched bug, and it ran here too, so "no resegmentation at all" is not what happened
either. What's left is that the archived clip differs from what production actually fed
the model: `dataset/*.mp3` is encoded by `archive.py` from the same float32 buffer the
live model saw, but **mp3 is lossy** — decoding it back is not byte-identical to the
original PCM. The determinism measured above (6/6 identical) rules out GPU-kernel
nondeterminism as the reason it doesn't reproduce; it does not rule out the mp3 round-trip
having perturbed the samples just enough to avoid whatever knife-edge triggered the
original decode. There is no way to test that directly — the raw pre-encode PCM was never
kept (nothing in this project's design keeps it; see "Archiving" in CLAUDE.md).

**Two levers tried anyway, against the (non-buggy) archived clip, as a check on whether
either is worth reaching for if this recurs:**

| variant | segments | chars | notes |
| --- | ---: | ---: | --- |
| production options (baseline) | 17 | 1625 | clean, correct case throughout |
| `condition_on_previous_text=False` | 21 | 1051 | **35% less content** — the clip fragments into short 1–3 s segments once context stops carrying forward, and the tail degrades into a hallucinated echo of the `HOTWORDS` term list (`"StandaloneTrackingSocket RemoteTracking ImGui nRF52840 cpu"` as if spoken) — the exact echo-the-prompt-back failure `INITIAL_PROMPT` was added to suppress, per CLAUDE.md |
| no `hotwords`/`initial_prompt` | 18 | 1376 | stays correctly cased, but the tail decodes into `"Спасибо за просмотр!"` — the trailing-silence subtitle hallucination the initial prompt and `HALLUCINATIONS` blocklist exist to stop |

Neither lever is a safe general fix on this evidence: turning off context conditioning
traded the (unreproduced) caps bug for a documented, worse failure (prompt-echo
hallucination, and a third of the transcript missing); dropping the prompt reintroduced
the subtitle-credit hallucination the prompt was added to kill. `float16` compute type was
not tested against this clip — a second model instance approached this GPU's 4 GB VRAM
ceiling with the live app already resident and was killed rather than risk destabilising
the machine's own dictation; this remains unverified here and would need either the RTX
5080 or a moment where the live app can be stopped first.

**Best-supported hypothesis, not proven:** this is Whisper's own documented long-context
failure mode — a temperature-fallback on one segment (quality thresholds failed at
temperature 0, so the decode retried at a higher temperature) can leave the model "stuck"
producing degenerate text (unpunctuated, or here upper-case) for the remainder of the
audio once `condition_on_previous_text` feeds that segment's text forward, because there
is no later point where the context resets. This is reported independently of this
project: [SYSTRAN/faster-whisper#1130](https://github.com/SYSTRAN/faster-whisper/issues/1130)
("some English is output as all uppercase" — unresolved, no fix merged) and
[openai/whisper#194](https://github.com/openai/whisper/discussions/194) (long audio
"stuck in a no-punctuation mode" partway through, a maintainer attributing it to the
temperature-fallback prompt-reset rule and the model then "deciding to go without
punctuation from there" with no recovery). Both are the same shape as this utterance: long
audio, no clean recovery point, one bad segment poisoning everything after it — plausible,
not confirmed, because the production run's own per-segment `temperature`/`avg_logprob`
were never logged and the exact input that triggered it cannot be replayed.

**Relationship to the batched/lowercase bug in CLAUDE.md:** same broad shape (very long,
pause-free speech losing casing/punctuation control for its tail) but a **different
mechanism** — that one is "no resegmentation happens at all" (batched VAD chunking, fixed
by not batching); this one happens on the very path that was supposed to be safe from it,
and best evidence points at Whisper's own temperature-fallback context-collapse rather
than anything specific to this project's chunking. Kept as a separate item rather than
folded into that bullet for that reason — CLAUDE.md's bullet now cross-references this one
instead of restating it.

**Diagnostics added, 2026-08-17, so the next occurrence doesn't need a repro session:**
`asr.py`'s `Engine.transcribe()` now logs which pipeline actually ran
(`pipeline=sequential` / `pipeline=batched`) plus one summary line per real utterance —
segment count, the worst `avg_logprob`/`no_speech_prob`/`compression_ratio` seen, and how
many segments needed a temperature fallback. If any segment looks like this bug (an
all-caps segment, or a long segment with almost no punctuation — both known shapes, see
`asr.py`'s `_Diagnostics`) it also logs the full per-segment breakdown at `WARNING`, so the
evidence needed to confirm or rule out the temperature-fallback hypothesis is captured
automatically instead of depending on the dataset clip still reproducing it later.

**Next step if it recurs:** read the new per-segment log line for that utterance first —
if `temperature_fallbacks > 0` on or just before the point where the transcript degrades,
that confirms the hypothesis above and the fix to look at is upstream (there is no
supported "reset context after a bad segment" option in faster-whisper today per #1130).
If `temperature_fallbacks` is 0 throughout, the hypothesis is wrong and this needs
reopening from scratch with real per-segment evidence for the first time.
