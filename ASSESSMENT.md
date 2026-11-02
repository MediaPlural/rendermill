# rendermill — ASSESSMENT

**Verdict:** the fork was not thin in features. It was thin in *foundations* —
the render could work, but nothing under it held. It could not install from a
clean clone, its timeline was arithmetic wearing a measurement's clothes, a
failed caption burn still exited 0, and deck styling was whatever CSS a
previous run happened to leave behind.

That is now fixed. Everything below was verified by execution, not by reading.

---

## What was wrong (12 defects, all reproduced)

### The tool did not install or run

| # | Defect | Evidence |
|---|---|---|
| D1 | `pyproject.toml` had an unterminated string — commit `d4d3804` truncated it mid-sentence | `pip install -e .` → `TOMLDecodeError: Illegal character '\n' (line 8, col 97)`. **A clean clone could not install at all.** |
| D2 | Console script pointed at a deleted module: `from video_claw.cli import main` | `rendermill --version` → `ModuleNotFoundError`. Every venv installed before the rename was dead. |
| D3 | `imageio-ffmpeg` was the *only* source of libass, and it was undeclared | Homebrew ffmpeg: `ass filter = False`. imageio binary: `ass filter = True`. The README told users to `pipx inject` it — a manual step documented around a missing dependency. |

### The timeline was assumed, not measured

| # | Defect | Evidence |
|---|---|---|
| D5 | Slide offsets were `cumulative += dur + 0.15`, assuming each slide MP4 equals its audio plus the pad. Captions, gates and clip cuts all inherited the guess | Measured per-slide error: −0.026s, +0.000s, −0.004s, −0.011s, −0.000s. Master 48.062s vs sum of slides 48.015s — **+0.046s** apart. It cancelled by luck; nothing guaranteed it. |
| D6 | The `0.15` constant was hand-copied into 4 files, one with a comment admitting *"must stay in sync"* | `core.py`, `ffmpeg_video.py`, `gates.py`, `clips.py` |

### Failure was invisible

| # | Defect | Evidence |
|---|---|---|
| D4 | Caption-burn failure was caught, logged, and **exited 0** | `except Exception: shutil.copyfile(raw, out_path)`. A caller could not tell a captioned render from an uncaptioned one. |

### Deck consistency was the author's problem

| # | Defect | Evidence |
|---|---|---|
| D8 | No theme system. Styling was a per-project hand-rolled `_shared.css`; templates hardcoded their own colours | 5 demo slides each restating the palette. No `theme` key in `config.py`. |
| D7 | Captions chunked proportionally to character count, with no notion of punctuation | Split `"…but shipped / a broken install"` mid-phrase |
| D9 | `batch --out` was passed into every project's render | All projects wrote to the same file; last one silently won |
| D10 | `batch.run_batch` was dead code with a `skipped` list nothing appended to; `cmd_batch` duplicated the logic | `run_batch` never called |
| D11 | Pre-rename identifiers survived: `MNV_FORCE_REGEN` (not `RENDERMILL_FORCE_REGEN`, so **force-regen was unreachable**), `video-claw` in templates | Repo-wide scan |

---

## What was upgraded

**Installable and runnable.** `pyproject.toml` repaired; console script resolves
`rendermill.cli:main`; `imageio-ffmpeg` promoted to a declared dependency — on
this machine it is load-bearing, not cosmetic, because homebrew ffmpeg has no
`ass` filter.

**A measured timeline (`timeline.py`).** New. `build_timeline()` probes every
*encoded* slide MP4 with ffprobe; captions are now built in a **new phase 3b,
after all slides encode**, against measured `start_s`. Gates and clip cuts read
the same spans. No downstream code adds up audio durations anymore. If ffprobe
is missing or returns garbage, the render **fails loudly** — it never falls back
to arithmetic. `drift_report()` prints how wrong the old assumption would have
been, so the fix is visible in the render report rather than merely asserted.

**One pad constant.** `PAD_S` lives only in `timeline.py`; `gates.py`,
`clips.py` and `ffmpeg_video.py` import it. `gates.SLIDE_PAD_S` is kept as an
alias for back-compat. The *"must stay in sync"* comment is gone because
nothing *can* drift now. A test asserts identity, and rejects any module that
re-defines the literal.

**Failure is loud.** A failed caption burn now reports `burned: false` plus the
ffmpeg error in `rendermill-report.json` and prints a `[done] WARNING`.

**Captions that break on sentences.** Punctuation-aware: sentence-end ≥3 words,
clause-break ≥5, hard cap 8. The mid-phrase splits are gone.

**A theme system (`theme.py`).** Four built-ins (`amber`, `field`, `signal`,
`paper`) plus per-token dict overrides. One declaration styles every slide
**and** the burned captions. A typo fails at load with the valid set, not
halfway through a render. Canvas defaults deliberately reproduce the old
hard-coded sizes (44pt, 560px right margin on wide) so opting into a theme does
**not** silently re-flow existing decks. Every theme is contrast-checked in the
test suite (≥4.5:1 body text, ≥3:1 accent/muted).

**`rendermill theme <dir>`** — new subcommand writing the generated `_shared.css`
(`--list` shows the built-ins). Without it the browser side still needed a
hand-edited stylesheet, which is exactly the drift the theme removes.

**A verifier for finished renders (`scripts/verify_render.py`).** The §5.2
gates check *inputs* before money is spent. Nothing checked the *output* — a
render could pass every preflight gate and still ship a file whose captions
never burned. Five checks, non-zero exit so CI can gate on them: container
codecs, container-vs-report duration, timeline seam continuity (no gap, no
overlap), the burn flag, and a **pixel diff** against the pre-caption master
proving caption ink actually landed.

That pixel check is the interesting one, because the obvious version is
useless. Counting bright pixels in the caption band passes on the *uncaptioned*
master: slide content (table rows, panel borders) sits there too — 2.28% bright
uncaptioned vs 4.02% captioned, so any threshold between them is arbitrary.
Diffing the same timestamp against `video_build/concat_raw.mp4` is decisive
instead. Verified as a negative control: **fails on the raw master (0.00%
changed), passes on the real output (7.67%)**. When no pre-caption master
exists it falls back to the weak heuristic and *says so* rather than implying
a guarantee it cannot make.

**Batch that doesn't clobber.** `--out` is a directory; each project writes
`<dir>/<project>.mp4`. Passing a file with N>1 is a hard error, not a silent
overwrite. Failures are counted and reported instead of vanishing.

---

## What was already good (kept)

92 tests were green before and after — **121 now**, no regressions. The content-hash
cache, gate machinery (hook + explainer), clip emission, music bed, cross-platform
local-TTS dispatch and the preview gate are genuinely built. The problem was never
missing features. It was that **nothing above them held**.

---

## What remains (honest)

1. **No audio-level sync verification.** Timeline is measured at the container
   level. Drift *within* a slide (encoder latency inside one MP4) is still
   unmeasured.
2. **Themes cover tokens, not layout.** `h1_size`/`p_size` scale, but a deck
   with wildly different content density per slide still needs per-slide CSS.
   A real layout engine (auto-fit text, overflow detection) does not exist.
3. **Captions are estimated in free mode.** With no TTS word boundaries,
   durations are proportional to characters. Paid providers get real timings.
4. **libass remains a hard requirement** for caption burn. It is now declared,
   but a machine without it still gets no burned captions — it now *says so*
   instead of pretending.
5. **Single-provider TTS regression testing.** Free-mode TTS is tested on this
   macOS host only; the Linux/Windows paths are covered by unit tests, not
   real renders.
6. **No `libreoffice`/DOCX slide sources.** HTML only.

---

## Verification

- `121 passed, 1 skipped` (`pytest tests/`)
- `pip install -e .` succeeds; `rendermill --version` resolves
- Real 6-slide demo render produced end-to-end in free mode ($0), 40.37s
- `scripts/verify_render.py` → **all 5 checks pass**, including the pixel diff
  proving caption ink landed (7.67% of the caption band differs from the
  pre-caption master) and background matching the declared `signal` theme
- Negative control: the same verifier **fails** on the pre-caption master, so
  the check is not vacuous
- Demo: `examples/deterministic-demo/` (`rendermill render examples/deterministic-demo`)

The demo's slide 1 was rewritten to 8 words because the §5.2 hook gate rejected
the original 21-word opener at 8.6s against a 3.0s budget. The gate was right,
and it cost nothing to comply.

MIT LICENSE and upstream provenance preserved.