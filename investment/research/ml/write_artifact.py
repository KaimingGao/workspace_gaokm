"""写出 ML 研究 artifact（N2）；不触碰生产 score。"""

from __future__ import annotations

import argparse
import json
import os
from datetime import datetime
from typing import Any, Dict


def build_artifact(
    *,
    note: str = "",
    metrics: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    return {
        "kind": "ml_research_artifact",
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "features": [],
        "sample_range": {"start": None, "end": None},
        "metrics": metrics or {"status": "placeholder"},
        "model_path": None,
        "note": note
        or "离线研究占位；不得直连生产 score/stance。须人审 promote。",
        "constraints": {
            "writes_production_score": False,
            "requires_human_promote": True,
        },
    }


def write_artifact(path: str, artifact: Dict[str, Any]) -> str:
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(artifact, f, ensure_ascii=False, indent=2)
        f.write("\n")
    return path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Write ML research artifact (N2)")
    parser.add_argument(
        "--out",
        default=os.path.join(os.path.dirname(__file__), "artifact.json"),
        help="output path",
    )
    parser.add_argument("--note", default="")
    args = parser.parse_args(argv)
    art = build_artifact(note=args.note)
    if art["constraints"]["writes_production_score"]:
        raise SystemExit("artifact must not claim production score writes")
    path = write_artifact(args.out, art)
    print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
