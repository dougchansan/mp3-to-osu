"""StyleParams: turn a learned StyleProfile (+ UI/CLI overrides) into the
concrete knobs the generator consumes.

Kept deliberately small and explicit so the browser tool can expose each field
as a slider and round-trip it back through `from_dict`.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field


def _clamp(v: float, lo: float, hi: float) -> float:
    return lo if v < lo else hi if v > hi else v


@dataclass
class StyleParams:
    name: str = "default"
    # Difficulty
    cs: float = 4.0
    ar: float = 9.0
    od: float = 7.0
    hp: float = 5.0
    # Composition
    slider_ratio: float = 0.5          # target fraction of objects = sliders
    slider_dur_beats: float = 0.75     # default slider hold (beats)
    objects_per_combo: float = 5.0     # new-combo cadence
    stream_rate: float = 0.12
    rhythm_lock: float = 0.0           # 0 = loose onsets .. 1 = strict on-pulse
    # Optional per-section-kind overrides of rhythm_lock (e.g. {"verse": 0.7}).
    # A kind listed here uses its value verbatim; unlisted kinds fall back to
    # the global rhythm_lock with automatic per-section weighting.
    rhythm_lock_sections: dict = field(default_factory=dict)
    # Flow / spacing (distance is in circle-radius units, like the analyzer)
    spacing_slope: float = 1.4         # radius-multiples per beat of gap
    spacing_intercept: float = 1.2
    jump_factor: float = 2.0
    mean_turn_deg: float = 95.0
    flow_ratio: float = 0.45           # P(soft <90deg turn)
    curve_weights: dict = field(
        default_factory=lambda: {"L": 0.4, "P": 0.4, "B": 0.2})

    # ---- construction -----------------------------------------------------

    @staticmethod
    def from_profile_json(path: str,
                          overrides: dict | None = None) -> "StyleParams":
        with open(path, "r", encoding="utf-8") as fh:
            p = json.load(fh)
        sp = StyleParams(
            name=p.get("mapper", "profile"),
            cs=round(p.get("cs_mean", 4.0), 1),
            ar=round(p.get("ar_mean", 9.0), 1),
            od=round(p.get("od_mean", 7.0), 1),
            hp=5.0,
            slider_ratio=p.get("slider_ratio", 0.5),
            slider_dur_beats=max(0.5, p.get("slider_dur_beats_med", 0.75)),
            objects_per_combo=max(2.0, p.get("objects_per_combo", 5.0)),
            stream_rate=p.get("stream_rate", 0.12),
            spacing_slope=p.get("spacing_slope", 1.4),
            spacing_intercept=p.get("spacing_intercept", 1.2),
            jump_factor=max(0.5, p.get("jump_factor", 2.0)),
            mean_turn_deg=p.get("mean_turn_deg", 95.0),
            flow_ratio=p.get("flow_ratio", 0.45),
            curve_weights=p.get("curve_freq",
                                {"L": 0.4, "P": 0.4, "B": 0.2}),
        )
        return sp.with_overrides(overrides or {})

    def with_overrides(self, ov: dict) -> "StyleParams":
        for k, v in ov.items():
            if v is None or not hasattr(self, k):
                continue
            try:
                setattr(self, k, type(getattr(self, k))(v)
                        if not isinstance(getattr(self, k), dict) else v)
            except (TypeError, ValueError):
                pass
        return self.sanitised()

    def sanitised(self) -> "StyleParams":
        self.cs = _clamp(self.cs, 0.0, 10.0)
        self.ar = _clamp(self.ar, 0.0, 10.0)
        self.od = _clamp(self.od, 0.0, 10.0)
        self.hp = _clamp(self.hp, 0.0, 10.0)
        self.slider_ratio = _clamp(self.slider_ratio, 0.0, 0.85)
        self.slider_dur_beats = _clamp(self.slider_dur_beats, 0.25, 4.0)
        self.objects_per_combo = _clamp(self.objects_per_combo, 2.0, 16.0)
        self.stream_rate = _clamp(self.stream_rate, 0.0, 0.9)
        self.rhythm_lock = _clamp(self.rhythm_lock, 0.0, 1.0)
        _kinds = {"intro", "build", "verse", "chorus", "drop", "break",
                  "outro"}
        clean: dict = {}
        for k, v in (self.rhythm_lock_sections or {}).items():
            if k in _kinds:
                try:
                    clean[k] = _clamp(float(v), 0.0, 1.0)
                except (TypeError, ValueError):
                    pass
        self.rhythm_lock_sections = clean
        self.spacing_slope = _clamp(self.spacing_slope, 0.1, 6.0)
        self.spacing_intercept = _clamp(self.spacing_intercept, 0.0, 6.0)
        self.jump_factor = _clamp(self.jump_factor, 0.3, 8.0)
        self.mean_turn_deg = _clamp(self.mean_turn_deg, 10.0, 170.0)
        self.flow_ratio = _clamp(self.flow_ratio, 0.0, 1.0)
        tot = sum(max(0.0, v) for v in self.curve_weights.values()) or 1.0
        self.curve_weights = {k: max(0.0, v) / tot
                              for k, v in self.curve_weights.items()}
        return self

    def to_dict(self) -> dict:
        return asdict(self)

    @staticmethod
    def from_dict(d: dict) -> "StyleParams":
        base = StyleParams()
        return base.with_overrides(d)
