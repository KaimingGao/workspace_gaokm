"""日线序列 Transformer 塔（四头 ŷ_* 共用 · L4 研究轨）。"""

from __future__ import annotations

import math
from typing import Optional

import torch
import torch.nn as nn

# 2：正弦位置编码、因子支路无输入 LayerNorm、gap 接在融合 LayerNorm 之后。
# 1：已落盘权重。缺省 arch 按 1 加载，不能 strict 载入 2。
ARCH_V2 = 2
ARCH_V1 = 1


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
        use_position: bool = False,
    ) -> None:
        super().__init__()
        self.d_in = int(d_in)
        self.d_model = int(d_model)
        self.use_gap = bool(use_gap)
        self.with_head = bool(with_head)
        self.use_position = bool(use_position)
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
        h = self._tokens(x)
        pooled = h[:, -1, :]
        if self.use_gap:
            if gap is None:
                g = torch.zeros(pooled.shape[0], 1, device=pooled.device, dtype=pooled.dtype)
            else:
                g = gap.reshape(-1, 1).to(dtype=pooled.dtype)
            pooled = torch.cat([pooled, g], dim=-1)
        return self.head(pooled).squeeze(-1)

    def _tokens(self, x: torch.Tensor) -> torch.Tensor:
        h = self.proj(x)
        if self.use_position:
            h = h + sinusoidal_position(h)
        return self.encoder(h)

    def encode(
        self,
        x: torch.Tensor,
        gap: Optional[torch.Tensor] = None,
    ) -> torch.Tensor:
        """返回末步向量。``use_gap`` 时再拼 gap。"""
        h = self._tokens(x)
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


def sinusoidal_position(h: torch.Tensor) -> torch.Tensor:
    """长度 × d_model 的固定正弦位置。位置 0 是窗口里最旧的一天。"""
    length, d_model = int(h.shape[1]), int(h.shape[-1])
    pos = torch.arange(length, device=h.device, dtype=h.dtype).unsqueeze(1)
    div = torch.exp(
        torch.arange(0, d_model, 2, device=h.device, dtype=h.dtype)
        * (-math.log(10000.0) / float(d_model))
    )
    pe = torch.zeros(length, d_model, device=h.device, dtype=h.dtype)
    pe[:, 0::2] = torch.sin(pos * div)
    pe[:, 1::2] = torch.cos(pos * div[: pe[:, 1::2].shape[-1]])
    return pe.unsqueeze(0)


class SeqFactorTransformer(nn.Module):
    """Arm·T（双支路晚融合）。

    ``arch=2``：正弦位置编码；因子支路 Linear→GELU→Dropout；
    先对序列末步和因子隐层做 LayerNorm，再拼 gap。
    ``arch=1``：已落盘结构，gap 在融合 LayerNorm 之内，无位置编码。
    """

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
        arch: int = ARCH_V2,
    ) -> None:
        super().__init__()
        self.arch = int(arch)
        if self.arch not in (ARCH_V1, ARCH_V2):
            raise ValueError(f"未知 arch={self.arch}；期望 1 或 2")
        v2 = self.arch == ARCH_V2
        self.seq = SeqTransformer(
            d_in=d_seq_in,
            d_model=d_model,
            nhead=nhead,
            nlayers=nlayers,
            dim_feedforward=max(64, int(d_model) * 2),
            dropout=dropout,
            use_gap=not v2,
            with_head=False,
            use_position=v2,
        )
        fh = max(16, int(factor_hidden))
        self.factor_hidden = fh
        if v2:
            self.factor_trunk = nn.Sequential(
                nn.Linear(int(d_factor), fh),
                nn.GELU(),
                nn.Dropout(float(dropout)),
            )
            self.fuse_norm = nn.LayerNorm(int(d_model) + fh)
            self.head = nn.Linear(int(d_model) + fh + 1, 1)
        else:
            self.factor_trunk = nn.Sequential(
                nn.LayerNorm(int(d_factor)),
                nn.Linear(int(d_factor), fh),
                nn.GELU(),
                nn.Dropout(float(dropout)),
            )
            self.fuse_norm = None
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
        fac_h = self.factor_trunk(x_fac)
        if self.arch == ARCH_V1:
            seq_h = self.seq.encode(x_seq, gap)
            return self.head(torch.cat([seq_h, fac_h], dim=-1)).squeeze(-1)
        seq_h = self.seq.encode(x_seq, None)
        fused = self.fuse_norm(torch.cat([seq_h, fac_h], dim=-1))
        if gap is None:
            g = torch.zeros(fused.shape[0], 1, device=fused.device, dtype=fused.dtype)
        else:
            g = gap.reshape(-1, 1).to(dtype=fused.dtype)
        return self.head(torch.cat([fused, g], dim=-1)).squeeze(-1)


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
    "ARCH_V1",
    "ARCH_V2",
    "SeqTransformer",
    "SeqFactorTransformer",
    "sinusoidal_position",
    "count_parameters",
    "pick_device",
]
