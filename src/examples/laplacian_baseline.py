"""T02 baseline: one global Laplacian threshold calibrated from dev images.

The review ranking uses only whole-image Laplacian variance. No ground-truth
labels or eval statistics are used to choose the threshold.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from ..common.contact_sheet import render_contact_sheet
from ..common.context import RunContext
from ..common.decision import Decision, write_decision
from ..common.image_ops import laplacian_variance, load_rgb
from ..common.manifest import verify_manifest
from ..common.review import make_review_queue, write_review_queue


def run(ctx: RunContext) -> None:
    if ctx.track != "t02":
        raise ValueError("laplacian_baseline supports t02 only")

    manifest = ctx.input_dir / "manifest.csv"
    problems = verify_manifest(manifest) if manifest.is_file() else [{"problem": "no_manifest"}]
    if problems and ctx.config.get("strict_provenance", True):
        raise ValueError(f"baseline provenance check failed: {problems[:3]}")

    images = pd.read_csv(ctx.input_dir / "images.csv")
    required = {"image_id", "path", "split", "camera_id", "light"}
    missing = sorted(required - set(images.columns))
    if missing or images.empty or images["image_id"].isna().any() or images["image_id"].duplicated().any():
        raise ValueError(f"invalid T02 images.csv; missing columns: {missing}")

    root = ctx.input_dir.resolve()
    rows = []
    for row in images.itertuples(index=False):
        image_path = (ctx.input_dir / row.path).resolve()
        if root not in image_path.parents:
            raise ValueError(f"image path escapes input directory: {row.path}")
        sharpness = float(laplacian_variance(load_rgb(image_path)))
        if not np.isfinite(sharpness) or sharpness < 0:
            raise ValueError(f"invalid Laplacian variance for {row.image_id}")
        rows.append({
            "image_id": str(row.image_id), "path": row.path,
            "camera_id": row.camera_id, "light": row.light,
            "split": str(row.split).strip().lower(), "sharpness": sharpness,
        })

    scored = pd.DataFrame(rows)
    dev = scored.loc[scored["split"].eq(str(ctx.param("dev_split", "dev")).lower()), "sharpness"]
    if dev.empty:
        raise ValueError("no dev images available to set Laplacian threshold")

    # Fixed data-only rule: the 25th percentile of whole-image dev sharpness.
    # The threshold also gives the score its scale; ranking is inverse sharpness.
    threshold = max(float(dev.quantile(0.25)), 1e-12)
    scored["score"] = threshold / (threshold + scored["sharpness"])
    scored["pred_action"] = np.where(scored["sharpness"] <= threshold, "RECAPTURE", "KEEP")
    scored["reason"] = "low_laplacian_variance"

    ctx.stats["n_items"] = len(scored)
    ctx.stats["dev_threshold_laplacian_variance"] = threshold
    budget = int(ctx.param("budget", 20))
    queue = make_review_queue(
        scored, id_col="image_id", score_col="score", budget=budget,
        reason_col="reason", action_col="pred_action", slice_col="light", evidence_col="path",
    )
    ctx.stats["review_queue_size"] = len(queue)
    write_review_queue(queue, ctx.out("review_queue.csv"))
    render_contact_sheet(queue, ctx.out("contact_sheet.html"), image_root=ctx.input_dir,
                         title="T02 Laplacian baseline review queue")
    scored.to_csv(ctx.out("ranked_review.csv"), index=False)
    write_decision(
        Decision(
            status="HOLD", reason_code="BASELINE_ONLY",
            reason="Laplacian-only baseline; human review and PoC decision are separate",
            owner=str(ctx.config.get("owner", "baseline")), scope="t02",
            evidence=[str(ctx.output_dir / "ranked_review.csv")],
            metrics={"n_items": len(scored), "review_queue_size": len(queue)},
        ),
        ctx.out("decision.json"), ctx.out("decision_log.csv"),
    )
