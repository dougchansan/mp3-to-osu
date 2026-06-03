# mp3-to-osu

Automatically generate a playable **osu! standard** beatmap from an audio file.
The pipeline detects tempo and rhythm, then places circles and sliders using
flow heuristics inspired by Monstrata-style mapping (constant-velocity spacing,
momentum-conserving wide angles, smooth curved streams).

## What you need

A **local audio file** — `.mp3`, `.ogg`, `.wav`, or `.flac`. Point the CLI or
the browser studio at it and you get a playable `.osz`. (Streaming links are
DRM-protected and can't be read — supply the audio file yourself.)

## Demo

![mp3-to-osu studio demo](docs/demo.gif)

Live osu!-style auto-play replay synced to the song — spectral visualizer,
multi-lane rhythm ruler, beat-locked hit-sounds, per-section rhythm lock and a
hit-error HUD. *(Silent GIF — the studio itself plays audio.)*


## Install

```powershell
git clone https://github.com/<you>/mp3-to-osu.git
cd mp3-to-osu
pip install -e .

# optional: authentic osu! hit-sounds (downloads osu!'s skin samples locally;
# the studio falls back to a synth click if you skip this)
python scripts/fetch_hitsounds.py
```

## Usage

```powershell
# single difficulty
mp3-to-osu "Artist - Title.mp3" -d hard -o output

# full Easy->Expert spread in one mapset
mp3-to-osu "Artist - Title.mp3" --spread

# fetch audio from a YouTube/SoundCloud URL (needs yt-dlp; NOT Spotify)
mp3-to-osu --url "https://youtu.be/..." --spread
```

Naming the file `Artist - Title.mp3` auto-fills metadata; otherwise pass
`--artist` / `--title`. The result is an `.osz` (and an unzipped folder) in the
output directory — drag it onto osu! or into your `Songs\` folder to play.

### Difficulty presets

`easy`, `normal`, `hard` (default), `insane`, `expert` — each scales circle
size, approach rate, beat subdivision, jump spacing and stream density.
`--spread` emits all five as one mapset sharing the audio.

### Features

- **Curved sliders** — quadratic-bezier arcs whose `length` is the true
  integrated path length, so visual and timing ends always agree.
- **Phrasing-based flow** — accents/downbeats stay clickable circles, weak
  beats become connecting sliders (the classic click-hold-click pulse).
- **Hitsounds** — finish on strong accents, clap on backbeat, whistle on
  sustained sliders.
- **Intro spinner** — auto-added when a track has a long empty lead-in.
- **Breaks** — silent stretches become rest sections.
- **Rank-normalised dynamics** — every map gets real accent contrast even from
  loudness-compressed source audio.

### Optional: URL fetching

```powershell
pip install yt-dlp   # plus ffmpeg on PATH for mp3 extraction
```

## How it works

| Stage | Module | What it does |
|-------|--------|--------------|
| Analyze | `audio.py` | librosa: tempo from **real tracked beats** (robust median), onsets (perc+chroma), per-beat energy |
| Grid | `rhythm.py` | Fine grid **interpolated between real tracked beats** (follows true pulse/tempo drift), not constant offset+k·beatlen |
| Profile | `structure.py` | Sections (drop/verse/…), intensity, auto subdivisions |
| Quantize | `rhythm.py` | Beat-grid timeline; rank-normalised dynamics; phrasing-based sliders; hitsounds; combos; spinner; breaks |
| Place | `patterns.py` | Flow placement + bezier slider arcs (arc-length solved) |
| Serialize | `osu_format.py` | Valid `.osu` v14 (timing, circles, bezier sliders, spinners, hitsounds) |
| Package | `package.py` | Bundles audio + all difficulties into one importable `.osz` |
| Fetch | `fetch.py` | Optional yt-dlp audio download from a URL |
| Learn | `learn/` | Parse a real-map corpus → per-mapper `StyleProfile`s |
| Style | `style.py` | Maps a profile (+UI/CLI overrides) to generator knobs |
| Studio | `web/` | stdlib server + canvas auto-play replay & tuning UI |

## Style learning (analyzer)

Instead of only hand-tuned heuristics, the `mp3_to_osu.learn` subsystem reads a
corpus of real downloaded `.osu` maps and measures *how they are built*, so a
specific mapper's style can be modelled and (later) reproduced.

```powershell
# which mappers in your library have enough maps to model?
mp3-to-osu-learn survey --songs "F:\osu\Songs"

# one profile per mapper (>=4 maps) + a combined ALL + _index.json
mp3-to-osu-learn analyze-all --songs "F:\osu\Songs" --min-maps 4

# build/print a single profile, or re-print a saved one
mp3-to-osu-learn analyze --mapper "Monstrata" --songs "F:\osu\Songs"
mp3-to-osu-learn show --mapper Monstrata
```

Per map it extracts: object mix, rhythm-gap distribution (1/4…2/1), object-type
transition matrix, distance-spacing-vs-time-gap regression (jump velocity, CS-
normalised), flow-angle statistics, new-combo cadence, slider-duration and
curve-type distributions, and stream rate. These aggregate per mapper into a
`StyleProfile` JSON.

### Generate in a learned style

```powershell
mp3-to-osu "song.mp3" --style monstrata          # uses ./profiles/monstrata.json
mp3-to-osu "song.mp3" --style profiles/Sotarks.json
```

`--style` drives CS/AR/OD, slider ratio, slider hold, combo cadence, spacing
slope/jump, flow angles and slider-curve mix from the profile (hybrid: audio
analysis still sets the rhythm; the profile shapes everything else).

## Song-reactive structure

`structure.py` profiles the track (beat-synced loudness + percussive drive +
onset density + timbre segmentation) into sections — intro / build / verse /
chorus / drop / break / outro — each with an intensity and an **auto
playability-gated max subdivision**. The generator reacts like a human mapper:

- **Drops & choruses** ride a continuous 1/2 pulse with 1/4 (and finer, when
  the BPM allows) bursts, bigger spacing, punchy finish/clap hitsounds.
- **Verses & builds** stay calmer — roughly one object per beat, bigger single
  jumps, lighter hitsounds.
- **Breaks/intros** thin right out; silence becomes rest.
- Subdivisions 1/8–1/32 unlock **only** where onset density and BPM keep a
  packed stream humanly playable (≤ ~15 hits/s), so high-BPM songs never
  degrade into unplayable spam.
- Hitsounds always land on grid/onset-aligned objects, so they stay locked to
  the beat. (Limitation: this is heuristic — it models melodic vs percussive
  activity, not true vocal/adlib stem separation.)

## Studio (browser tool)

An interactive local tool to tune the style and **watch an osu!-style auto-play
replay synced to the song** before importing.

```powershell
mp3-to-osu-web                       # then open http://127.0.0.1:8000
mp3-to-osu-web --port 8731 --profiles profiles
```

Pick an audio file and mapper profile, tweak the difficulty + style sliders,
**Generate**, then **Play** to watch approach circles, sliders with a moving
ball and an auto cursor — with a **live audio-spectrum visualizer**,
**beat-synced hit-sounds** (scheduled on the Web Audio clock so they never
drift), a **current-section readout**, and a window-fitting responsive canvas.
Audio-synced and seekable, then **Download .osz**. Pure stdlib backend (no
extra deps); audio is streamed with HTTP Range so the timeline scrubs
smoothly.

## Song ID & lyrics (studio)

Click **Identify (Shazam)** to fingerprint the track via `shazamio` (optional,
unofficial — `pip install shazamio`). It fills artist/title and, when Shazam
has them, the lyrics. You can also paste lyrics manually. With "use lyric lines
to drive combos" on, lines are spread across the song's vocal-active sections,
snapped to nearby onsets, used to anchor new combos, and shown as a **karaoke
overlay** in the replay.

> Honest limits: Shazam's API is unofficial/fragile and its lyrics (when
> present) have **no timestamps**, so line timing is *heuristic* (section +
> onset aligned), not frame-exact. ID itself is reliable.

The studio visualizer has five modes — **Bars / Area / Waveform / Bands /
Spectrogram** — directly above the rhythm ruler so spectral energy lines up
with the beat/bar ticks.

**Bands** shows five instrument-labelled frequency zones — SUB (kick/sub),
BASS (bassline), LOWMID (gtr body/snare), MELODY (lead/vox/gtr), AIR
(cymbals/hats) — each with a peak-hold line and a **transient flash** the
instant that range spikes (an instrument "hits"). **Spectrogram** scrolls
frequency-vs-time so you can read exactly what fires when.

The **rhythm ruler is multi-lane**: a bar-number header plus one row for the
map's objects and **one row per instrument zone**, every row sharing the same
bar/beat vertical grid and centre playhead. So you see, simultaneously, when
the kick / bass / melody / cymbals each hit relative to the beat — no
selector, all instruments at once. (These are frequency-range approximations —
a spectrum can't truly isolate instruments without stem separation.)

## Limitations & next steps

- It approximates mapping *principles*, not a specific human's taste. Treat the
  output as a strong first pass — open it in the osu! editor to polish.
- Difficulty differentiation is mostly CS/AR/OD + spacing/streams; on very dense
  tracks all spreads have similar object counts (the beats dominate).
- Very ambient/beatless tracks fall back to a steady beat-grid map.
- Multi-segment "wave" sliders, kiai-time highlighting, and per-section SV
  changes are possible future refinements.

## Test

`python tests/make_click_and_run.py` generates a 128 BPM click track, runs the
full pipeline, and validates the emitted `.osu` (format header, sections,
on-field coordinates, timing accuracy, slider lengths).

## License & assets

Source code is **MIT** (see `LICENSE`). That license covers the code only —
**not** any beatmaps, music, replays, or osu! assets:

- **osu! hit-sound samples are not bundled.** Run `scripts/fetch_hitsounds.py`
  to download osu!'s default-skin samples locally for personal use; they remain
  the property of osu! / ppy Pty Ltd.
- **No beatmaps or audio are included.** Supply your own audio. Any downloaded
  map corpus (`corpus_fetch/`) is gitignored because it contains other
  creators' copyrighted maps. The learned `profiles/` *are* included — they
  hold only aggregate statistics (no map or audio data).

This is an independent, non-commercial tool, not affiliated with or endorsed by
osu!. Use of the osu! API is subject to osu!'s Terms of Service.
