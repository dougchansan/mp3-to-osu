"use strict";
// mp3-to-osu studio: responsive osu!-style auto-play replay with a live
// audio-spectrum visualizer and beat-synced hit-sounds.

const $ = (s) => document.querySelector(s);
const cv = $("#view"), ctx = cv.getContext("2d");
const audio = $("#audio");
const PF_W = 512, PF_H = 384;
const COMBO = ["#ff5d5d", "#5dff9b", "#5db8ff", "#ffd24a",
               "#c98bff", "#ff8bd1"];
const SECT_COL = {
  intro: "#3a3f55", build: "#4a6bce", verse: "#46836b",
  chorus: "#c98a3a", drop: "#d6477a", break: "#34384a", outro: "#3a3f55",
};

let MAP = null, KF = [], raf = 0;
let TRAIL = [];           // recent cursor positions for the tracer
let VIEW = { S: 1, ox: 0, oy: 0, visH: 70, W: 0, H: 0 };

// ---- responsive sizing --------------------------------------------------
let DESIRED_RES = null;        // [w,h] from the resolution dropdown
function fit() {
  const wrap = $("#canvasWrap");
  const dpr = window.devicePixelRatio || 1;
  let W = Math.max(320, wrap.clientWidth) * dpr;
  let H = Math.max(240, wrap.clientHeight) * dpr;
  if (DESIRED_RES) { W = DESIRED_RES[0]; H = DESIRED_RES[1]; }
  cv.width = W; cv.height = H;
  const visH = Math.round(Math.min(120, Math.max(56, H * 0.14)));
  // Ruler is now multi-lane: a bar-number header + one lane for map objects
  // + one lane per instrument zone. Size lanes to fit, capped so the
  // playfield stays usable.
  const headerH = Math.max(15, Math.round(H * 0.022));
  const rows = ZONES.length + 6; // BEAT+OBJ+ONSET+LEAD+TAP+REPLAY+zones
  const capH = H * 0.42;
  let laneH = Math.max(12, Math.round(H * 0.027));
  let rulerH = headerH + rows * laneH + 4;
  if (rulerH > capH) {
    laneH = Math.max(8, Math.floor((capH - headerH - 4) / rows));
    rulerH = headerH + rows * laneH + 4;
  }
  const pfH = H - visH - rulerH;
  const S = Math.min(W / PF_W, pfH / PF_H);
  VIEW = { S, visH, rulerH, headerH, laneH, W, H, pfH,
    gut: 58,                   // left gutter for lane labels
    visY: pfH,
    rulerY: pfH + visH,
    ox: (W - PF_W * S) / 2,
    oy: (pfH - PF_H * S) / 2 };
}
const X = (x) => VIEW.ox + x * VIEW.S;
const Y = (y) => VIEW.oy + y * VIEW.S;
new ResizeObserver(fit).observe($("#canvasWrap"));

// ---- web audio: analyser + hit-sound synth ------------------------------
let AC = null, analyser = null, freq = null, wave = null, hsGain = null;
let metroGain = null;                       // separate metronome bus
let detAnalyser = null, detFreq = null;     // un-smoothed: transient detect
let SR = 44100;
function initAudio() {
  if (AC) return;
  AC = new (window.AudioContext || window.webkitAudioContext)();
  SR = AC.sampleRate || 44100;
  const srcNode = AC.createMediaElementSource(audio);
  analyser = AC.createAnalyser();
  analyser.fftSize = 2048;                 // ~21 Hz bins for detail
  analyser.smoothingTimeConstant = 0.7;    // visual smoothing only
  freq = new Uint8Array(analyser.frequencyBinCount);
  wave = new Uint8Array(analyser.fftSize);
  // Separate tap with NO time-smoothing + more low-end resolution: smoothing
  // flattens the very transients we want to detect (esp. bass kicks).
  detAnalyser = AC.createAnalyser();
  detAnalyser.fftSize = 4096;              // ~10.7 Hz bins -> better bass
  detAnalyser.smoothingTimeConstant = 0.0;
  detAnalyser.minDecibels = -90;
  detAnalyser.maxDecibels = -5;            // headroom so loud bass != pinned
  detFreq = new Uint8Array(detAnalyser.frequencyBinCount);
  hsGain = AC.createGain();
  hsGain.gain.value = (+$("#volHits").value) * 2;   // 2x scale
  metroGain = AC.createGain();
  metroGain.gain.value = +$("#volMetro").value;
  srcNode.connect(analyser);
  srcNode.connect(detAnalyser);            // analyser node needs no output
  analyser.connect(AC.destination);
  hsGain.connect(AC.destination);
  metroGain.connect(AC.destination);
  loadSamples();                           // real osu! hitsounds (async)
}

// ---- real osu! default-skin samples (open-source ppy/osu-resources) ------
// The genuine "normal" sample set + slider tick, decoded once into
// AudioBuffers. blip()/sliderTick() play these for an authentic osu! feel
// and fall back to the synth if a sample fails to load (e.g. offline).
const SAMPLES = {};                         // name -> AudioBuffer
let SAMPLES_READY = false;
const SAMPLE_FILES = {
  hitnormal:  "normal-hitnormal.wav",
  hitwhistle: "normal-hitwhistle.wav",
  hitfinish:  "normal-hitfinish.wav",
  hitclap:    "normal-hitclap.wav",
  slidertick: "normal-slidertick.wav",
};
async function loadSamples() {
  if (!AC || SAMPLES_READY) return;
  await Promise.all(Object.entries(SAMPLE_FILES).map(async ([k, f]) => {
    try {
      const r = await fetch("/static/sounds/" + f);
      if (!r.ok) throw new Error("HTTP " + r.status);
      SAMPLES[k] = await AC.decodeAudioData(await r.arrayBuffer());
    } catch (e) { /* this sample stays on the synth fallback */ }
  }));
  SAMPLES_READY = !!SAMPLES.hitnormal;       // need at least the base hit
}
function playSample(when, name, gain) {
  const buf = SAMPLES[name];
  if (!buf) return false;
  const src = AC.createBufferSource();
  src.buffer = buf;
  const g = AC.createGain();
  g.gain.value = gain == null ? 1 : gain;
  src.connect(g); g.connect(hsGain);
  src.start(Math.max(when, AC.currentTime)); register(src);
  return true;
}
// Every scheduled source is registered so it can be cancelled the instant
// the song ends / is paused (otherwise the 200 ms look-ahead leaves a few
// blips that fire after the audio has stopped).
let liveNodes = [];
function register(node) {
  liveNodes.push(node);
  node.onended = () => {
    const k = liveNodes.indexOf(node);
    if (k >= 0) liveNodes.splice(k, 1);
  };
}
function killScheduled() {
  for (const n of liveNodes.slice()) {
    try { n.stop(); } catch (e) { /* already stopped */ }
    try { n.disconnect(); } catch (e) { /* already gone */ }
  }
  liveNodes.length = 0;
}
function blip(when, kind) {
  // Real osu! samples when available: every hit plays hitnormal, plus the
  // addition (whistle/finish/clap) layered over it - exactly like the game.
  if (SAMPLES_READY) {
    const t = Math.max(when, AC.currentTime);
    playSample(t, "hitnormal", 0.9);
    if (kind === 2) playSample(t, "hitwhistle", 0.85);
    else if (kind === 8) playSample(t, "hitfinish", 0.8);
    else if (kind === 32) playSample(t, "hitclap", 0.85);
    return;
  }
  // ---- synth fallback (offline / samples failed to load) ----
  // Short synthesized osu-style hit. Scheduled on the audio clock so it
  // stays locked to the beat regardless of frame rate.
  const g = AC.createGain();
  g.connect(hsGain);
  const t = Math.max(when, AC.currentTime);
  let dur = 0.05;
  if (kind === 32 || kind === 2) {            // clap / whistle: noise burst
    const n = AC.createBufferSource();
    const buf = AC.createBuffer(1, AC.sampleRate * 0.08, AC.sampleRate);
    const d = buf.getChannelData(0);
    for (let i = 0; i < d.length; i++) d[i] = (Math.random() * 2 - 1);
    n.buffer = buf;
    const bp = AC.createBiquadFilter();
    bp.type = "bandpass";
    bp.frequency.value = kind === 2 ? 2600 : 1500;
    bp.Q.value = kind === 2 ? 6 : 2;
    n.connect(bp); bp.connect(g);
    dur = 0.07;
    n.start(t); n.stop(t + dur); register(n);
  } else {                                    // normal / finish: tonal click
    const o = AC.createOscillator();
    o.type = kind === 8 ? "sine" : "triangle";
    o.frequency.value = kind === 8 ? 320 : 950;
    o.connect(g);
    dur = kind === 8 ? 0.07 : 0.04;            // short = crisp, not boomy
    o.start(t); o.stop(t + dur); register(o);
  }
  // near-instant attack so the perceived hit is exactly on `t`
  g.gain.setValueAtTime(0.0001, t);
  g.gain.exponentialRampToValueAtTime(kind === 8 ? 0.85 : 0.6, t + 0.0015);
  g.gain.exponentialRampToValueAtTime(0.0001, t + dur);
}

// ---- precise transport clock -------------------------------------------
// `audio.currentTime` only updates a few times/sec and lags the real output,
// so deriving timing from it every frame makes hits wobble + feel late.
// Instead anchor ONCE when playback actually starts and read the position
// from the sample-accurate AudioContext clock.
let clkCtx0 = 0, clkMedia0 = 0, clkPerf0 = 0, OFFSET_MS = 0;
function reanchor() {
  if (AC) {
    clkCtx0 = AC.currentTime;
    clkMedia0 = audio.currentTime;
    clkPerf0 = performance.now();         // bridges perf <-> AC for events
  }
}
function songMs() {
  if (!AC || audio.paused) return (audio.currentTime || 0) * 1000;
  return (AC.currentTime - clkCtx0 + clkMedia0) * 1000;
}
// Convert a perf-domain timestamp (event.timeStamp) into song-position ms
// at the moment the event was hardware-stamped. This is the timestamp the
// browser attaches the instant the OS hands it the key event - it bypasses
// event-queue + handler-dispatch jitter (the few ms wobble you feel).
// `perfMs` defaults to "now" so callers without a timestamp keep working.
function perfToSongMs(perfMs) {
  if (!AC || audio.paused) return (audio.currentTime || 0) * 1000;
  if (perfMs == null) return songMs();
  // AC.currentTime at perfMs is clkCtx0 + (perfMs - clkPerf0)/1000, so:
  return clkMedia0 * 1000 + (perfMs - clkPerf0);
}

// ---- music-reading metrics ---------------------------------------------
let TRIPLET = false;
// Local tempo at `t`: median interval of the ~6 nearest tracked beats, so
// you can watch the BPM move when the song speeds up / slows down.
function beatIndexAt(B, t) {
  let lo = 0, hi = B.length - 1;
  if (!B.length || t <= B[0]) return 0;
  if (t >= B[hi]) return hi;
  while (lo < hi) {
    const m = (lo + hi) >> 1;
    if (B[m] < t) lo = m + 1; else hi = m;
  }
  return Math.max(0, lo - 1);
}
function localBpm(t) {
  const B = MAP && MAP.beat_times;
  if (!B || B.length < 3) return MAP ? MAP.bpm : 0;
  const i = beatIndexAt(B, t);
  const a = Math.max(1, i - 3), b = Math.min(B.length - 1, i + 3);
  const iv = [];
  for (let k = a; k <= b; k++) iv.push(B[k] - B[k - 1]);
  iv.sort((p, q) => p - q);
  const med = iv[iv.length >> 1] || 0;
  return med > 0 ? 60000 / med : (MAP ? MAP.bpm : 0);
}
// Does the recent onset pattern fit a straight (k/4) or triplet (k/3) grid?
function feelHint(B, onsets, now, win) {
  if (!B || B.length < 2 || !onsets || !onsets.length) return "—";
  let straight = 0, trip = 0, seen = 0;
  for (let j = 0; j < onsets.length; j++) {
    const t = onsets[j];
    if (t < now - win) continue;
    if (t > now + win) break;
    const i = beatIndexAt(B, t);
    if (i + 1 >= B.length) continue;
    const span = B[i + 1] - B[i];
    if (span <= 1) continue;
    const f = (t - B[i]) / span;                 // 0..1 within the beat
    const ds = Math.min(...[0, .25, .5, .75, 1].map((g) => Math.abs(f - g)));
    const dt = Math.min(...[0, 1 / 3, 2 / 3, 1].map((g) => Math.abs(f - g)));
    if (ds < dt) straight++; else if (dt < ds) trip++;
    seen++;
  }
  if (seen < 6) return "—";
  return trip > straight * 1.15 ? "triplet"
       : straight > trip * 1.15 ? "straight" : "mixed";
}

// hit-sound scheduler --------------------------------------------------
let hits = [];          // [{ms, hs}]
let nextHit = 0;
function rebuildHits(objs) {
  hits = [];
  // Emulate osu! slider audio: a HEAD hit (object hitsound), soft SLIDER
  // TICKS along the body (every beat at SliderTickRate 1), and an END hit
  // on the tail edge (normal, matching our generated edgeSounds hs|0).
  const beat = (MAP && MAP.beat_ms) || 500;
  for (const o of objs) {
    if (o.kind === "spinner") continue;
    hits.push({ ms: o.t, hs: o.hs | 0, type: "hit" });
    if (o.kind === "slider") {
      // ticks at k*beat after the head, dropped if too close to the end
      for (let k = 1; o.t + k * beat < o.t + o.dur - beat * 0.25; k++) {
        hits.push({ ms: o.t + k * beat, type: "tick" });
      }
      hits.push({ ms: o.t + o.dur, hs: 0, type: "hit" });
    }
  }
  hits.sort((a, b) => a.ms - b.ms);
  nextHit = 0;
}
// Metronome: a click on EVERY detected beat (accent on the downbeat),
// independent of the generated objects. This is the diagnostic - if these
// clicks don't sit on the song's pulse, beat detection is the problem.
let metro = [];         // [{ms, level}]  level 0=downbeat 1=beat 2=sub
let nextMetro = 0;
function rebuildMetro(beatTimes) {
  metro = [];
  nextMetro = 0;
  const B = beatTimes || [];
  if (B.length < 2) return;
  const div = Math.max(1, parseInt($("#metroDiv").value, 10) || 1);
  for (let i = 0; i < B.length; i++) {
    const span = (i + 1 < B.length) ? B[i + 1] - B[i]
                                    : (B[i] - B[i - 1] || 500);
    for (let s = 0; s < div; s++) {
      const t = B[i] + span * (s / div);
      const level = s !== 0 ? 2 : (i % 4 === 0 ? 0 : 1);
      metro.push({ ms: t, level });
    }
  }
}
function resyncHits() {
  const now = songMs();
  nextHit = hits.findIndex((h) => h.ms >= now - 20);
  if (nextHit < 0) nextHit = hits.length;
  nextMetro = metro.findIndex((m) => m.ms >= now - 20);
  if (nextMetro < 0) nextMetro = metro.length;
  nextTap = tapsSorted.findIndex((t) => t >= now - 20);
  if (nextTap < 0) nextTap = tapsSorted.length;
}
function metroClick(when, level) {
  // level 0 = bar downbeat (high/loud), 1 = beat, 2 = subdivision (soft).
  const o = AC.createOscillator();
  const g = AC.createGain();
  o.type = "square";
  o.frequency.value = level === 0 ? 2000 : level === 1 ? 1300 : 1000;
  o.connect(g); g.connect(metroGain || hsGain);
  const t = Math.max(when, AC.currentTime);
  const dur = level === 2 ? 0.02 : 0.03;
  o.start(t); o.stop(t + dur); register(o);
  const peak = level === 0 ? 1.0 : level === 1 ? 0.55 : 0.28;
  g.gain.setValueAtTime(0.0001, t);
  g.gain.exponentialRampToValueAtTime(peak, t + 0.001);
  g.gain.exponentialRampToValueAtTime(0.0001, t + dur);
}
function tapClick(when) {                 // your recorded taps, played back
  const o = AC.createOscillator();
  const g = AC.createGain();
  o.type = "square";
  o.frequency.value = 880;
  o.connect(g); g.connect(hsGain);
  const t = Math.max(when, AC.currentTime);
  o.start(t); o.stop(t + 0.035); register(o);
  g.gain.setValueAtTime(0.0001, t);
  g.gain.exponentialRampToValueAtTime(0.8, t + 0.001);
  g.gain.exponentialRampToValueAtTime(0.0001, t + 0.035);
}
function sliderTick(when) {
  // osu! slider-tick: a short, soft, bright tick distinct from a hit.
  if (SAMPLES_READY && playSample(when, "slidertick", 0.55)) return;
  const o = AC.createOscillator();
  const g = AC.createGain();
  o.type = "sine";
  o.frequency.value = 1700;
  o.connect(g); g.connect(hsGain);
  const t = Math.max(when, AC.currentTime);
  o.start(t); o.stop(t + 0.025); register(o);
  g.gain.setValueAtTime(0.0001, t);
  g.gain.exponentialRampToValueAtTime(0.32, t + 0.001);
  g.gain.exponentialRampToValueAtTime(0.0001, t + 0.025);
}
function songEndMs() {
  // The TRUE end of playback. The generated map can extrapolate a tail a
  // little past the real audio; never schedule a hit past the audio itself.
  const d = audio.duration;
  if (d && isFinite(d)) return d * 1000;
  return MAP ? MAP.duration_ms : Infinity;
}
function scheduleHits() {
  if (!AC || audio.paused) return;
  // Re-anchor if the precise clock has drifted from the media clock (a
  // stall / unseen seek) so we never accumulate lag.
  if (Math.abs(songMs() - audio.currentTime * 1000) > 250) reanchor();
  const endMs = songEndMs() - 30;            // hard stop before audio ends
  const horizonMs = Math.min(songMs() + 200, endMs);   // bounded look-ahead
  const offS = OFFSET_MS / 1000;
  const at = (ms) => clkCtx0 + (ms / 1000 - clkMedia0) + offS;

  if ($("#hsOn").checked) {
    while (nextHit < hits.length && hits[nextHit].ms <= horizonMs) {
      const h = hits[nextHit++];
      if (h.ms > endMs) continue;
      const when = at(h.ms);
      if (when > AC.currentTime - 0.02) {
        if (h.type === "tick") sliderTick(when);
        else blip(when, h.hs);
      }
    }
  }
  if ($("#metroOn").checked) {
    while (nextMetro < metro.length && metro[nextMetro].ms <= horizonMs) {
      const m = metro[nextMetro++];
      if (m.ms > endMs) continue;
      const when = at(m.ms);
      if (when > AC.currentTime - 0.02) metroClick(when, m.level);
    }
  }
  if ($("#tapPlay").checked) {
    while (nextTap < tapsSorted.length
           && tapsSorted[nextTap] <= horizonMs) {
      const tm = tapsSorted[nextTap++];
      const when = at(tm);
      if (when > AC.currentTime - 0.02) tapClick(when);
    }
  }
}

// ---- geometry helpers ---------------------------------------------------
function preempt(ar) {
  return ar < 5 ? 1200 + 600 * (5 - ar) / 5
       : ar > 5 ? 1200 - 750 * (ar - 5) / 5 : 1200;
}
const radiusPx = (cs) => (54.4 - 4.48 * cs) * VIEW.S;
function qbez(p0, c, p1, t) {
  const u = 1 - t;
  return [u*u*p0[0] + 2*u*t*c[0] + t*t*p1[0],
          u*u*p0[1] + 2*u*t*c[1] + t*t*p1[1]];
}
function sliderPoint(o, pr) {
  if (o.curve === "L")
    return [o.x + (o.ex - o.x) * pr, o.y + (o.ey - o.y) * pr];
  return qbez([o.x, o.y], [o.cx, o.cy], [o.ex, o.ey], pr);
}
function buildKeyframes(objs) {
  const k = [];
  for (const o of objs) {
    if (o.kind === "spinner") {
      k.push({ t: o.t, x: 256, y: 192 });
      k.push({ t: o.end, x: 256, y: 192 });
    } else if (o.kind === "slider") {
      k.push({ t: o.t, x: o.x, y: o.y });
      k.push({ t: o.t + o.dur, x: o.ex, y: o.ey });
    } else k.push({ t: o.t, x: o.x, y: o.y });
  }
  return k;
}
function objEnd(o) {
  return o.kind === "slider" ? o.t + o.dur
       : o.kind === "spinner" ? o.end : o.t;
}
function objEndPos(o) {
  return o.kind === "slider" ? [o.ex, o.ey]
       : o.kind === "spinner" ? [256, 192] : [o.x, o.y];
}
// osu! Auto-style cursor: sits exactly on each circle at its hit time,
// FOLLOWS the slider curve while a slider is active, spins on spinners, and
// glides between notes arriving precisely on the next one.
function cursorAt(now) {
  const O = MAP && MAP.objects;
  if (!O || !O.length) return [256, 192];
  let lo = 0, hi = O.length - 1, idx = -1;
  while (lo <= hi) {
    const m = (lo + hi) >> 1;
    if (O[m].t <= now) { idx = m; lo = m + 1; } else hi = m - 1;
  }
  if (idx < 0) return [O[0].x, O[0].y];
  const cur = O[idx];
  if (now <= objEnd(cur)) {                 // an object is active
    if (cur.kind === "slider") {
      const pr = Math.max(0, Math.min(1,
        (now - cur.t) / Math.max(1, cur.dur)));
      return sliderPoint(cur, pr);
    }
    if (cur.kind === "spinner") {
      const a = (now - cur.t) / 90;
      return [256 + 72 * Math.cos(a), 192 + 72 * Math.sin(a)];
    }
    return [cur.x, cur.y];                   // circle: exactly on it
  }
  const nx = O[idx + 1];
  const [ax, ay] = objEndPos(cur);
  const at = objEnd(cur);
  if (!nx) return [ax, ay];
  const f = nx.t === at ? 1
    : Math.max(0, Math.min(1, (now - at) / (nx.t - at)));
  const e = f < .5 ? 2 * f * f : 1 - Math.pow(-2 * f + 2, 2) / 2;
  return [ax + (nx.x - ax) * e, ay + (nx.y - ay) * e];
}
function sectionAt(ms) {
  if (!MAP || !MAP.sections) return null;
  for (const s of MAP.sections) if (ms >= s.start && ms < s.end) return s;
  return MAP.sections[MAP.sections.length - 1] || null;
}

// ---- render loop --------------------------------------------------------
let VIS_MODE = "bands";
function bandAvgOf(arr, fftSize, loHz, hiHz) {
  const binHz = SR / fftSize;
  const a = Math.max(0, Math.floor(loHz / binHz));
  const b = Math.min(arr.length - 1, Math.ceil(hiHz / binHz));
  let s = 0;
  for (let i = a; i <= b; i++) s += arr[i];
  return b >= a ? s / ((b - a + 1) * 255) : 0;
}
function bandAvg(loHz, hiHz) {        // smoothed, for the visualizer bars
  if (!analyser) return 0;
  return bandAvgOf(freq, analyser.fftSize, loHz, hiHz);
}
// Frequency zones labelled by the instruments that usually live there.
// (A spectrum can't truly separate instruments - this is the honest
// approximation: ranges, not isolated stems.)
const ZONES = [
  { k: "SUB", lo: 20, hi: 60, c: "#ff5d5d", ins: "kick / sub" },
  { k: "BASS", lo: 60, hi: 250, c: "#ff9d3a", ins: "bassline" },
  { k: "LOWMID", lo: 250, hi: 800, c: "#ffd24a", ins: "gtr body / snare" },
  { k: "MELODY", lo: 800, hi: 4000, c: "#5dff9b", ins: "lead / vox / gtr" },
  { k: "AIR", lo: 4000, hi: 16000, c: "#5db8ff", ins: "cymbals / hats" },
];
const zoneState = ZONES.map(() => ({
  slow: 0, prev: 0, val: 0, peak: 0, flash: 0, last: -1e9, hits: [],
  vmin: null, vmax: null,            // per-band auto-range for the visual bar
}));

// Onset/transient detection per zone. Uses the UN-smoothed analyser and a
// ratio-to-running-floor test (not a flux on a clamped value) so loud
// sustained bass no longer pins at 1.0 and swallows its own notes - each
// re-articulation that rises above the slow floor registers as a hit.
function trackZones(nowMs, playing) {
  if (detAnalyser) detAnalyser.getByteFrequencyData(detFreq);
  for (let i = 0; i < ZONES.length; i++) {
    const z = ZONES[i], st = zoneState[i];
    // visual bar = smoothed + per-band AUTO-RANGED so each zone uses its full
    // height and you can read when an instrument actually enters / accents,
    // instead of every bar pinning at the top all song. The ceiling tracks
    // the recent loudest moment (slow decay); the floor creeps up under a
    // sustained tone, so steady-loud content no longer sits at max forever.
    const raw = bandAvg(z.lo, z.hi);
    st.vmax = st.vmax == null ? Math.max(raw, 0.08)
            : Math.max(raw, st.vmax * 0.997, 0.08);          // peak ceiling
    st.vmin = st.vmin == null ? raw
            : (raw < st.vmin ? raw                           // drop instantly
                             : st.vmin + (raw - st.vmin) * 0.0009);  // slow up
    const span = Math.max(st.vmax - st.vmin, 0.04);
    const norm = Math.max(0, Math.min(1, (raw - st.vmin) / span));
    st.val = Math.pow(norm, 0.85);                           // perceptual lift
    st.peak = Math.max(st.val, st.peak * 0.94);
    st.flash *= 0.86;

    const rd = detAnalyser
      ? bandAvgOf(detFreq, detAnalyser.fftSize, z.lo, z.hi) : 0;
    const rising = rd - st.prev;
    st.prev = rd;
    st.slow = st.slow * 0.95 + rd * 0.05;          // adaptive floor
    const ratio = rd / Math.max(st.slow, 0.015);

    if (playing && st.slow > 0.008 && ratio > 1.22 && rising > 0.008 &&
        rd > 0.045 && nowMs - st.last > 80) {
      st.last = nowMs; st.flash = 1;
      st.hits.push(nowMs);
    }
    if (st.hits.length && st.hits[0] < nowMs - 16000) {
      st.hits = st.hits.filter((t) => t >= nowMs - 16000);
    }
  }
}

let specCv = null, specCtx = null, specW = 0, specH = 0;
function drawSpectrogram(y0, h, W) {
  if (!specCv || specW !== (W | 0) || specH !== (h | 0)) {
    specW = W | 0; specH = h | 0;
    specCv = document.createElement("canvas");
    specCv.width = specW; specCv.height = specH;
    specCtx = specCv.getContext("2d");
  }
  const dx = 2;
  specCtx.drawImage(specCv, -dx, 0);                 // scroll left
  specCtx.clearRect(specW - dx, 0, dx, specH);
  const fmin = Math.log10(40), fmax = Math.log10(SR / 2);
  for (let py = 0; py < specH; py++) {
    const fr = fmin + (fmax - fmin) * (1 - py / specH);
    const hz = Math.pow(10, fr);
    const bin = Math.min(freq.length - 1,
      Math.round(hz / (SR / analyser.fftSize)));
    const v = freq[bin] / 255;
    if (v <= 0.02) continue;
    specCtx.fillStyle =
      `hsl(${280 - v * 200}, 90%, ${10 + v * 55}%)`;
    specCtx.fillRect(specW - dx, py, dx, 1);
  }
  ctx.drawImage(specCv, 0, y0);
  // freq guide labels
  ctx.fillStyle = "rgba(207,211,228,0.5)"; ctx.font = "9px Segoe UI";
  for (const hz of [100, 500, 2000, 8000]) {
    const py = y0 + h * (1 - (Math.log10(hz) - fmin) / (fmax - fmin));
    ctx.fillText(hz >= 1000 ? hz / 1000 + "k" : hz + "", 2, py - 1);
  }
}

function drawVisualizer(nowMs) {
  const W = VIEW.W, y0 = VIEW.visY, h = VIEW.visH;
  ctx.fillStyle = "#08080c";
  ctx.fillRect(0, y0, W, h);
  ctx.strokeStyle = "#2c2e3a"; ctx.lineWidth = 1;
  ctx.beginPath(); ctx.moveTo(0, y0); ctx.lineTo(W, y0); ctx.stroke();
  if (!analyser) {
    ctx.fillStyle = "#444"; ctx.font = "11px Segoe UI";
    ctx.fillText("spectrum (press Play)", 8, y0 + 16);
    return;
  }

  if (VIS_MODE === "wave") {
    analyser.getByteTimeDomainData(wave);
    ctx.strokeStyle = "#5db8ff"; ctx.lineWidth = 2;
    ctx.beginPath();
    for (let i = 0; i < wave.length; i++) {
      const x = (i / (wave.length - 1)) * W;
      const yy = y0 + (wave[i] / 255) * h;
      i ? ctx.lineTo(x, yy) : ctx.moveTo(x, yy);
    }
    ctx.stroke();
    return;
  }

  analyser.getByteFrequencyData(freq);
  trackZones(nowMs, !audio.paused);

  if (VIS_MODE === "spectro") {
    drawSpectrogram(y0, h, W);
    return;
  }

  if (VIS_MODE === "bands") {
    const bw = W / ZONES.length;
    ZONES.forEach((z, i) => {
      const st = zoneState[i];
      const x = i * bw;
      const bh = st.val * (h - 26);
      ctx.fillStyle = z.c;
      ctx.globalAlpha = 0.35 + 0.65 * st.val;
      ctx.fillRect(x + 8, y0 + h - bh - 2, bw - 16, bh);
      ctx.globalAlpha = 1;
      // peak-hold line
      const pky = y0 + h - 2 - st.peak * (h - 26);
      ctx.strokeStyle = z.c; ctx.lineWidth = 2;
      ctx.beginPath(); ctx.moveTo(x + 8, pky);
      ctx.lineTo(x + bw - 8, pky); ctx.stroke();
      // transient flash border = this instrument just hit
      if (st.flash > 0.25) {
        ctx.strokeStyle = "#fff";
        ctx.lineWidth = 1 + 3 * st.flash;
        ctx.strokeRect(x + 6, y0 + 4, bw - 12, h - 8);
      }
      ctx.fillStyle = "#eef"; ctx.font = "bold 11px Segoe UI";
      ctx.fillText(z.k, x + 10, y0 + 13);
      ctx.fillStyle = "#9aa0b4"; ctx.font = "9px Segoe UI";
      ctx.fillText(z.ins, x + 10, y0 + 24);
      ctx.fillText((z.lo >= 1000 ? z.lo / 1000 + "k" : z.lo) + "-" +
        (z.hi >= 1000 ? z.hi / 1000 + "k" : z.hi), x + 10, y0 + h - 4);
    });
    return;
  }

  // bars (default) + area (mirrored, filled)
  const n = Math.min(freq.length, 128);
  const bw = W / n;
  ctx.beginPath();
  for (let i = 0; i < n; i++) {
    const v = freq[i] / 255;
    if (VIS_MODE === "area") {
      const x = (i / (n - 1)) * W;
      const yy = y0 + h / 2 - v * (h / 2);
      i ? ctx.lineTo(x, yy) : ctx.moveTo(x, yy);
    } else {
      const bh = v * (h - 4);
      ctx.fillStyle = `hsl(${320 - v * 140}, 85%, ${35 + v * 30}%)`;
      ctx.fillRect(i * bw + 1, y0 + h - bh, bw - 1.5, bh);
    }
  }
  if (VIS_MODE === "area") {
    for (let i = n - 1; i >= 0; i--) {
      const v = freq[i] / 255;
      ctx.lineTo((i / (n - 1)) * W, y0 + h / 2 + v * (h / 2));
    }
    ctx.closePath();
    ctx.fillStyle = "rgba(255,95,180,0.55)";
    ctx.strokeStyle = "#ff8bd1"; ctx.lineWidth = 1.5;
    ctx.fill(); ctx.stroke();
  }
}
function drawRuler(now) {
  const H = VIEW.H, W = VIEW.W, rh = VIEW.rulerH, top = H - rh;
  const hdr = VIEW.headerH, laneH = VIEW.laneH, gut = VIEW.gut;
  ctx.fillStyle = "#101018";
  ctx.fillRect(0, top, W, rh);
  ctx.strokeStyle = "#2c2e3a"; ctx.lineWidth = 1;
  ctx.beginPath(); ctx.moveTo(0, top); ctx.lineTo(W, top); ctx.stroke();
  if (!MAP || !MAP.beat_ms) return;

  const beat = MAP.beat_ms, off = MAP.offset_ms || 0;
  const winMs = beat * 8;                      // ±8 beats visible
  const pw = W - gut;                          // plotting width after gutter
  const xt = (t) => gut + pw / 2 + ((t - now) / winMs) * (pw / 2);
  const lo = now - winMs, hi = now + winMs;
  const lanesTop = top + hdr;
  const lanesBot = H - 2;

  // Real librosa-tracked beats (follow actual tempo). Fall back to a
  // synthetic constant grid only if the analysis didn't return beats.
  const realBeats = (MAP.beat_times && MAP.beat_times.length)
    ? MAP.beat_times : null;
  let gridBeats;
  if (realBeats) {
    gridBeats = [];
    for (let i = 0; i < realBeats.length; i++) {
      const t = realBeats[i];
      if (t < lo) continue;
      if (t > hi) break;
      gridBeats.push([t, i % 4 === 0]);          // [time, isBar]
    }
  } else {
    gridBeats = [];
    const k0 = Math.floor((now - winMs - off) / beat);
    const k1 = Math.ceil((now + winMs - off) / beat);
    for (let k = k0; k <= k1; k++) {
      const t = off + k * beat;
      if (t >= 0) gridBeats.push([t, ((k % 4) + 4) % 4 === 0]);
    }
  }

  for (const [t, bar] of gridBeats) {
    const x = xt(t);
    if (x < gut) continue;
    ctx.strokeStyle = bar ? "#5db8ff" : "#2c3046";
    ctx.lineWidth = bar ? 2 : 1;
    ctx.beginPath(); ctx.moveTo(x, top + 2); ctx.lineTo(x, lanesBot);
    ctx.stroke();
  }

  // Triplet guides: split each beat interval into thirds (helps read a
  // triplet/swing feel that a binary grid hides).
  if (TRIPLET && realBeats) {
    ctx.strokeStyle = "rgba(93,255,155,0.30)"; ctx.lineWidth = 1;
    for (let i = 0; i < realBeats.length - 1; i++) {
      const b0 = realBeats[i], b1 = realBeats[i + 1];
      if (b1 < lo || b0 > hi) continue;
      for (const f of [1 / 3, 2 / 3]) {
        const x = xt(b0 + (b1 - b0) * f);
        if (x < gut) continue;
        ctx.beginPath();
        ctx.moveTo(x, lanesTop); ctx.lineTo(x, lanesBot); ctx.stroke();
      }
    }
  }

  // Tempo curve across the header: watch BPM ramp up/down over the window.
  if (realBeats && realBeats.length > 2) {
    const avg = MAP.bpm || 130;
    const bMin = avg * 0.55, bMax = avg * 1.7;
    const ty = (bpm) => top + 2 + (hdr - 4) *
      (1 - (Math.min(bMax, Math.max(bMin, bpm)) - bMin) / (bMax - bMin));
    ctx.strokeStyle = "rgba(127,134,160,0.5)"; ctx.lineWidth = 1;
    ctx.beginPath(); ctx.moveTo(gut, ty(avg)); ctx.lineTo(W, ty(avg));
    ctx.stroke();
    ctx.strokeStyle = "#ffd24a"; ctx.lineWidth = 1.5;
    ctx.beginPath();
    let started = false;
    for (let i = 1; i < realBeats.length; i++) {
      const t = realBeats[i];
      if (t < lo) continue;
      if (t > hi) break;
      const bpm = 60000 / Math.max(1, realBeats[i] - realBeats[i - 1]);
      const x = xt(t), yy = ty(bpm);
      if (x < gut) continue;
      started ? ctx.lineTo(x, yy) : ctx.moveTo(x, yy);
      started = true;
    }
    ctx.stroke();
  }

  // lanes: BEAT | OBJ | ONSET | LEAD (main melody) | each ZONE
  const lanes = [
    { k: "BEAT", c: "#5db8ff" },
    { k: "OBJ", c: "#ff8bd1" },
    { k: "ONSET", c: "#9aa0b4" },
    { k: "LEAD", c: "#b388ff" },
    { k: "TAP", c: "#ff9d3a" },
    { k: "REPLAY", c: "#3ad6c0" },
    ...ZONES.map((z) => ({ k: z.k, c: z.c })),
  ];
  const laneY = (i) => lanesTop + i * laneH;
  const onsets = MAP.onset_times || [];

  ctx.textBaseline = "middle";
  for (let i = 0; i < lanes.length; i++) {
    const y = laneY(i), cy = y + laneH / 2;
    if (i % 2) {
      ctx.fillStyle = "rgba(255,255,255,0.025)";
      ctx.fillRect(gut, y, pw, laneH);
    }
    ctx.strokeStyle = "#23252f"; ctx.lineWidth = 1;
    ctx.beginPath(); ctx.moveTo(gut, y); ctx.lineTo(W, y); ctx.stroke();
    ctx.fillStyle = lanes[i].c; ctx.font = "bold 10px Segoe UI";
    ctx.fillText(lanes[i].k, 6, cy);
    const th = Math.max(5, laneH * 0.62);
    ctx.fillStyle = lanes[i].c;

    if (lanes[i].k === "BEAT") {
      for (const [t, bar] of gridBeats) {
        const x = xt(t);
        if (x < gut) continue;
        ctx.fillRect(x - (bar ? 1.5 : 1), cy - th / 2,
          bar ? 3 : 2, th);
      }
    } else if (lanes[i].k === "OBJ") {
      for (const o of MAP.objects) {
        if (o.t < lo || o.t > hi) continue;
        const x = xt(o.t);
        if (x >= gut) ctx.fillRect(x - 1, cy - th / 2, 2, th);
      }
    } else if (lanes[i].k === "ONSET") {
      for (let j = 0; j < onsets.length; j++) {
        const t = onsets[j];
        if (t < lo) continue;
        if (t > hi) break;
        ctx.fillRect(xt(t) - 0.5, cy - th / 2, 1, th);
      }
    } else if (lanes[i].k === "TAP") {
      for (let j = 0; j < tapsSorted.length; j++) {
        const t = tapsSorted[j];
        if (t < lo) continue;
        if (t > hi) break;
        const x = xt(t);
        if (x >= gut) ctx.fillRect(x - 1.5, cy - th / 2, 3, th);
      }
    } else if (lanes[i].k === "REPLAY") {
      const ps = REPLAY && REPLAY.presses;
      if (ps) {
        for (let j = 0; j < ps.length; j++) {
          const t = ps[j];
          if (t < lo) continue;
          if (t > hi) break;
          const x = xt(t);
          if (x >= gut) ctx.fillRect(x - 1.5, cy - th / 2, 3, th);
        }
      }
    } else {
      const zk = lanes[i].k;
      // Map lane -> its zoneState by NAME (robust to extra lanes like LEAD).
      const zsi = ZONES.findIndex((z) => z.k === zk);
      const st = zsi >= 0 ? zoneState[zsi] : null;
      // Offline onsets (accurate; LEAD = predominant melody); fall back to
      // the live analyser hits only if the server didn't provide them.
      const bo = MAP.band_onsets && MAP.band_onsets[zk];
      if (bo && bo.length) {
        for (let j = 0; j < bo.length; j++) {
          const t = bo[j];
          if (t < lo) continue;
          if (t > hi) break;
          const x = xt(t);
          if (x >= gut) ctx.fillRect(x - 1.5, cy - th / 2, 3, th);
        }
      } else if (st) {
        for (const ht of st.hits) {
          if (ht < lo || ht > hi) continue;
          const x = xt(ht);
          if (x >= gut) ctx.fillRect(x - 1.5, cy - th / 2, 3, th);
        }
      }
      if (st && st.flash > 0.3) {
        ctx.globalAlpha = st.flash;
        ctx.fillRect(gut + pw / 2 - 2, y + 1, 4, laneH - 2);
        ctx.globalAlpha = 1;
      }
    }
  }
  ctx.textBaseline = "alphabetic";

  // center playhead; flash gold when on a real beat (±45 ms)
  let onBeat = false;
  if (realBeats) {
    for (const [t] of gridBeats) {
      if (Math.abs(t - now) < 45) { onBeat = true; break; }
    }
  } else {
    const phase = Math.abs(((now - off) % beat + beat) % beat);
    onBeat = phase < 40 || phase > beat - 40;
  }
  const px = gut + pw / 2;
  ctx.strokeStyle = onBeat ? "#ffd24a" : "#ff5fb4";
  ctx.lineWidth = onBeat ? 3 : 2;
  ctx.beginPath(); ctx.moveTo(px, top); ctx.lineTo(px, H); ctx.stroke();
}

function latencyMs() {
  // What you HEAR lags the AudioContext clock by the output buffer + device
  // latency. Visuals must be delayed by the same amount or circles run ahead
  // of the music (the "out of sync from the start" you saw).
  if (!AC) return 0;
  const o = (AC.outputLatency || 0) + (AC.baseLatency || 0);
  return (isFinite(o) ? o : 0) * 1000;
}
function draw() {
  raf = requestAnimationFrame(draw);
  // Heard-time clock: AudioContext position minus output latency, minus the
  // user's manual offset. Everything visual uses this so it matches the
  // audio you actually hear; hit-sound scheduling stays on the raw AC clock.
  const now = songMs() - latencyMs() - OFFSET_MS;
  ctx.clearRect(0, 0, VIEW.W, VIEW.H);
  ctx.fillStyle = "#0c0c11";
  ctx.fillRect(0, 0, VIEW.W, VIEW.pfH);     // playfield region (top)
  drawVisualizer(now);
  drawRuler(now);
  scheduleHits();

  if (!MAP) {
    ctx.fillStyle = "#555"; ctx.font = "16px Segoe UI";
    ctx.fillText("Generate a map to preview it here.", 28, 36);
    return;
  }
  // advance judgement: play-test miss-scan, or replay progressive feed
  if (!audio.paused) {
    if (PLAY_TEST) {
      const w5 = odWindows()[2];
      while (missScan < MAP.objects.length
             && MAP.objects[missScan].t < now - w5) {
        const o = MAP.objects[missScan];
        if (o.kind !== "spinner" && !judged.has(missScan)) {
          judged.add(missScan); registerMiss();
        }
        missScan++;
      }
    } else if (REPLAY && REPLAY.timed) {
      while (REPLAY._ri < REPLAY.timed.length
             && REPLAY.timed[REPLAY._ri].t <= now) {
        registerHit(REPLAY.timed[REPLAY._ri++].err);
      }
    }
  }
  const r = radiusPx(MAP.cs), pre = preempt(MAP.ar);
  let combo = -1;
  let comboNum = 0;
  let hitGlow = 0;
  for (const o of MAP.objects) {
    if (o.nc || combo < 0) { combo++; comboNum = 1; }
    else comboNum++;
    const col = COMBO[combo % COMBO.length];
    const end = o.kind === "slider" ? o.t + o.dur
              : o.kind === "spinner" ? o.end : o.t;
    if (now < o.t - pre || now > end + 220) continue;

    // Per-object opacity: fade in over the approach, fade out after the
    // hit, so overlapping / stacked objects layer readably instead of all
    // painting at full strength.
    const fin = Math.min(1, Math.max(0,
      (now - (o.t - pre)) / (pre * 0.5)));
    const fout = now > end
      ? 1 - Math.min(1, (now - end) / 180) : 1;
    const alpha = Math.max(0.12, Math.min(fin, fout));

    if (o.kind === "spinner") {
      const p = Math.max(0, Math.min(1, (now - o.t)
        / Math.max(1, o.end - o.t)));
      ctx.save();
      ctx.globalAlpha = alpha;
      ctx.translate(X(256), Y(192));
      // outer guide + shrinking progress ring + spinning needle + label
      ctx.strokeStyle = "#555"; ctx.lineWidth = 2;
      ctx.beginPath(); ctx.arc(0, 0, 150, 0, 7); ctx.stroke();
      ctx.strokeStyle = "#5db8ff"; ctx.lineWidth = 6;
      ctx.beginPath(); ctx.arc(0, 0, 150 * (1 - p), 0, 7); ctx.stroke();
      ctx.rotate((now / 90) % (Math.PI * 2));
      ctx.strokeStyle = "#fff"; ctx.lineWidth = 4;
      ctx.beginPath(); ctx.moveTo(0, 0);
      ctx.lineTo(0, -150 * (1 - p)); ctx.stroke();
      ctx.rotate(-((now / 90) % (Math.PI * 2)));
      ctx.fillStyle = "#fff"; ctx.font = "bold 22px Segoe UI";
      ctx.textAlign = "center"; ctx.textBaseline = "middle";
      ctx.fillText("SPIN", 0, 0);
      ctx.restore();
      ctx.textAlign = "left"; ctx.textBaseline = "alphabetic";
      continue;
    }
    ctx.globalAlpha = alpha;
    if (o.kind === "slider") {
      for (const [w, c] of [[r*2, col+"55"], [3, col]]) {
        ctx.strokeStyle = c; ctx.lineWidth = w;
        ctx.lineCap = "round"; ctx.lineJoin = "round";
        ctx.beginPath(); ctx.moveTo(X(o.x), Y(o.y));
        if (o.curve === "L") ctx.lineTo(X(o.ex), Y(o.ey));
        else ctx.quadraticCurveTo(X(o.cx), Y(o.cy), X(o.ex), Y(o.ey));
        ctx.stroke();
      }
      if (now >= o.t && now <= o.t + o.dur) {
        const bp = sliderPoint(o, (now - o.t) / o.dur);
        ctx.fillStyle = "#fff";
        ctx.beginPath(); ctx.arc(X(bp[0]), Y(bp[1]), r*.55, 0, 7);
        ctx.fill();
      }
    }
    ctx.beginPath(); ctx.arc(X(o.x), Y(o.y), r, 0, 7);
    ctx.fillStyle = col + "cc"; ctx.fill();
    ctx.lineWidth = 3; ctx.strokeStyle = "#fff"; ctx.stroke();
    // combo number inside the circle (osu! reading aid)
    ctx.fillStyle = "#fff";
    ctx.font = `bold ${Math.round(r * 1.05)}px Segoe UI`;
    ctx.textAlign = "center"; ctx.textBaseline = "middle";
    ctx.fillText(String(comboNum), X(o.x), Y(o.y) + r * 0.04);
    ctx.textAlign = "left"; ctx.textBaseline = "alphabetic";
    if (now < o.t) {
      const k = 1 + 3 * (o.t - now) / pre;
      ctx.beginPath(); ctx.arc(X(o.x), Y(o.y), r * k, 0, 7);
      ctx.strokeStyle = col; ctx.lineWidth = 2; ctx.stroke();
    }
    ctx.globalAlpha = 1;        // reactions/pings draw at full strength
    // HIT reaction: at the moment the object is hit (its time, in heard
    // time), flash + an expanding ring so you can confirm it lands on the
    // beat both visually and audibly.
    const HB = 150;
    if (now >= o.t && now <= o.t + HB) {
      const p = (now - o.t) / HB;            // 0..1
      if (p < 0.6) hitGlow = Math.max(hitGlow, 1 - p / 0.6);
      ctx.globalAlpha = 1 - p;
      ctx.beginPath();
      ctx.arc(X(o.x), Y(o.y), r * (1 + 1.6 * p), 0, 7);
      ctx.strokeStyle = "#ffffff"; ctx.lineWidth = 3; ctx.stroke();
      if (p < 0.4) {                          // brief bright pop
        ctx.globalAlpha = (0.4 - p) / 0.4;
        ctx.beginPath(); ctx.arc(X(o.x), Y(o.y), r * 0.85, 0, 7);
        ctx.fillStyle = "#ffffff"; ctx.fill();
      }
      ctx.globalAlpha = 1;
    }
    // play-test judgement ping: green = on-beat hit, red = off
    if (o._ping && now - o._ping >= 0 && now - o._ping <= 220) {
      const q = (now - o._ping) / 220;
      ctx.globalAlpha = 1 - q;
      ctx.beginPath();
      ctx.arc(X(o.x), Y(o.y), r * (1 + 2.0 * q), 0, 7);
      ctx.strokeStyle = o._pgood ? "#5dff9b" : "#ff5d5d";
      ctx.lineWidth = 4; ctx.stroke();
      ctx.globalAlpha = 1;
    }
  }
  const [cx, cy] = cursorAt(now);
  // cursor tracer: fading trail of recent positions
  if (!audio.paused) {
    TRAIL.push([cx, cy]);
    if (TRAIL.length > 22) TRAIL.shift();
  }
  if (TRAIL.length > 1) {
    ctx.lineWidth = 3; ctx.lineCap = "round"; ctx.lineJoin = "round";
    for (let i = 1; i < TRAIL.length; i++) {
      ctx.globalAlpha = (i / TRAIL.length) * 0.5;
      ctx.strokeStyle = "#ff5fb4";
      ctx.beginPath();
      ctx.moveTo(X(TRAIL[i - 1][0]), Y(TRAIL[i - 1][1]));
      ctx.lineTo(X(TRAIL[i][0]), Y(TRAIL[i][1]));
      ctx.stroke();
    }
    ctx.globalAlpha = 1;
  }
  if (hitGlow > 0.02) {                       // cursor pops on each hit
    ctx.globalAlpha = hitGlow * 0.8;
    ctx.beginPath();
    ctx.arc(X(cx), Y(cy), 9 + 14 * hitGlow, 0, 7);
    ctx.fillStyle = "#ffd24a"; ctx.fill();
    ctx.globalAlpha = 1;
  }
  ctx.beginPath(); ctx.arc(X(cx), Y(cy), 7 + 4 * hitGlow, 0, 7);
  ctx.fillStyle = "#fff"; ctx.fill();
  ctx.strokeStyle = "#ff5fb4"; ctx.lineWidth = 3; ctx.stroke();

  // real replay cursor (your actual aim) overlaid in cyan
  if (REPLAY && REPLAY.frames && REPLAY.frames.length
      && $("#rCur") && $("#rCur").checked) {
    const F = REPLAY.frames;
    let lo = 0, hi = F.length - 1;
    while (lo < hi) {
      const m = (lo + hi) >> 1;
      if (F[m][0] < now) lo = m + 1; else hi = m;
    }
    const b = F[Math.max(1, lo)], a = F[Math.max(0, lo - 1)];
    const f = b[0] === a[0] ? 0 : (now - a[0]) / (b[0] - a[0]);
    const rx = a[1] + (b[1] - a[1]) * Math.max(0, Math.min(1, f));
    const ry = a[2] + (b[2] - a[2]) * Math.max(0, Math.min(1, f));
    ctx.beginPath(); ctx.arc(X(rx), Y(ry), 8, 0, 7);
    ctx.strokeStyle = "#3ad6c0"; ctx.lineWidth = 3; ctx.stroke();
  }

  // karaoke overlay (approx-timed lyric lines)
  if (MAP.lyrics && MAP.lyrics.length) {
    let cur = -1;
    for (let i = 0; i < MAP.lyrics.length; i++)
      if (MAP.lyrics[i].t <= now) cur = i; else break;
    const fz = Math.max(16, Math.round(VIEW.pfH * 0.05));
    ctx.textAlign = "center";
    if (cur >= 0) {
      ctx.fillStyle = "#ffffff"; ctx.font = `600 ${fz}px Segoe UI`;
      ctx.fillText(MAP.lyrics[cur].text, VIEW.W / 2, fz + 8);
    }
    if (cur + 1 < MAP.lyrics.length) {
      ctx.fillStyle = "rgba(255,255,255,0.32)";
      ctx.font = `${Math.round(fz * 0.72)}px Segoe UI`;
      ctx.fillText(MAP.lyrics[cur + 1].text, VIEW.W / 2, fz * 2 + 14);
    }
    ctx.textAlign = "left";
  }

  const sec = sectionAt(now);
  if (sec) $("#section").textContent =
    `${sec.kind} · 1/${sec.subdiv} · int ${sec.intensity.toFixed(2)}`;
  // live music-reading metrics
  const B = MAP.beat_times || [];
  const win = (MAP.beat_ms || 500) * 8;
  const lbpm = localBpm(now);
  let onsW = 0;
  const O = MAP.onset_times || [];
  for (let j = 0; j < O.length; j++) {
    if (O[j] < now - win) continue;
    if (O[j] > now + win) break;
    onsW++;
  }
  let beatsW = 0;
  for (let j = 0; j < B.length; j++) {
    if (B[j] < now - win) continue;
    if (B[j] > now + win) break;
    beatsW++;
  }
  const dens = beatsW ? (onsW / beatsW).toFixed(1) : "0";
  $("#metrics").textContent =
    `BPM ${lbpm.toFixed(1)} (avg ${(MAP.bpm || 0).toFixed(0)}) · ` +
    `feel ${feelHint(B, O, now, win)} · ${dens} onsets/beat`;
  // ---- live HUD + hit-error (UR) bar ----
  const showJudge = PLAY_TEST || (REPLAY && REPLAY.timed);
  if (showJudge) {
    const [w3, w1, w5] = odWindows();
    const span = Math.max(w5 * 1.25, 60);
    const bw = Math.min(VIEW.W * 0.5, 460);
    const bx = VIEW.W / 2 - bw / 2;
    const by = VIEW.pfH - 26;
    const xe = (e) => bx + bw / 2
      + Math.max(-1, Math.min(1, e / span)) * (bw / 2);
    // judgement zones
    const zone = (w, c) => {
      ctx.fillStyle = c;
      ctx.fillRect(xe(-w), by + 4, xe(w) - xe(-w), 6);
    };
    ctx.globalAlpha = 0.5;
    zone(w5, "#d6477a"); zone(w1, "#ffd24a"); zone(w3, "#5dff9b");
    ctx.globalAlpha = 1;
    ctx.fillStyle = "#fff";
    ctx.fillRect(VIEW.W / 2 - 1, by, 2, 14);     // perfect centre
    const tnow = performance.now();
    for (const h of J.bar) {
      const age = (tnow - h.shown) / 1600;
      if (age >= 1) continue;
      ctx.globalAlpha = 1 - age;
      ctx.fillStyle = "#9adcff";
      ctx.fillRect(xe(h.err) - 1, by, 2, 14);
    }
    ctx.globalAlpha = 1;
    if (J.errs.length) {                          // mean marker
      const mean = J.errs.reduce((s, e) => s + e, 0) / J.errs.length;
      ctx.fillStyle = "#ff5fb4";
      ctx.fillRect(xe(mean) - 1, by - 4, 2, 22);
    }
    // HUD: accuracy (top-right), combo (bottom-left)
    ctx.textBaseline = "alphabetic";
    ctx.fillStyle = "#cfd3e4";
    ctx.font = `bold ${Math.round(VIEW.pfH * 0.055)}px Segoe UI`;
    ctx.textAlign = "right";
    ctx.fillText(acc().toFixed(2) + "%", VIEW.W - 12,
      Math.round(VIEW.pfH * 0.07));
    ctx.textAlign = "left";
    ctx.fillStyle = "#ffd24a";
    ctx.font = `bold ${Math.round(VIEW.pfH * 0.075)}px Segoe UI`;
    ctx.fillText(J.combo + "x", 12, VIEW.pfH - 12);
    ctx.fillStyle = "#9aa0b4";
    ctx.font = `${Math.round(VIEW.pfH * 0.035)}px Segoe UI`;
    ctx.textAlign = "right";
    ctx.fillText(J.score.toLocaleString(), VIEW.W - 12,
      Math.round(VIEW.pfH * 0.115));
    ctx.textAlign = "left";
  }

  // 3-2-1 lead-in overlay before a play-test run
  const cdLeft = countdownUntil - performance.now();
  if (cdLeft > 0) {
    const n = Math.ceil(cdLeft / 1000);
    const label = n <= 0 ? "GO" : String(n);
    const fr = 1 - (cdLeft % 1000) / 1000;        // 0..1 within the second
    ctx.save();
    ctx.fillStyle = "rgba(0,0,0,0.45)";
    ctx.fillRect(0, 0, VIEW.W, VIEW.pfH);
    ctx.globalAlpha = 0.35 + 0.65 * (1 - fr);
    ctx.fillStyle = "#ffd24a";
    ctx.font = `bold ${Math.round(VIEW.pfH * 0.35)}px Segoe UI`;
    ctx.textAlign = "center"; ctx.textBaseline = "middle";
    ctx.fillText(label, VIEW.W / 2, VIEW.pfH / 2);
    ctx.font = `bold ${Math.round(VIEW.pfH * 0.05)}px Segoe UI`;
    ctx.fillStyle = "#fff";
    ctx.fillText("get ready — hit Z / X on the notes",
      VIEW.W / 2, VIEW.pfH * 0.78);
    ctx.restore();
    ctx.textAlign = "left"; ctx.textBaseline = "alphabetic";
  }
  const dur = (audio.duration || MAP.duration_ms / 1000) || 1;
  $("#seek").value = Math.round((audio.currentTime / dur) * 1000) || 0;
  $("#time").textContent = fmt(audio.currentTime) + " / " + fmt(dur);
}
function fmt(s) {
  s = Math.max(0, s | 0);
  return (s / 60 | 0) + ":" + String(s % 60).padStart(2, "0");
}

// ---- controls -----------------------------------------------------------
const KNOBS = [...document.querySelectorAll(".knob")];
function setKnobs(style) {
  for (const el of KNOBS) {
    const k = el.dataset.k;
    if (style[k] === undefined) continue;
    const inp = el.querySelector("input");
    inp.value = style[k];
    el.querySelector("b").textContent = (+style[k]).toFixed(2);
  }
}
KNOBS.forEach((el) => {
  const inp = el.querySelector("input"), out = el.querySelector("b");
  inp.addEventListener("input",
    () => { out.textContent = (+inp.value).toFixed(2); });
});
async function loadProfiles() {
  const list = await (await fetch("/api/profiles")).json();
  const sel = $("#profile"); sel.innerHTML = "";
  for (const p of list) {
    const o = document.createElement("option");
    o.value = p.name;
    o.textContent = p.name + (p.n_maps ? `  (${p.n_maps} maps)` : "");
    sel.appendChild(o);
  }
  if ([...sel.options].some((o) => o.value === "monstrata"))
    sel.value = "monstrata";
  await loadProfileKnobs();
}
async function loadProfileKnobs() {
  const r = await fetch("/api/profile?name=" +
    encodeURIComponent($("#profile").value));
  setKnobs(await r.json());
}
$("#profile").addEventListener("change", loadProfileKnobs);
// Per-section lock rows: a ticked row contributes a verbatim lock value for
// that section kind; unticked rows are omitted (fall back to the global knob).
const SLROWS = [...document.querySelectorAll(".sl-row")];
SLROWS.forEach((row) => {
  const on = row.querySelector(".sl-on");
  const val = row.querySelector(".sl-val");
  const out = row.querySelector(".sl-out");
  const sync = () => {
    val.disabled = !on.checked;
    out.textContent = on.checked ? (+val.value).toFixed(2) : "—";
    row.classList.toggle("on", on.checked);
  };
  on.addEventListener("change", sync);
  val.addEventListener("input", sync);
  sync();
});
function sectionLocks() {
  const m = {};
  for (const row of SLROWS) {
    if (row.querySelector(".sl-on").checked)
      m[row.dataset.kind] = +row.querySelector(".sl-val").value;
  }
  return m;
}
function overrides() {
  const o = {};
  for (const el of KNOBS) o[el.dataset.k] = +el.querySelector("input").value;
  o.rhythm_lock_sections = sectionLocks();
  return o;
}
async function generate() {
  const msg = $("#msg"); msg.className = ""; msg.textContent = "Generating…";
  $("#gen").disabled = true;
  try {
    const r = await fetch("/api/generate", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        audio_path: $("#audioPath").value.trim(),
        profile: $("#profile").value,
        difficulty: $("#difficulty").value,
        overrides: overrides(),
        lyrics: $("#lyrics").value.split(/\r?\n/)
          .map((s) => s.trim()).filter(Boolean),
        lyrics_drive: $("#lyricsDrive").checked,
        tempo: TEMPO_OVERRIDE || undefined,
      }),
    });
    const d = await r.json();
    if (!r.ok) throw new Error(d.error || "generation failed");
    loadMap(d);
    msg.textContent = "Ready. Press Play for the auto replay.";
  } catch (e) {
    msg.className = "err"; msg.textContent = "Error: " + e.message;
  } finally { $("#gen").disabled = false; }
}
$("#gen").addEventListener("click", generate);

// ---- reset: drop the loaded/imported map for a fresh generation ----------
// Keeps the current song (so Generate works on it) but clears the map,
// replay, taps and tempo override - useful after importing an .osz when you
// then want to generate your own map from the same audio.
function resetSession() {
  try { audio.pause(); } catch (e) { /* not started */ }
  killScheduled();
  const keepPath = (MAP && MAP.audio_path) || $("#audioPath").value.trim();
  MAP = null; KF = [];
  hits = []; nextHit = 0; metro = []; nextMetro = 0;
  REPLAY = null;
  taps = []; tapsSorted = []; nextTap = 0; TEMPO_OVERRIDE = null;
  resetPlayStats(); tapInfo();
  // hide imported / replay-only widgets
  $("#impDiffWrap").style.display = "none";
  $("#replayApply").style.display = "none";
  $("#rCurWrap").style.display = "none";
  $("#replayInfo").className = "hint";
  $("#replayInfo").textContent =
    "Load a map, then a replay played on it to see your real hit offset.";
  const dl = $("#dl");
  dl.setAttribute("aria-disabled", "true"); dl.removeAttribute("href");
  $("#stats").textContent = "no map yet";
  if (keepPath) $("#audioPath").value = keepPath;
  const msg = $("#msg"); msg.className = "";
  msg.textContent = keepPath
    ? "Reset. Adjust style / difficulty, then Generate for a fresh map."
    : "Reset. Load audio, then Generate.";
}
$("#reset").addEventListener("click", resetSession);

// ---- song identify (Shazam) --------------------------------------------
$("#idBtn").addEventListener("click", async () => {
  const info = $("#idInfo");
  info.className = "hint"; info.textContent = "Identifying…";
  try {
    const r = await fetch("/api/identify", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ audio_path: $("#audioPath").value.trim() }),
    });
    const d = await r.json();
    if (!d.ok) {
      info.textContent = "Not identified" +
        (d.error ? " (" + d.error + ")" : "") +
        ". You can still paste lyrics manually.";
      return;
    }
    info.className = "hint ok";
    info.textContent = `${d.artist} — ${d.title}` +
      (d.genre ? ` [${d.genre}]` : "") +
      (d.has_lyrics ? "" : " · Shazam had no lyrics for this track");
    if (d.lyrics && d.lyrics.length) $("#lyrics").value = d.lyrics.join("\n");
  } catch (e) {
    info.textContent = "Identify failed: " + e.message;
  }
});

// ---- drag & drop upload -------------------------------------------------
const dropmask = $("#dropmask");
let dragDepth = 0;
function isAudio(f) {
  return f && (/^audio\//.test(f.type) ||
    /\.(mp3|ogg|wav|flac|m4a|aac)$/i.test(f.name));
}
function loadMap(d) {
  MAP = d; KF = buildKeyframes(d.objects); rebuildHits(d.objects);
  rebuildMetro(d.beat_times);
  audio.src = "/audio?path=" + encodeURIComponent(d.audio_path);
  audio.load(); audio.currentTime = 0; nextHit = 0;
  resetPlayStats();
  const dl = $("#dl");
  if (d.osz) {
    dl.href = "/download?path=" + encodeURIComponent(d.osz);
    dl.setAttribute("download", d.osz_name || "map.osz");
    dl.setAttribute("aria-disabled", "false");
  } else {
    dl.setAttribute("aria-disabled", "true");
  }
  // imported-map difficulty chooser
  const dw = $("#impDiffWrap"), ds = $("#impDiff");
  if (d.imported && d.difficulties && d.difficulties.length > 1) {
    dw.style.display = "";
    ds.innerHTML = "";
    for (const df of d.difficulties) {
      const o = document.createElement("option");
      o.value = df.version;
      o.textContent = `${df.version} (${df.objects})`;
      ds.appendChild(o);
    }
    ds.value = d.version || ds.value;
  } else {
    dw.style.display = "none";
  }
  const drops = (d.sections || []).filter((s) => s.kind === "drop").length;
  const ly = (d.lyrics || []).length;
  $("#stats").textContent =
    `${d.objects.length} objects · ${d.bpm} BPM · CS${d.cs} AR${d.ar} ` +
    `· ${(d.sections || []).length} sections (${drops} drops) · ` +
    `${d.style ? d.style.name : ""}` + (ly ? ` · ${ly} lyrics` : "") +
    (d.imported ? "  [IMPORTED MAP]" : "");
}
async function importOsz(file) {
  const msg = $("#msg");
  msg.className = ""; msg.textContent = `Importing ${file.name}…`;
  try {
    const r = await fetch("/api/import?name=" +
      encodeURIComponent(file.name), { method: "POST", body: file });
    const d = await r.json();
    if (!r.ok || d.error) throw new Error(d.error || "import failed");
    loadMap(d);
    msg.textContent = `Imported ${d.objects.length} objects. Press Play ` +
      "to check our BEAT lane vs the real circles.";
  } catch (e) {
    msg.className = "err"; msg.textContent = "Import error: " + e.message;
  }
}
async function uploadFile(file) {
  const msg = $("#msg");
  msg.className = ""; msg.textContent = `Uploading ${file.name}…`;
  try {
    const r = await fetch("/api/upload?name=" +
      encodeURIComponent(file.name), { method: "POST", body: file });
    const d = await r.json();
    if (!r.ok || !d.ok) throw new Error(d.error || "upload failed");
    $("#audioPath").value = d.path;
    $("#idInfo").className = "hint";
    $("#idInfo").textContent = "Not identified yet.";
    msg.textContent = `Loaded ${file.name} ` +
      `(${(d.bytes / 1048576).toFixed(1)} MB). Click Generate.`;
  } catch (e) {
    msg.className = "err"; msg.textContent = "Upload error: " + e.message;
  }
}
window.addEventListener("dragenter", (e) => {
  e.preventDefault(); dragDepth++; dropmask.classList.add("on");
});
window.addEventListener("dragover", (e) => { e.preventDefault(); });
window.addEventListener("dragleave", (e) => {
  e.preventDefault();
  if (--dragDepth <= 0) { dragDepth = 0; dropmask.classList.remove("on"); }
});
window.addEventListener("drop", (e) => {
  e.preventDefault(); dragDepth = 0; dropmask.classList.remove("on");
  const f = e.dataTransfer && e.dataTransfer.files &&
            e.dataTransfer.files[0];
  if (!f) return;
  if (/\.osz$/i.test(f.name)) { importOsz(f); return; }
  if (!isAudio(f)) {
    const m = $("#msg"); m.className = "err";
    m.textContent = "Drop an audio file or a .osz: " + f.name;
    return;
  }
  uploadFile(f);
});

// file-picker buttons (drag-drop is finicky)
$("#impDiff").addEventListener("change", async (e) => {
  if (!MAP || !MAP.osz) return;
  const msg = $("#msg"); msg.className = "";
  msg.textContent = "Loading difficulty…";
  try {
    const r = await fetch("/api/import_diff", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ osz: MAP.osz, version: e.target.value }),
    });
    const d = await r.json();
    if (!r.ok || d.error) throw new Error(d.error || "load failed");
    loadMap(d);
    msg.textContent = `Loaded [${d.version}].`;
  } catch (err) {
    msg.className = "err"; msg.textContent = "Error: " + err.message;
  }
});
// ---- import an .osr replay: compare your real hits to the map ----------
let REPLAY = null;          // {presses, frames, player, meanErr, ...}
function analyzeReplay(d) {
  if (!MAP || !MAP.objects.length) {
    $("#replayInfo").className = "hint err";
    $("#replayInfo").textContent =
      "Load the map first (Open .osz / generate), then the replay.";
    return;
  }
  const objs = MAP.objects;
  let n = 0, sum = 0, sumAbs = 0;
  const timed = [];          // {t: pressMs, err} for progressive HUD/UR
  for (const pt of d.presses) {
    let lo = 0, hi = objs.length - 1;
    while (lo < hi) {
      const m = (lo + hi) >> 1;
      if (objs[m].t < pt) lo = m + 1; else hi = m;
    }
    let bd = Math.abs(objs[lo].t - pt), bi = lo;
    if (lo > 0 && Math.abs(objs[lo - 1].t - pt) < bd) {
      bd = Math.abs(objs[lo - 1].t - pt); bi = lo - 1;
    }
    if (bd <= 200) {
      const e = pt - objs[bi].t;
      sum += e; sumAbs += Math.abs(e); n++;
      timed.push({ t: pt, err: e });
    }
  }
  d.timed = timed.sort((a, b) => a.t - b.t);
  d._ri = 0;
  d.meanErr = n ? sum / n : 0;
  d.absErr = n ? sumAbs / n : 0;
  d.matched = n;
  REPLAY = d;
  $("#rCurWrap").style.display = d.frames && d.frames.length ? "" : "none";
  $("#replayApply").style.display = n ? "" : "none";
  const c = d.counts || {};
  $("#replayInfo").className = "hint ok";
  let autoMsg = "";
  if (n >= 20) {                  // enough data -> auto-fix the sync
    OFFSET_MS = Math.max(-250, Math.min(250, Math.round(-d.meanErr)));
    const sl = $("#audioOffset");
    if (sl) sl.value = OFFSET_MS;
    $("#offVal").textContent = (OFFSET_MS > 0 ? "+" : "") + OFFSET_MS + "ms";
    try { localStorage.setItem("m2o_offset", String(OFFSET_MS)); }
    catch (e) { /* storage off */ }
    resyncHits();
    autoMsg = ` → auto-set & saved offset ${OFFSET_MS}ms`;
  }
  $("#replayInfo").textContent =
    `${d.player || "replay"} · ${n} hits matched · your offset ` +
    `${d.meanErr > 0 ? "+" : ""}${d.meanErr.toFixed(0)}ms ` +
    `(${d.meanErr > 0 ? "late" : "early"}) · σ ${d.absErr.toFixed(0)}ms` +
    ` · ${c["300"] || 0}/${c["100"] || 0}/${c["50"] || 0}×miss ` +
    `${c.miss || 0}` + (d.rate !== 1 ? ` · rate ${d.rate}` : "") + autoMsg;
}
async function importReplay(file) {
  $("#replayInfo").className = "hint";
  $("#replayInfo").textContent = `Reading ${file.name}…`;
  try {
    const r = await fetch("/api/replay?name=" +
      encodeURIComponent(file.name), { method: "POST", body: file });
    const d = await r.json();
    if (!d.ok) throw new Error(d.error || "replay parse failed");
    analyzeReplay(d);
  } catch (e) {
    $("#replayInfo").className = "hint err";
    $("#replayInfo").textContent = "Replay error: " + e.message;
  }
}
$("#openReplay").addEventListener("click", () => $("#osrFile").click());
$("#osrFile").addEventListener("change", (e) => {
  const f = e.target.files && e.target.files[0];
  if (f) importReplay(f);
  e.target.value = "";
});
$("#replayApply").addEventListener("click", () => {
  if (!REPLAY) return;
  OFFSET_MS = Math.round(-REPLAY.meanErr);
  const sl = $("#audioOffset");
  if (sl) { sl.value = Math.max(-250, Math.min(250, OFFSET_MS));
            $("#offVal").textContent =
              (OFFSET_MS > 0 ? "+" : "") + OFFSET_MS + "ms"; }
  try { localStorage.setItem("m2o_offset", String(OFFSET_MS)); }
  catch (e) { /* storage disabled */ }
  resyncHits();
});

$("#openOsz").addEventListener("click", () => $("#oszFile").click());
$("#oszFile").addEventListener("change", (e) => {
  const f = e.target.files && e.target.files[0];
  if (f) importOsz(f);
  e.target.value = "";
});
$("#openAudio").addEventListener("click", () => $("#audFile").click());
$("#audFile").addEventListener("change", (e) => {
  const f = e.target.files && e.target.files[0];
  if (f) uploadFile(f);
  e.target.value = "";
});

// ---- transport ----------------------------------------------------------
let countdownUntil = 0;        // perf.now() ms while a 3-2-1 is showing
$("#play").addEventListener("click", () => {
  if (!audio.src) return;
  initAudio();
  if (AC.state === "suspended") AC.resume();
  if (!audio.paused) { audio.pause(); return; }
  if ($("#playTest").checked && audio.currentTime < 0.05
      && countdownUntil < performance.now()) {
    resetPlayStats();
    countdownUntil = performance.now() + 3000;     // 3-2-1-GO lead-in
    $("#play").textContent = "…";
    setTimeout(() => { countdownUntil = 0; audio.play(); }, 3000);
    return;
  }
  audio.play();
});
audio.addEventListener("play", () => { $("#play").textContent = "❚❚ Pause"; });
// 'playing' fires when output actually starts (after buffering) - the only
// reliable moment to anchor the precise clock.
audio.addEventListener("playing", () => { reanchor(); resyncHits(); });
audio.addEventListener("pause", () => {
  $("#play").textContent = "▶ Play"; killScheduled();
});
audio.addEventListener("ended", () => {
  $("#play").textContent = "▶ Play"; killScheduled();
});
audio.addEventListener("seeked", () => {
  killScheduled(); reanchor(); resyncHits();
  resetPlayStats();            // reset judgement/HUD/UR after a scrub
  TRAIL = [];                  // don't streak the tracer across a jump
});
$("#seek").addEventListener("input", () => {
  const dur = audio.duration || (MAP && MAP.duration_ms / 1000) || 0;
  if (dur) { audio.currentTime = ($("#seek").value / 1000) * dur;
             reanchor(); resyncHits(); }
});

// ---- volume -------------------------------------------------------------
const volMusic = $("#volMusic"), volHits = $("#volHits");
const volMetro = $("#volMetro");
function applyVol() {
  audio.volume = +volMusic.value;
  if (hsGain) hsGain.gain.value = (+volHits.value) * 2;   // 2x scale
  if (metroGain) metroGain.gain.value = +volMetro.value;
}
volMusic.addEventListener("input", applyVol);
volHits.addEventListener("input", applyVol);
volMetro.addEventListener("input", applyVol);
audio.volume = +volMusic.value;
function saveOffset() {
  try { localStorage.setItem("m2o_offset", String(OFFSET_MS)); }
  catch (e) { /* storage disabled */ }
}
$("#audioOffset").addEventListener("input", (e) => {
  OFFSET_MS = +e.target.value;
  $("#offVal").textContent = (OFFSET_MS > 0 ? "+" : "") + OFFSET_MS + "ms";
  saveOffset();
  resyncHits();
});
// ---- tap / record your own tempo ---------------------------------------
let taps = [];                 // heard-time ms, recording order
let tapsSorted = [];
let nextTap = 0;
let TEMPO_OVERRIDE = null;     // {bpm, offset_ms} once "Use my tempo"
function heardNow() { return songMs() - latencyMs() - OFFSET_MS; }
// Hardware-stamped variant: pass `event.timeStamp` to anchor to when the
// key actually arrived from the OS, not when JS got around to dispatching.
function heardAt(perfMs) {
  return perfToSongMs(perfMs) - latencyMs() - OFFSET_MS;
}
function deriveTap() {
  if (tapsSorted.length < 4) return null;
  const iv = [];
  for (let i = 1; i < tapsSorted.length; i++)
    iv.push(tapsSorted[i] - tapsSorted[i - 1]);
  const s = [...iv].sort((a, b) => a - b);
  const med = s[s.length >> 1];
  const good = iv.filter((v) => v > med * 0.5 && v < med * 1.8);
  const g = good.length ? good : iv;
  const per = [...g].sort((a, b) => a - b)[g.length >> 1];
  if (!per || per <= 0) return null;
  let bpm = 60000 / per;
  while (bpm > 230) bpm /= 2;
  while (bpm < 65) bpm *= 2;
  return { bpm: Math.round(bpm * 100) / 100, offset: tapsSorted[0] };
}
function tapInfo() {
  const d = deriveTap();
  $("#tapInfo").textContent = d
    ? `${taps.length} taps · BPM ${d.bpm} · off ${Math.round(d.offset)}ms`
    : (taps.length ? `${taps.length} taps (need 4+)` : "no taps");
}
function recordTap(perfMs) {
  if (!MAP || !AC || audio.paused) return;
  const t = heardAt(perfMs);
  if (t < 0) return;
  taps.push(t);
  tapsSorted = [...taps].sort((a, b) => a - b);
  tapInfo();
  const b = $("#tap"); b.classList.add("armed");
  setTimeout(() => b.classList.remove("armed"), 90);
}
$("#tap").addEventListener("click", (e) => recordTap(e.timeStamp));
window.addEventListener("keydown", (e) => {
  const tag = (e.target.tagName || "").toLowerCase();
  if (tag === "input" || tag === "textarea" || tag === "select") return;
  if (e.repeat) return;
  const k = (e.key || "").toLowerCase();
  // event.timeStamp is a DOMHighResTimeStamp (same epoch as performance.now)
  // stamped by the browser when the OS delivered the key - far closer to the
  // real press than the time this handler ends up running.
  const ts = e.timeStamp;
  if (PLAY_TEST && (k === "z" || k === "x")) {
    e.preventDefault(); playHit(ts); return;
  }
  // z/x are the default osu! keys; T also works. (Tap mode.)
  if (k === "t" || k === "z" || k === "x") {
    e.preventDefault(); recordTap(ts);
  }
});

// ---- play-along / judgement: UR error bar + live HUD -------------------
let PLAY_TEST = false;
let judged = new Set();
let missScan = 0;             // play-test object pointer for miss detection
// J = live judgement state (drives HUD + the hit-error bar).
const J = { n3: 0, n1: 0, n5: 0, nm: 0, combo: 0, maxc: 0, score: 0,
            bar: [], errs: [] };   // bar:{shown,err}  errs: signed history
function odWindows() {
  const od = (MAP && MAP.od != null) ? MAP.od : 7;   // ms (osu! formula)
  return [80 - 6 * od, 140 - 8 * od, 200 - 10 * od];
}
function resetPlayStats() {
  judged = new Set(); missScan = 0;
  J.n3 = J.n1 = J.n5 = J.nm = J.combo = J.maxc = J.score = 0;
  J.bar = []; J.errs = [];
  if (REPLAY) REPLAY._ri = 0;
  updatePlayInfo();
}
function acc() {
  const t = J.n3 + J.n1 + J.n5 + J.nm;
  return t ? (300 * J.n3 + 100 * J.n1 + 50 * J.n5) / (300 * t) * 100 : 100;
}
function registerHit(err) {
  const [w3, w1, w5] = odWindows();
  const a = Math.abs(err);
  J.bar.push({ shown: performance.now(), err });
  J.errs.push(err);
  if (a > w5) { J.nm++; J.combo = 0; updatePlayInfo(); return; }
  let g;
  if (a <= w3) { J.n3++; g = 300; }
  else if (a <= w1) { J.n1++; g = 100; }
  else { J.n5++; g = 50; }
  J.combo++; J.maxc = Math.max(J.maxc, J.combo);
  J.score += Math.round(g * (1 + J.combo * 0.02));
  updatePlayInfo();
}
function registerMiss() { J.nm++; J.combo = 0; updatePlayInfo(); }
function updatePlayInfo() {
  const el = $("#playInfo");
  if (!el) return;
  const tot = J.n3 + J.n1 + J.n5 + J.nm;
  if (!tot) {
    el.textContent = PLAY_TEST ? "press z/x on the notes (3-2-1 on Play)"
      : (REPLAY ? "press Play to watch the replay judged" : "idle");
    return;
  }
  const mean = J.errs.length
    ? J.errs.reduce((s, e) => s + e, 0) / J.errs.length : 0;
  const sd = J.errs.length > 1
    ? Math.sqrt(J.errs.reduce((s, e) => s + (e - mean) ** 2, 0)
        / J.errs.length) : 0;
  el.textContent =
    `${J.n3}/${J.n1}/${J.n5}/✗${J.nm} · ${acc().toFixed(2)}% · ` +
    `x${J.maxc} · mean ${mean > 0 ? "+" : ""}${mean.toFixed(0)}ms ` +
    `· UR ${(sd * 10).toFixed(0)}`;
}
function nearestObj(t) {
  const objs = MAP.objects;
  let lo = 0, hi = objs.length - 1;
  while (lo < hi) {
    const m = (lo + hi) >> 1;
    if (objs[m].t < t) lo = m + 1; else hi = m;
  }
  let best = lo, bd = Math.abs(objs[lo].t - t);
  if (lo > 0 && Math.abs(objs[lo - 1].t - t) < bd) {
    best = lo - 1; bd = Math.abs(objs[lo - 1].t - t);
  }
  return [best, bd];
}
function playHit(perfMs) {
  if (!MAP || !AC || audio.paused || !MAP.objects.length) return;
  const t = heardAt(perfMs);
  const [best, bd] = nearestObj(t);
  if (bd <= 200 && !judged.has(best)) {       // a real hit
    judged.add(best);
    const err = t - MAP.objects[best].t;      // + late, - early
    MAP.objects[best]._ping = t;
    MAP.objects[best]._pgood = Math.abs(err) <= odWindows()[1];
    registerHit(err);
  }
}
$("#tapClear").addEventListener("click", () => {
  taps = []; tapsSorted = []; nextTap = 0; TEMPO_OVERRIDE = null; tapInfo();
});
$("#tapPlay").addEventListener("change", resyncHits);
$("#tapUse").addEventListener("click", () => {
  const d = deriveTap();
  if (!d) { $("#tapInfo").textContent = "tap at least 4 beats first"; return; }
  TEMPO_OVERRIDE = { bpm: d.bpm, offset_ms: d.offset };
  generate();
});

$("#metroDiv").addEventListener("change", () => {
  if (MAP) rebuildMetro(MAP.beat_times);
  resyncHits();
});
$("#playTest").addEventListener("change", (e) => {
  PLAY_TEST = e.target.checked; resetPlayStats();
});
$("#tripOn").addEventListener("change",
  (e) => { TRIPLET = e.target.checked; });
$("#resSel").addEventListener("change", (e) => {
  const v = e.target.value;
  DESIRED_RES = v ? v.split("x").map(Number) : null;
  fit();
});
$("#visMode").addEventListener("change",
  (e) => { VIS_MODE = e.target.value; });

// restore a previously-calibrated offset so you only dial it once
try {
  const sv = localStorage.getItem("m2o_offset");
  if (sv !== null && !Number.isNaN(+sv)) {
    OFFSET_MS = Math.max(-250, Math.min(250, +sv));
    const sl = $("#audioOffset");
    if (sl) sl.value = OFFSET_MS;
    $("#offVal").textContent =
      (OFFSET_MS > 0 ? "+" : "") + OFFSET_MS + "ms";
  }
} catch (e) { /* storage disabled */ }

$("#dl").setAttribute("aria-disabled", "true");
fit(); loadProfiles(); draw();
