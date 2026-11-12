"""日线序列 Transformer 塔（四头 ŷ_* 共用 · L4 研究轨）。"""

from __future__ import annotations

from typing import Any, Optional

import torch
import torch.nn as nn


class SeqTransformer(nn.Module):
    """Encoder：投影 → TransformerEncoder → 末步 + gap → 线性头。"""

    def __init__(
        self,
        *,
        d_in: int = 8,
        d_model: int = 32,
        nhead: int = 4,
        nlayers: int = 2,
        dim_feedforward: int = 64,
        dropout: float = 0.1,
        use_gap: bool = True,
    ) -> None:
        super().__init__()
        self.d_in = int(d_in)
        self.d_model = int(d_model)
        self.use_gap = bool(use_gap)
        self.proj = nn.Linear(self.d_in, self.d_model)
        enc_layer = nn.TransformerEncoderLayer(
            d_model=self.d_model,
            nhead=max(1, int(nhead)),
            dim_feedforward=max(self.d_model, int(dim_feedforward)),
            dropout=float(dropout),
            batch_first=True,
            norm_first=True,
            activation="gelu",
        )
        try:
            self.encoder = nn.TransformerEncoder(
                enc_layer, num_layers=max(1, int(nlayers)), enable_nested_tensor=False
            )
        except TypeError:
            self.encoder = nn.TransformerEncoder(
                enc_layer, num_layers=max(1, int(nlayers))
            )
        head_in = self.d_model + (1 if self.use_gap else 0)
        self.head = nn.Sequential(
            nn.LayerNorm(head_in),
            nn.Linear(head_in, 1),
        )

    def forward(
        self,
        x: torch.Tensor,
        gap: Optional[torch.Tensor] = None,
    ) -> torch.Tensor:
        h = self.proj(x)
        h = self.encoder(h)
        pooled = h[:, -1, :]
        if self.use_gap:
            if gap is None:
                g = torch.zeros(pooled.shape[0], 1, device=pooled.device, dtype=pooled.dtype)
            else:
                g = gap.reshape(-1, 1).to(dtype=pooled.dtype)
            pooled = torch.cat([pooled, g], dim=-1)
        return self.head(pooled).squeeze(-1)

    def encode(
        self,
        x: torch.Tensor,
        gap: Optional[torch.Tensor] = None,
    ) -> torch.Tensor:
        """返回拼接 gap 后的向量（供 Arm C 融合）。"""
        h = self.proj(x)
        h = self.encoder(h)
        pooled = h[:, -1, :]
        if self.use_gap:
            if gap is None:
                g = torch.zeros(
                    pooled.shape[0], 1, device=pooled.device, dtype=pooled.dtype
                )
            else:
                g = gap.reshape(-1, 1).to(dtype=pooled.dtype)
            pooled = torch.cat([pooled, g], dim=-1)
        return pooled


class FactorMLP(nn.Module):
    """Arm A：决策日因子向量 → 小 MLP → 标量。"""

    def __init__(
        self,
        *,
        d_in: int,
        hidden: int = 64,
        dropout: float = 0.1,
    ) -> None:
        super().__init__()
        h = max(16, int(hidden))
        self.net = nn.Sequential(
            nn.LayerNorm(int(d_in)),
            nn.Linear(int(d_in), h),
            nn.GELU(),
            nn.Dropout(float(dropout)),
            nn.Linear(h, h // 2),
            nn.GELU(),
            nn.Dropout(float(dropout)),
            nn.Linear(h // 2, 1),
        )

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        return self.net(x).squeeze(-1)


class SeqFactorFusion(nn.Module):
    """Arm C：序列 Encoder 表征 ∥ 因子 MLP 隐层 → 标量。"""

    def __init__(
        self,
        *,
        d_seq_in: int = 8,
        d_factor: int,
        d_model: int = 32,
        nhead: int = 4,
        nlayers: int = 2,
        factor_hidden: int = 64,
        dropout: float = 0.1,
    ) -> None:
        super().__init__()
        self.seq = SeqTransformer(
            d_in=d_seq_in,
            d_model=d_model,
            nhead=nhead,
            nlayers=nlayers,
            dim_feedforward=max(64, int(d_model) * 2),
            dropout=dropout,
            use_gap=True,
        )
        fh = max(16, int(factor_hidden))
        self.factor_trunk = nn.Sequential(
            nn.LayerNorm(int(d_factor)),
            nn.Linear(int(d_factor), fh),
            nn.GELU(),
            nn.Dropout(float(dropout)),
        )
        head_in = int(d_model) + 1 + fh
        self.head = nn.Sequential(
            nn.LayerNorm(head_in),
            nn.Linear(head_in, 1),
        )

    def forward(
        self,
        x_seq: torch.Tensor,
        gap: torch.Tensor,
        x_fac: torch.Tensor,
    ) -> torch.Tensor:
        seq_h = self.seq.encode(x_seq, gap)
        fac_h = self.factor_trunk(x_fac)
        return self.head(torch.cat([seq_h, fac_h], dim=-1)).squeeze(-1)


def count_parameters(model: nn.Module) -> int:
    return int(sum(p.numel() for p in model.parameters() if p.requires_grad))


def pick_device(prefer: Optional[str] = None) -> torch.device:
    pref = str(prefer or "").strip().lower()
    if pref in ("cpu", "mps", "cuda"):
        if pref == "mps" and getattr(torch.backends, "mps", None) and torch.backends.mps.is_available():
            return torch.device("mps")
        if pref == "cuda" and torch.cuda.is_available():
            return torch.device("cuda")
        return torch.device("cpu")
    return torch.device("cpu")


def model_config_dict(model: SeqTransformer) -> dict[str, Any]:
    return {
        "d_in": model.d_in,
        "d_model": model.d_model,
        "use_gap": model.use_gap,
        "n_params": count_parameters(model),
        "arch": "SeqTransformer",
    }


__all__ = [
    "SeqTransformer",
    "FactorMLP",
    "SeqFactorFusion",
    "count_parameters",
    "pick_device",
    "model_config_dict",
]
