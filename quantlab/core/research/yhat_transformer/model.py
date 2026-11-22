"""日线序列 Transformer 塔（四头 ŷ_* 共用 · L4 研究轨）。"""

from __future__ import annotations

import math
from typing import Optional

import torch
import torch.nn as nn

from core.research.yhat_transformer.heads import ARM_T_D_MODEL, ARM_T_NLAYERS

def feedforward_width(d_model: int) -> int:
    """标准 Transformer 前馈：4×d_model。窄模型至少 64。"""
    return max(64, int(d_model) * 4)


class SeqTransformer(nn.Module):
    """投影 → 正弦位置 → TransformerEncoder → 末步。"""

    def __init__(
        self,
        *,
        d_in: int = 8,
        d_model: int = ARM_T_D_MODEL,
        nhead: int = 4,
        nlayers: int = ARM_T_NLAYERS,
        dim_feedforward: Optional[int] = None,
        dropout: float = 0.1,
    ) -> None:
        super().__init__()
        self.d_in = int(d_in)
        self.d_model = int(d_model)
        nh = max(1, int(nhead))
        if self.d_model % nh != 0:
            raise ValueError(f"d_model={self.d_model} 须能被 nhead={nh} 整除")
        ff = feedforward_width(self.d_model) if dim_feedforward is None else int(dim_feedforward)
        self.proj = nn.Linear(self.d_in, self.d_model)
        enc_layer = nn.TransformerEncoderLayer(
            d_model=self.d_model,
            nhead=nh,
            dim_feedforward=max(self.d_model, ff),
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

    def encode(self, x: torch.Tensor) -> torch.Tensor:
        """返回末步向量。位置 0 是最旧一天，末位是决策日。"""
        h = self.proj(x)
        h = self.encoder(h + sinusoidal_position(h))
        return h[:, -1, :]


def sinusoidal_position(h: torch.Tensor) -> torch.Tensor:
    """长度 × d_model 的固定正弦位置。位置 0 是窗口里最旧的一天，末位是决策日。"""
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


class ArmTModel(nn.Module):
    """Arm·T：序列编码器。末步 LayerNorm 之后拼 gap，线性头出标量。"""

    def __init__(
        self,
        *,
        d_seq_in: int = 16,
        d_model: int = ARM_T_D_MODEL,
        nhead: int = 4,
        nlayers: int = ARM_T_NLAYERS,
        dropout: float = 0.1,
    ) -> None:
        super().__init__()
        self.seq = SeqTransformer(
            d_in=d_seq_in,
            d_model=d_model,
            nhead=nhead,
            nlayers=nlayers,
            dim_feedforward=feedforward_width(d_model),
            dropout=dropout,
        )
        self.fuse_norm = nn.LayerNorm(int(d_model))
        self.head = nn.Linear(int(d_model) + 1, 1)

    def forward(
        self,
        x_seq: torch.Tensor,
        gap: Optional[torch.Tensor] = None,
    ) -> torch.Tensor:
        seq_h = self.fuse_norm(self.seq.encode(x_seq))
        if gap is None:
            g = torch.zeros(seq_h.shape[0], 1, device=seq_h.device, dtype=seq_h.dtype)
        else:
            g = gap.reshape(-1, 1).to(dtype=seq_h.dtype)
        return self.head(torch.cat([seq_h, g], dim=-1)).squeeze(-1)


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
    "feedforward_width",
    "SeqTransformer",
    "ArmTModel",
    "sinusoidal_position",
    "count_parameters",
    "pick_device",
]
