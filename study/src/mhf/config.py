"""Configuration loading and source-availability gating.

The whole point of this module: the pipeline must degrade gracefully as data
arrives. A source that is absent is skipped everywhere -- features, ablation
arms, fusion branches, targets -- rather than silently imputed.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]


class ConfigError(RuntimeError):
    pass


@dataclass
class Config:
    raw: dict[str, Any]
    root: Path = REPO_ROOT

    # ---------------------------------------------------------------- access
    def __getitem__(self, key: str) -> Any:
        return self.raw[key]

    def get(self, dotted: str, default: Any = None) -> Any:
        node: Any = self.raw
        for part in dotted.split("."):
            if not isinstance(node, dict) or part not in node:
                return default
            node = node[part]
        return node

    def path(self, key: str) -> Path:
        return self.root / self.raw["paths"][key]

    def resolve(self, relative: str) -> Path:
        return self.root / relative

    # ------------------------------------------------------------ availability
    def has(self, source: str) -> bool:
        """True only if the source is declared present AND its file exists."""
        node = self.raw["sources"].get(source)
        if node is None or not node.get("present", False):
            return False
        return self.resolve(node["path"]).exists()

    def available_sources(self) -> list[str]:
        return [s for s in self.raw["sources"] if self.has(s)]

    def missing_sources(self) -> list[str]:
        return [s for s in self.raw["sources"] if not self.has(s)]

    # ---------------------------------------------------------------- targets
    def enabled_targets(self) -> list[str]:
        """Targets whose *inputs* actually exist. Never trust the yaml alone."""
        out: list[str] = []
        t = self.raw["targets"]
        if t["A_new_water"]["enabled"] and self.has("sentinel1"):
            out.append("A_new_water")
        if (
            t["B_suitable_habitat"]["enabled"]
            and self.has("sentinel1")
            and self.has("sentinel2")
        ):
            out.append("B_suitable_habitat")
        if t["C_anopheles"]["enabled"] and self.has("entomology"):
            out.append("C_anopheles")
        return out

    def blocked_targets(self) -> dict[str, str]:
        """Target -> why it cannot be built. Printed in every report."""
        blocked: dict[str, str] = {}
        if not self.has("sentinel1"):
            blocked["A_new_water"] = "sentinel1 export absent"
            blocked["B_suitable_habitat"] = "sentinel1 export absent"
        elif not self.has("sentinel2"):
            blocked["B_suitable_habitat"] = "sentinel2 export absent"
        if not self.has("entomology"):
            blocked["C_anopheles"] = (
                "no larval survey records; model cannot claim confirmed "
                "Anopheles breeding, only POTENTIAL suitable habitat"
            )
        return blocked

    @property
    def operational_forecast_possible(self) -> bool:
        """Without NWP input the system is a hindcast. Full stop."""
        return self.has("nwp_forecast")

    @property
    def mode(self) -> str:
        return "operational_forecast" if self.operational_forecast_possible else "hindcast"

    # ------------------------------------------------------------- provenance
    def provenance(self) -> dict[str, Any]:
        return {
            "project": self.raw["project"]["name"],
            "study_area": self.raw["project"]["study_area"],
            "mode": self.mode,
            "available_sources": self.available_sources(),
            "missing_sources": self.missing_sources(),
            "enabled_targets": self.enabled_targets(),
            "blocked_targets": self.blocked_targets(),
            "seed": self.raw["project"]["seed"],
        }

    def write_provenance(self, out: Path | None = None) -> Path:
        out = out or (self.path("reports") / "provenance.json")
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(self.provenance(), indent=2), encoding="utf-8")
        return out


def load_config(path: str | Path = "configs/default.yaml") -> Config:
    p = Path(path)
    if not p.is_absolute():
        p = REPO_ROOT / p
    if not p.exists():
        raise ConfigError(f"config not found: {p}")
    with p.open(encoding="utf-8") as fh:
        raw = yaml.safe_load(fh)
    for key in ("project", "paths", "sources", "targets", "features",
                "validation", "models", "evaluation"):
        if key not in raw:
            raise ConfigError(f"config missing required section: {key}")
    return Config(raw=raw)
