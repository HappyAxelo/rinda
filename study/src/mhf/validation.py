"""Splitters. No random split is ever used for a reported result.

V1 exists only to quantify how optimistic a conventional protocol would have
been; the difference V1 - V4 is itself a reportable finding.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterator

import numpy as np
import pandas as pd

from .config import Config


@dataclass
class Split:
    name: str
    scheme: str
    train_idx: np.ndarray
    test_idx: np.ndarray
    note: str = ""

    def sizes(self) -> tuple[int, int]:
        return len(self.train_idx), len(self.test_idx)


# ------------------------------------------------------------------- schemes
def v1_random(df: pd.DataFrame, cfg: Config, n_splits: int = 5) -> Iterator[Split]:
    """Reference only. Groups are NOT respected -- that is the point."""
    rng = np.random.default_rng(cfg["project"]["seed"])
    idx = np.arange(len(df))
    folds = np.array_split(rng.permutation(idx), n_splits)
    for i, te in enumerate(folds):
        tr = np.setdiff1d(idx, te)
        yield Split(f"V1_fold{i}", "V1_random", tr, te,
                    "OPTIMISTIC REFERENCE ONLY -- ignores space and time")


def v2_spatial_block(df: pd.DataFrame, cfg: Config) -> Iterator[Split]:
    """Checkerboard blocks, so neighbouring pixels cannot straddle the split."""
    n_folds = cfg.get("validation.spatial.n_folds", 5)
    blocks = _block_ids(df, cfg)
    uniq = np.array(sorted(pd.unique(blocks)))
    rng = np.random.default_rng(cfg["project"]["seed"])
    assign = {b: i % n_folds for i, b in enumerate(rng.permutation(uniq))}
    fold_of = blocks.map(assign).to_numpy()
    for f in range(n_folds):
        te = np.where(fold_of == f)[0]
        tr = np.where(fold_of != f)[0]
        if len(te) and len(tr):
            yield Split(f"V2_fold{f}", "V2_spatial_block", tr, te,
                        "checkerboard spatial blocking")


def v3_leave_block_out(df: pd.DataFrame, cfg: Config) -> Iterator[Split]:
    """Leave one contiguous block out. Tests within-area spatial transfer.

    With a single agro-ecological zone this is NOT cross-zone transfer, and the
    note is carried into every report so the distinction cannot be lost.

    Capped at `validation.spatial.max_folds`: with many small blocks the
    estimate stops improving long before every block has been held out, and the
    cost is linear in the number of folds.
    """
    blocks = _block_ids(df, cfg)
    uniq = [b for b in sorted(pd.unique(blocks))
            if (blocks == b).sum() >= 20 and (blocks != b).sum() >= 50]
    cap = cfg.get("validation.spatial.max_folds", 10)
    if len(uniq) > cap:
        # keep the largest blocks: they give the most informative held-out sets
        sizes = {b: int((blocks == b).sum()) for b in uniq}
        uniq = sorted(sizes, key=sizes.get, reverse=True)[:cap]
    for b in uniq:
        te = np.where(blocks == b)[0]
        tr = np.where(blocks != b)[0]
        yield Split(f"V3_block_{b}", "V3_leave_block_out", tr, te,
                    "within-area spatial transfer; NOT cross-zone transfer")


def v4_forward_chain(df: pd.DataFrame, cfg: Config) -> Iterator[Split]:
    """Rolling origin with a gap >= horizon so labels cannot bleed backwards."""
    fc = cfg["validation"]["forward_chain"]
    n_splits = fc["n_splits"]
    gap = pd.Timedelta(days=fc["gap_days"])
    min_train = pd.Timedelta(days=fc["min_train_days"])

    dates = np.sort(df["date"].unique())
    t0, t1 = pd.Timestamp(dates[0]), pd.Timestamp(dates[-1])
    if t1 - t0 <= min_train:
        return
    starts = pd.date_range(t0 + min_train, t1, periods=n_splits + 1)[:-1]
    for i, cut in enumerate(starts):
        # The test window runs from the end of the gap to the next origin; the
        # final origin tests against everything remaining. Written explicitly
        # because `A & B if c else True` binds the conditional around the whole
        # mask, which silently reduced the last fold to a single row.
        hi = starts[i + 1] if i + 1 < len(starts) else t1
        tr = np.where(df["date"] <= cut)[0]
        te = np.where((df["date"] > cut + gap) & (df["date"] <= hi))[0]
        if len(te) and len(tr):
            yield Split(f"V4_origin{i}", "V4_forward_chain", tr, te,
                        f"train<= {cut.date()}, {fc['gap_days']}d gap, then test")


def temporal_holdout(df: pd.DataFrame, cfg: Config) -> dict[str, np.ndarray]:
    """The single untouched final split used for the headline number."""
    t = cfg["validation"]["temporal"]
    tr_end = pd.Timestamp(t["train_end"])
    va_end = pd.Timestamp(t["val_end"])
    te_start = pd.Timestamp(t["test_start"])
    return {
        "train": np.where(df["date"] <= tr_end)[0],
        "val": np.where((df["date"] > tr_end) & (df["date"] <= va_end))[0],
        "test": np.where(df["date"] >= te_start)[0],
    }


def leave_one_station_out(df: pd.DataFrame) -> Iterator[Split]:
    """Only meaningful once more than one station exists."""
    if "station" not in df.columns or df["station"].nunique() < 2:
        return
    for st in sorted(df["station"].dropna().unique()):
        te = np.where(df["station"] == st)[0]
        tr = np.where(df["station"] != st)[0]
        if len(te) and len(tr):
            yield Split(f"LOSO_{st}", "leave_one_station_out", tr, te,
                        "cross-station generalisation")


# --------------------------------------------------------------------- utils
def _block_ids(df: pd.DataFrame, cfg: Config) -> pd.Series:
    if "block_id" in df.columns and df["block_id"].notna().all():
        return df["block_id"]
    size_m = cfg.get("validation.spatial.block_size_m", 500)
    deg = size_m / 111_320.0
    if not {"latitude", "longitude"}.issubset(df.columns):
        # fall back to site identity so groups are at least respected
        return df["site_id"].astype(str)
    bx = np.floor(df["longitude"] / deg).astype(int)
    by = np.floor(df["latitude"] / deg).astype(int)
    return (bx.astype(str) + "_" + by.astype(str))


def get_splitter(scheme: str):
    return {"V1_random": v1_random, "V2_spatial_block": v2_spatial_block,
            "V3_leave_block_out": v3_leave_block_out,
            "V4_forward_chain": v4_forward_chain}[scheme]


def assert_no_group_overlap(df: pd.DataFrame, sp: Split,
                            group_col: str = "site_id") -> None:
    a = set(df.iloc[sp.train_idx][group_col])
    b = set(df.iloc[sp.test_idx][group_col])
    if sp.scheme in ("V2_spatial_block", "V3_leave_block_out") and (a & b):
        raise AssertionError(
            f"{sp.name}: {len(a & b)} groups appear in both train and test")


def assert_temporal_order(df: pd.DataFrame, sp: Split, horizon: int) -> None:
    if sp.scheme != "V4_forward_chain":
        return
    tr_max = df.iloc[sp.train_idx]["date"].max()
    te_min = df.iloc[sp.test_idx]["date"].min()
    if (te_min - tr_max).days <= horizon:
        raise AssertionError(
            f"{sp.name}: gap {(te_min - tr_max).days}d <= horizon {horizon}d; "
            "training labels can bleed into the test window")
