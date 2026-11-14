"""T02 image quality triage pipeline.

Only input data is read. Feature calibration uses the dev split, and the
hidden ground truth is never accessed by this module.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from .common.contact_sheet import render_contact_sheet
from .common.context import RunContext
from .common.decision import Decision, write_decision
from .common.image_ops import brightness_stats, laplacian_variance, load_rgb
from .common.manifest import verify_manifest
from .common.review import (
    apply_overrides,
    load_review_decisions,
    make_review_queue,
    write_review_queue,
)
from .metrics.tracks import get_track


IMAGE_COLUMNS = {"image_id", "path", "camera_id", "light", "split"}
RISK_WEIGHTS = {"blur": 0.5, "dark": 0.3, "glare": 0.2}


def _risk_score(
    values: pd.Series,
    reference: pd.Series,
    *,
    high_is_risky: bool,
    flat_reference_scale: float | None = None,
) -> pd.Series:
    """Map a feature to [0, 1], using quantiles from dev only."""
    values = pd.to_numeric(values, errors="raise").astype(float)
    reference = pd.to_numeric(reference, errors="raise").astype(float).dropna()

    if reference.empty or not np.isfinite(reference.to_numpy()).all():
        raise ValueError("cannot calibrate quality scores: dev values are empty or non-finite")

    q25, q50, q75 = (float(reference.quantile(q)) for q in (0.25, 0.50, 0.75))
    iqr = q75 - q25
    tolerance = max(1e-12, abs(q50) * 1e-9)

    if iqr > tolerance:
        scores = (values - q25) / iqr if high_is_risky else (q75 - values) / iqr
    elif high_is_risky:
        # Fractions are in [0, 1]; this catches a non-zero anomaly when dev is flat.
        scores = values / (flat_reference_scale or 0.1)
    else:
        scale = max(abs(q50), 1.0)
        scores = (q50 - values) / scale

    return scores.clip(lower=0.0, upper=1.0).fillna(0.0)


# ---------------------------------------------------------------- 01 load
def load(ctx: RunContext) -> dict:
    """Verify provenance and load the T02 input table."""
    manifest = ctx.input_dir / "manifest.csv"
    manifest_df = None

    if manifest.is_file():
        problems = verify_manifest(manifest)
        manifest_df = pd.read_csv(manifest)
    else:
        problems = [{"problem": "no_manifest"}]

    if problems:
        if ctx.audit is not None:
            ctx.audit.log(
                actor="pipeline",
                action="provenance_problems",
                item_id=str(manifest),
                after=problems[:20],
            )
        if ctx.config.get("strict_provenance", True):
            raise ValueError(
                f"provenance check failed ({len(problems)} problems), "
                f"first: {problems[:3]}"
            )

    images_path = ctx.input_dir / "images.csv"
    if not images_path.is_file():
        raise FileNotFoundError(f"T02 input table not found: {images_path}")

    images = pd.read_csv(images_path)
    missing = sorted(IMAGE_COLUMNS - set(images.columns))
    if missing:
        raise ValueError(f"{images_path} is missing required columns: {missing}")
    if images.empty:
        raise ValueError(f"{images_path} contains no image rows")

    images = images.copy()
    images["image_id"] = images["image_id"].astype(str).str.strip()
    images["path"] = images["path"].astype(str).str.strip()
    images["split"] = images["split"].astype(str).str.strip().str.lower()

    if images["image_id"].eq("").any() or images["image_id"].duplicated().any():
        raise ValueError("images.csv must have unique, non-empty image_id values")
    if images["path"].eq("").any() or images["split"].eq("").any():
        raise ValueError("images.csv contains an empty path or split value")

    return {"manifest": manifest_df, "images": images}


# ---------------------------------------------------------------- 02 process
def process(ctx: RunContext, data: dict) -> pd.DataFrame:
    """Score blur, underexposure, and glare with dev-only calibration."""
    if ctx.track != "t02":
        spec = get_track(ctx.track)
        raise NotImplementedError(
            f"src.pipeline implements T02 only; selected {spec.track} ({spec.title})"
        )

    images = data["images"]
    dev_split = str(ctx.param("dev_split", "dev")).strip().lower()
    dev_mask = images["split"].astype(str).str.strip().str.lower().eq(dev_split)
    if not dev_mask.any():
        raise ValueError(f"no rows for calibration split {dev_split!r} in images.csv")

    input_root = ctx.input_dir.resolve()
    rows: list[dict] = []

    for row in images.itertuples(index=False):
        image_path = (ctx.input_dir / row.path).resolve()
        if input_root not in image_path.parents:
            raise ValueError(f"image path escapes the input directory: {row.path}")
        if not image_path.is_file():
            raise FileNotFoundError(f"image listed in images.csv does not exist: {image_path}")

        image = load_rgb(image_path)
        sharpness = float(laplacian_variance(image))
        stats = brightness_stats(image)
        values = [sharpness, stats["frac_dark"], stats["frac_saturated"]]
        if not np.isfinite(values).all():
            raise ValueError(f"non-finite quality feature for image {row.image_id}")

        rows.append(
            {
                "image_id": row.image_id,
                "path": row.path,
                "camera_id": row.camera_id,
                "light": row.light,
                "split": row.split,
                "sharpness": sharpness,
                "frac_dark": float(stats["frac_dark"]),
                "frac_saturated": float(stats["frac_saturated"]),
            }
        )

    scored = pd.DataFrame(rows)
    calibration = scored.loc[dev_mask.to_numpy()]

    # Log compresses the skewed Laplacian range; eval is transformed with dev references.
    scored["log_sharpness"] = np.log1p(scored["sharpness"].clip(lower=0.0))
    dev_log_sharpness = np.log1p(calibration["sharpness"].clip(lower=0.0))

    scored["blur_score"] = _risk_score(
        scored["log_sharpness"], dev_log_sharpness, high_is_risky=False
    )
    scored["dark_score"] = _risk_score(
        scored["frac_dark"],
        calibration["frac_dark"],
        high_is_risky=True,
        flat_reference_scale=0.1,
    )
    scored["glare_score"] = _risk_score(
        scored["frac_saturated"],
        calibration["frac_saturated"],
        high_is_risky=True,
        flat_reference_scale=0.05,
    )

    scored["score"] = (
        RISK_WEIGHTS["blur"] * scored["blur_score"]
        + RISK_WEIGHTS["dark"] * scored["dark_score"]
        + RISK_WEIGHTS["glare"] * scored["glare_score"]
    ).clip(lower=0.0, upper=1.0)

    component = scored[["blur_score", "dark_score", "glare_score"]].idxmax(axis=1)
    scored["reason"] = component.map(
        {
            "blur_score": "blur_risk",
            "dark_score": "dark_risk",
            "glare_score": "glare_risk",
        }
    )
    scored["suggested_action"] = "RECAPTURE_IF_DEFECT_CONFIRMED"
    return scored


# ---------------------------------------------------------------- 03 rank
def rank(ctx: RunContext, scored: pd.DataFrame) -> pd.DataFrame:
    """Write the highest-risk images to the fixed-budget review queue."""
    missing = sorted({"image_id", "score"} - set(scored.columns))
    if missing:
        raise ValueError(f"rank(): scored data is missing required columns: {missing}")
    if scored["image_id"].duplicated().any():
        raise ValueError("rank(): image_id values must be unique")
    scores = pd.to_numeric(scored["score"], errors="raise")
    if not np.isfinite(scores.to_numpy()).all():
        raise ValueError("rank(): score values must be finite numbers")

    budget = int(ctx.param("budget", 20))
    if budget < 1:
        raise ValueError("review budget must be at least 1")

    queue = make_review_queue(
        scored,
        id_col="image_id",
        score_col="score",
        budget=budget,
        reason_col="reason" if "reason" in scored.columns else None,
        action_col="suggested_action" if "suggested_action" in scored.columns else None,
        slice_col="light" if "light" in scored.columns else None,
        evidence_col="path" if "path" in scored.columns else None,
    )
    write_review_queue(queue, ctx.out("review_queue.csv"))

    if "path" in scored.columns:
        render_contact_sheet(
            queue,
            ctx.out("contact_sheet.html"),
            image_root=ctx.input_dir,
            title=f"T02 review queue ({len(queue)} images)",
        )

    ctx.stats["review_queue_size"] = len(queue)
    return queue


# ---------------------------------------------------------------- 04 review
def review(
    ctx: RunContext,
    scored: pd.DataFrame,
    queue: pd.DataFrame,
) -> pd.DataFrame:
    """Apply and audit human decisions for the current queue.

    approve confirms a defect; reject marks a false alarm and defaults its score
    to zero; edit requires a replacement score in [0, 1]; escalate is unresolved;
    skip remains pending.
    """
    reviewed_path = ctx.output_dir / "review_queue_reviewed.csv"
    final = scored.copy()
    final["reviewer"] = ""
    final["review_decision"] = ""
    final["review_note"] = ""

    if not reviewed_path.is_file():
        ctx.stats.update(
            {
                "n_reviewed": 0,
                "n_approved": 0,
                "n_rejected": 0,
                "n_edited": 0,
                "n_escalated": 0,
                "n_skipped": 0,
            }
        )
        return final

    reviewed = load_review_decisions(reviewed_path)
    if reviewed["item_id"].duplicated().any():
        raise ValueError("review file contains duplicate item_id decisions")

    queue_ids = set(queue["item_id"].astype(str))
    submitted_ids = set(reviewed["item_id"].astype(str))
    unknown_ids = sorted(submitted_ids - queue_ids)
    if unknown_ids:
        raise ValueError(
            "review file contains items outside the current review queue: "
            f"{unknown_ids[:5]}"
        )

    queue_scores = queue.set_index("item_id")["score"].astype(float)
    for decision in reviewed.itertuples(index=False):
        current_score = float(queue_scores.loc[str(decision.item_id)])
        if not np.isclose(float(decision.score), current_score, atol=1e-6):
            raise ValueError(
                f"stale review row for {decision.item_id}: score changed since queue generation; "
                "review the current queue again"
            )

    reviewed = reviewed.copy()
    # load_review_decisions reads every CSV field as a string. Use object dtype
    # here so validated numeric overrides can be stored as floats; this also
    # keeps apply_overrides from assigning text into the numeric score column.
    reviewed["override_value"] = reviewed["override_value"].astype(object)
    for index, decision in reviewed.iterrows():
        action = str(decision["reviewer_decision"]).strip().lower()
        override_text = str(decision["override_value"]).strip()

        if action == "edit":
            if not override_text:
                raise ValueError(
                    f"review item {decision['item_id']} uses edit but has no override_value"
                )
            try:
                override = float(override_text)
            except ValueError as exc:
                raise ValueError(
                    f"override_value for {decision['item_id']} must be numeric"
                ) from exc
            if not np.isfinite(override) or not 0.0 <= override <= 1.0:
                raise ValueError(
                    f"override_value for {decision['item_id']} must be between 0 and 1"
                )
            reviewed.at[index, "override_value"] = override

        elif action == "reject":
            if override_text:
                try:
                    override = float(override_text)
                except ValueError as exc:
                    raise ValueError(
                        f"override_value for {decision['item_id']} must be numeric"
                    ) from exc
                if not np.isfinite(override) or not 0.0 <= override <= 1.0:
                    raise ValueError(
                        f"override_value for {decision['item_id']} must be between 0 and 1"
                    )
                reviewed.at[index, "override_value"] = override
            else:
                reviewed.at[index, "override_value"] = 0.0

    final = apply_overrides(
        final,
        reviewed,
        id_col="image_id",
        value_col="score",
        audit=ctx.audit,
    )

    # apply_overrides logs edit/reject corrections. Log approve/escalate/skip too.
    override_ids = set(
        reviewed.loc[
            reviewed["reviewer_decision"].isin(["edit", "reject"]), "item_id"
        ].astype(str)
    )
    original_by_id = scored.set_index(scored["image_id"].astype(str))
    final_by_id = final.set_index(final["image_id"].astype(str))

    for decision in reviewed.itertuples(index=False):
        item_id = str(decision.item_id)
        before = float(original_by_id.loc[item_id, "score"])
        after = float(final_by_id.loc[item_id, "score"])
        mask = final["image_id"].astype(str).eq(item_id)
        final.loc[mask, "reviewer"] = decision.reviewer
        final.loc[mask, "review_decision"] = decision.reviewer_decision
        final.loc[mask, "review_note"] = decision.review_note

        if ctx.audit is not None and item_id not in override_ids:
            ctx.audit.log(
                actor=decision.reviewer,
                action=f"review:{decision.reviewer_decision}",
                item_id=item_id,
                before=before,
                after=after,
                reason=decision.review_note,
                reviewed_at=decision.reviewed_at,
            )

    counts = reviewed["reviewer_decision"].value_counts().to_dict()
    n_skipped = int(counts.get("skip", 0))
    ctx.stats.update(
        {
            "n_reviewed": len(reviewed) - n_skipped,
            "n_approved": int(counts.get("approve", 0)),
            "n_rejected": int(counts.get("reject", 0)),
            "n_edited": int(counts.get("edit", 0)),
            "n_escalated": int(counts.get("escalate", 0)),
            "n_skipped": n_skipped,
        }
    )
    return final


# ---------------------------------------------------------------- 05 decide
def decide(ctx: RunContext, final: pd.DataFrame) -> Decision:
    """Make a conservative release decision from completed human review."""
    raw_owner = str(ctx.config.get("owner", "")).strip()
    owner = raw_owner.split(":", 1)[-1].strip() if raw_owner.lower().startswith("todo") else raw_owner
    if not owner or owner.lower() in {"owner", "todo"}:
        raise ValueError("set a real name/role under owner in configs/lab.yaml")

    queue_size = int(ctx.stats.get("review_queue_size", 0))
    n_reviewed = int(ctx.stats.get("n_reviewed", 0))
    n_approved = int(ctx.stats.get("n_approved", 0))
    n_escalated = int(ctx.stats.get("n_escalated", 0))
    n_skipped = int(ctx.stats.get("n_skipped", 0))

    evidence = [
        str(ctx.output_dir / get_track(ctx.track).pred_file),
        str(ctx.output_dir / "review_queue.csv"),
        str(ctx.output_dir / "audit_log.jsonl"),
    ]
    metrics = {
        "n_items": int(len(final)),
        "review_queue_size": queue_size,
        "n_reviewed": n_reviewed,
        "n_approved": n_approved,
        "n_escalated": n_escalated,
        "n_skipped": n_skipped,
    }

    if n_escalated:
        return Decision(
            status="HOLD",
            reason_code="REVIEW_ESCALATED",
            reason=f"{n_escalated} image(s) need additional review",
            owner=owner,
            scope=ctx.track,
            evidence=evidence,
            metrics=metrics,
        )

    if n_reviewed < queue_size or n_skipped:
        return Decision(
            status="HOLD",
            reason_code="REVIEW_PENDING",
            reason=f"Review is incomplete: {n_reviewed}/{queue_size} completed; "
            f"{n_skipped} item(s) skipped",
            owner=owner,
            scope=ctx.track,
            evidence=evidence,
            metrics=metrics,
        )

    if n_approved:
        return Decision(
            status="REWORK",
            reason_code="CONFIRMED_DEFECTS",
            reason=f"Reviewer confirmed defects in {n_approved} image(s); recapture or repair is needed",
            owner=owner,
            scope=ctx.track,
            evidence=evidence,
            metrics=metrics,
        )

    return Decision(
        status="GO",
        reason_code="REVIEW_COMPLETE",
        reason=f"All {queue_size} review item(s) were assessed with no confirmed defects",
        owner=owner,
        scope=ctx.track,
        evidence=evidence,
        metrics=metrics,
    )


def run(ctx: RunContext) -> None:
    """Execute the complete T02 load, score, rank, review, decide flow."""
    spec = get_track(ctx.track)
    if ctx.track != "t02":
        raise NotImplementedError(f"src.pipeline currently supports t02 only, not {ctx.track}")

    data = load(ctx)
    scored = process(ctx, data)
    ctx.stats["n_items"] = len(scored)

    queue = rank(ctx, scored)
    final = review(ctx, scored, queue)

    # Required evaluator columns first; preserve other columns for auditability.
    columns = spec.pred_columns + [
        column for column in final.columns if column not in spec.pred_columns
    ]
    final[columns].to_csv(ctx.out(spec.pred_file), index=False)

    decision = decide(ctx, final)
    write_decision(decision, ctx.out("decision.json"), ctx.out("decision_log.csv"))
