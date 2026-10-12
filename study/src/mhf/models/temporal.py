"""Sequence and multi-modal fusion models (PyTorch, optional dependency).

Contains the architecture that is the candidate methodological contribution:

  MultiModalTemporalFusion
    - one encoder per modality branch (SAR, optical, meteo/IoT, terrain)
    - temporal encoder per time-varying branch (GRU)
    - cross-branch attention that learns how much to trust each modality
    - TWO prediction heads (7-day, 14-day) trained jointly (multi-task)

The power gate is enforced by `deep_learning_is_supported`. If the dataset
cannot support these models, the trainer reports NOT_EVALUABLE rather than an
underpowered number dressed up as a negative result about deep learning.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

try:
    import torch
    import torch.nn as nn
    HAS_TORCH = True
except Exception:                                    # pragma: no cover
    HAS_TORCH = False
    nn = object  # type: ignore


def deep_learning_is_supported(n_positive_sequences: int,
                              n_independent_events: int,
                              cfg) -> tuple[bool, str]:
    gate = cfg.get("models.power_gate", {})
    need_pos = gate.get("min_positive_sequences", 500)
    need_ev = gate.get("min_independent_events", 100)
    if not HAS_TORCH:
        return False, "PyTorch not installed"
    if n_positive_sequences < need_pos:
        return False, (f"only {n_positive_sequences} positive sequences "
                       f"(need >= {need_pos}); reporting NOT_EVALUABLE")
    if n_independent_events < need_ev:
        return False, (f"only {n_independent_events} independent events "
                       f"(need >= {need_ev}); sequences are autocorrelated so "
                       "the effective sample size is far below the row count")
    return True, "power gate satisfied"


if HAS_TORCH:

    class FocalLoss(nn.Module):
        """For heavy imbalance. Preferred over SMOTE, which interpolates
        across time and leaks."""

        def __init__(self, gamma: float = 2.0, pos_weight: float | None = None):
            super().__init__()
            self.gamma = gamma
            self.pos_weight = pos_weight

        def forward(self, logits, target):
            p = torch.sigmoid(logits)
            ce = nn.functional.binary_cross_entropy_with_logits(
                logits, target, reduction="none",
                pos_weight=(torch.tensor(self.pos_weight, device=logits.device)
                            if self.pos_weight else None))
            pt = torch.where(target > 0.5, p, 1 - p)
            return (((1 - pt) ** self.gamma) * ce).mean()

    class SequenceEncoder(nn.Module):
        def __init__(self, n_feat: int, hidden: int = 64, kind: str = "gru",
                     dropout: float = 0.2):
            super().__init__()
            self.kind = kind
            if kind == "lstm":
                self.rnn = nn.LSTM(n_feat, hidden, batch_first=True,
                                   num_layers=1, dropout=0.0)
            elif kind == "gru":
                self.rnn = nn.GRU(n_feat, hidden, batch_first=True,
                                  num_layers=1, dropout=0.0)
            elif kind == "tcn":
                self.rnn = None
                self.conv = nn.Sequential(
                    nn.Conv1d(n_feat, hidden, 3, padding=2, dilation=2),
                    nn.ReLU(), nn.Dropout(dropout),
                    nn.Conv1d(hidden, hidden, 3, padding=4, dilation=4),
                    nn.ReLU(), nn.AdaptiveAvgPool1d(1))
            else:
                raise ValueError(kind)
            self.drop = nn.Dropout(dropout)
            self.out_dim = hidden

        def forward(self, x):                       # x: (B, T, F)
            if self.kind == "tcn":
                h = self.conv(x.transpose(1, 2)).squeeze(-1)
            else:
                out, _ = self.rnn(x)
                h = out[:, -1, :]
            return self.drop(h)

    class StaticEncoder(nn.Module):
        def __init__(self, n_feat: int, hidden: int = 32, dropout: float = 0.2):
            super().__init__()
            self.net = nn.Sequential(nn.Linear(n_feat, hidden), nn.ReLU(),
                                     nn.Dropout(dropout))
            self.out_dim = hidden

        def forward(self, x):
            return self.net(x)

    class BranchAttention(nn.Module):
        """Learns a scalar weight per modality per sample. Interpretable:
        the weights say how much the model trusts each data source, and can be
        reported alongside SHAP as modality-level importance."""

        def __init__(self, dims: list[int], proj: int = 64):
            super().__init__()
            self.proj = nn.ModuleList([nn.Linear(d, proj) for d in dims])
            self.score = nn.Linear(proj, 1)
            self.out_dim = proj

        def forward(self, hs: list["torch.Tensor"]):
            z = torch.stack([p(h) for p, h in zip(self.proj, hs)], dim=1)
            w = torch.softmax(self.score(torch.tanh(z)), dim=1)
            return (z * w).sum(dim=1), w.squeeze(-1)

    class MultiModalTemporalFusion(nn.Module):
        """Intermediate-fusion, multi-horizon, uncertainty-capable network."""

        def __init__(self, seq_dims: dict[str, int], static_dim: int,
                     hidden: int = 64, kind: str = "gru", dropout: float = 0.2,
                     horizons: tuple[int, ...] = (7, 14)):
            super().__init__()
            self.seq_names = list(seq_dims)
            self.seq_encoders = nn.ModuleDict(
                {k: SequenceEncoder(v, hidden, kind, dropout)
                 for k, v in seq_dims.items()})
            self.static = (StaticEncoder(static_dim, hidden // 2, dropout)
                           if static_dim > 0 else None)
            dims = [self.seq_encoders[k].out_dim for k in self.seq_names]
            if self.static is not None:
                dims.append(self.static.out_dim)
            self.attn = BranchAttention(dims)
            self.trunk = nn.Sequential(nn.Linear(self.attn.out_dim, hidden),
                                       nn.ReLU(), nn.Dropout(dropout))
            self.heads = nn.ModuleDict(
                {str(h): nn.Linear(hidden, 1) for h in horizons})
            self.horizons = horizons

        def forward(self, seqs: dict[str, "torch.Tensor"],
                    static: "torch.Tensor | None" = None):
            hs = [self.seq_encoders[k](seqs[k]) for k in self.seq_names]
            if self.static is not None and static is not None:
                hs.append(self.static(static))
            fused, w = self.attn(hs)
            z = self.trunk(fused)
            return {h: self.heads[str(h)](z).squeeze(-1) for h in self.horizons}, w

    def mc_dropout_predict(model, seqs, static, passes: int = 50):
        """Epistemic uncertainty by keeping dropout active at inference."""
        model.train()
        acc: dict[int, list] = {h: [] for h in model.horizons}
        with torch.no_grad():
            for _ in range(passes):
                out, _ = model(seqs, static)
                for h, logit in out.items():
                    acc[h].append(torch.sigmoid(logit).cpu().numpy())
        model.eval()
        return {h: (np.mean(v, axis=0), np.std(v, axis=0))
                for h, v in acc.items()}

else:                                                # pragma: no cover
    class MultiModalTemporalFusion:                  # type: ignore
        def __init__(self, *a, **k):
            raise ImportError("PyTorch is required for the fusion model")

    def mc_dropout_predict(*a, **k):                 # type: ignore
        raise ImportError("PyTorch is required for MC dropout")


# --------------------------------------------------------------- sequence prep
def build_sequences(df, feature_groups: dict[str, list[str]], seq_len: int,
                    group_col: str = "site_id", date_col: str = "date",
                    y_cols: tuple[str, ...] = ("y_7", "y_14")):
    """Turn the tabular table into per-branch sequences ending at t.

    Only rows with a full `seq_len` history are emitted; short histories are
    dropped rather than zero-padded, so the model never trains on invented past.
    """
    import pandas as pd
    seqs: dict[str, list] = {k: [] for k in feature_groups if k != "static"}
    static, ys, meta = [], [], []
    for sid, g in df.sort_values([group_col, date_col]).groupby(group_col):
        g = g.reset_index(drop=True)
        for i in range(seq_len - 1, len(g)):
            win = g.iloc[i - seq_len + 1: i + 1]
            for k, cols in feature_groups.items():
                if k == "static":
                    continue
                use = [c for c in cols if c in win.columns]
                if not use:
                    continue
                seqs[k].append(win[use].to_numpy(dtype="float32"))
            if "static" in feature_groups:
                use = [c for c in feature_groups["static"] if c in g.columns]
                static.append(g.iloc[i][use].to_numpy(dtype="float32")
                              if use else np.zeros(0, dtype="float32"))
            ys.append([float(g.iloc[i].get(c, np.nan)) for c in y_cols])
            meta.append({group_col: sid, date_col: g.iloc[i][date_col]})
    out = {k: np.stack(v) for k, v in seqs.items() if v}
    return (out,
            np.stack(static) if static else None,
            np.asarray(ys, dtype="float32"),
            pd.DataFrame(meta))
