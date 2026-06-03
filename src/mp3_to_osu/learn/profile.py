"""Aggregate per-map features into a per-mapper StyleProfile.

A StyleProfile is the artifact the generator will later sample from: pooled
distributions plus mean/spread of the scalar style knobs.
"""

from __future__ import annotations

import json
import statistics
from dataclasses import asdict, dataclass, field

from .features import RHYTHM_LABELS, MapFeatures


def _mean(v: list[float]) -> float:
    return round(statistics.fmean(v), 4) if v else 0.0


def _std(v: list[float]) -> float:
    return round(statistics.pstdev(v), 4) if len(v) > 1 else 0.0


def _pool(dicts: list[dict[str, float]], keys=None) -> dict[str, float]:
    """Average a list of normalised distributions, then renormalise."""
    acc: dict[str, float] = {}
    for d in dicts:
        for k, v in d.items():
            acc[k] = acc.get(k, 0.0) + v
    tot = sum(acc.values()) or 1.0
    items = ((k, round(acc[k] / tot, 4)) for k in (keys or acc))
    return {k: v for k, v in items if k in acc}


@dataclass
class StyleProfile:
    mapper: str
    n_maps: int
    n_objects_total: int
    bpm_mean: float
    bpm_std: float
    cs_mean: float
    ar_mean: float
    od_mean: float
    circle_ratio: float
    slider_ratio: float
    spinner_ratio: float
    rhythm_hist: dict[str, float]
    type_transitions: dict[str, float]
    spacing_slope: float          # normalised distance per beat of time gap
    spacing_intercept: float
    spacing_slope_std: float
    jump_factor: float
    mean_turn_deg: float
    flow_ratio: float
    objects_per_combo: float
    beats_per_combo: float
    slider_dur_beats_med: float
    curve_freq: dict[str, float]
    stream_rate: float
    mean_stream_len: float
    sample_versions: list[str] = field(default_factory=list)

    def to_json(self, path: str) -> None:
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(asdict(self), fh, indent=2)

    @staticmethod
    def from_json(path: str) -> "StyleProfile":
        with open(path, "r", encoding="utf-8") as fh:
            return StyleProfile(**json.load(fh))

    def describe(self) -> str:
        top_rh = sorted(self.rhythm_hist.items(), key=lambda kv: -kv[1])[:4]
        top_tr = sorted(self.type_transitions.items(),
                        key=lambda kv: -kv[1])[:5]
        L = [
            f"Style profile: {self.mapper}",
            f"  maps={self.n_maps}  objects={self.n_objects_total}  "
            f"BPM={self.bpm_mean:.0f}+/-{self.bpm_std:.0f}",
            f"  CS~{self.cs_mean:.1f} AR~{self.ar_mean:.1f} "
            f"OD~{self.od_mean:.1f}",
            f"  mix: circle {self.circle_ratio:.0%} / "
            f"slider {self.slider_ratio:.0%} / "
            f"spinner {self.spinner_ratio:.1%}",
            "  rhythm: " + ", ".join(f"{k} {v:.0%}" for k, v in top_rh),
            "  type flow: " + ", ".join(f"{k} {v:.0%}" for k, v in top_tr),
            f"  spacing: dist/r ~= {self.spacing_slope:.2f}*beats + "
            f"{self.spacing_intercept:.2f}  (jump~{self.jump_factor:.2f})",
            f"  flow: mean turn {self.mean_turn_deg:.0f}deg, "
            f"soft {self.flow_ratio:.0%}",
            f"  combo: {self.objects_per_combo:.1f} objs / "
            f"{self.beats_per_combo:.1f} beats",
            f"  sliders: median {self.slider_dur_beats_med:.2f} beats, "
            f"curves {self.curve_freq}",
            f"  streams: {self.stream_rate:.0%} of objects, "
            f"mean run {self.mean_stream_len:.1f}",
        ]
        return "\n".join(L)


def build_profile(mapper: str, feats: list[MapFeatures]) -> StyleProfile:
    if not feats:
        raise ValueError(f"no feature rows for {mapper!r}")
    sl = [f.spacing_slope for f in feats]
    return StyleProfile(
        mapper=mapper,
        n_maps=len(feats),
        n_objects_total=sum(f.n_objects for f in feats),
        bpm_mean=_mean([f.bpm for f in feats]),
        bpm_std=_std([f.bpm for f in feats]),
        cs_mean=_mean([f.cs for f in feats]),
        ar_mean=_mean([f.ar for f in feats]),
        od_mean=_mean([f.od for f in feats]),
        circle_ratio=_mean([f.circle_ratio for f in feats]),
        slider_ratio=_mean([f.slider_ratio for f in feats]),
        spinner_ratio=_mean([f.spinner_ratio for f in feats]),
        rhythm_hist=_pool([f.rhythm_hist for f in feats], RHYTHM_LABELS),
        type_transitions=_pool([f.type_transitions for f in feats]),
        spacing_slope=_mean(sl),
        spacing_intercept=_mean([f.spacing_intercept for f in feats]),
        spacing_slope_std=_std(sl),
        jump_factor=_mean([f.jump_factor for f in feats]),
        mean_turn_deg=_mean([f.mean_turn_deg for f in feats]),
        flow_ratio=_mean([f.flow_ratio for f in feats]),
        objects_per_combo=_mean([f.objects_per_combo for f in feats]),
        beats_per_combo=_mean([f.beats_per_combo for f in feats]),
        slider_dur_beats_med=_mean([f.slider_dur_beats_med for f in feats]),
        curve_freq=_pool([f.curve_freq for f in feats], ["L", "P", "B"]),
        stream_rate=_mean([f.stream_rate for f in feats]),
        mean_stream_len=_mean([f.mean_stream_len for f in feats]),
        sample_versions=[f"{f.version} ({f.bpm:.0f}bpm)"
                         for f in feats[:8]],
    )
