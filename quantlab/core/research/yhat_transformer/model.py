"""日线序列 Transformer 塔（四头 ŷ_* 共用 · L4 研究轨）。"""

from __future__ import annotations

from typing import Optional

import torch
import torch.nn as nn


class SeqTransformer(nn.Module):
    """Encoder：投影 → TransformerEncoder → 末步 + gap →（可选）线性头。

    ``with_head=False`` 时只作表征塔（Arm·T ``encode``）；不建死参数 ``head``。
    """

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
        with_head: bool = True,
    ) -> None:
        super().__init__()
        self.d_in = int(d_in)
        self.d_model = int(d_model)
        self.use_gap = bool(use_gap)
        self.with_head = bool(with_head)
        nh = max(1, int(nhead))
        if self.d_model % nh != 0:
            raise ValueError(
                f"d_model={self.d_model} 须能被 nhead={nh} 整除"
            )
        self.proj = nn.Linear(self.d_in, self.d_model)
        enc_layer = nn.TransformerEncoderLayer(
            d_model=self.d_model,
            nhead=nh,
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
        if self.with_head:
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
        if not self.with_head:
            raise RuntimeError(
                "SeqTransformer(with_head=False) 仅 encode；请用 encode() 或 SeqFactorTransformer"
            )
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
        """返回拼接 gap 后的向量（供 Arm·T 融合）。"""
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


class SeqFactorTransformer(nn.Module):
    """Arm·T（双支路晚融合）：序列 Encoder 表征 ∥ 因子 MLP 隐层 → 标量。"""

    def __init__(
        self,
        *,
        d_seq_in: int = 16,
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
            with_head=False,  # 晚融合只用 encode；避免未训死头占参/占梯度
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


__all__ = [
    "SeqTransformer",
    "SeqFactorTransformer",
    "count_parameters",
    "pick_device",
]
