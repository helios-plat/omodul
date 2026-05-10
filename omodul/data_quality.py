"""Group 6: Data Quality & Diagnostics modules."""

from __future__ import annotations

import numpy as np
import pandas as pd

import oprim
import oskill


def panel_data_quality_check(
    panel: pd.DataFrame,
    *,
    expected_freq: str = "1D",
    baseline_panel: pd.DataFrame | None = None,
    check_freshness: bool = True,
    max_acceptable_gap_periods: int = 3,
    outlier_threshold_zscore: float = 3.0,
    now_timestamp: pd.Timestamp | None = None,
    score_weights: dict | None = None,
    freshness_max_days: float = 7.0,
) -> dict:
    """Panel data quality check with weighted score.

    Calls:
        oskill.detect_outliers_robust, oskill.distribution_shift_test,
        oprim.gap_detect, oprim.lag_forward_fill
    """
    if panel.empty:
        raise ValueError("panel must not be empty")
    if score_weights is None:
        score_weights = {"gap": 0.3, "outlier": 0.3, "freshness": 0.2, "drift": 0.2}

    if now_timestamp is None:
        now_timestamp = pd.Timestamp.now()

    numeric_cols = panel.select_dtypes(include=[np.number]).columns.tolist()
    if not numeric_cols:
        raise ValueError("panel must have at least one numeric column")

    per_field = {}
    for col in numeric_cols:
        series = panel[col]
        field_result: dict = {}

        # Gap detection
        if isinstance(panel.index, pd.DatetimeIndex):
            gap_result = oprim.gap_detect(panel.index)
            n_gaps = len(gap_result)
        else:
            n_gaps = int(series.isna().sum())
        gap_score = max(0, 1.0 - n_gaps / max(max_acceptable_gap_periods * 3, 1))
        field_result["gaps"] = {"n_gaps": n_gaps, "score": gap_score}

        # Outlier detection
        valid_data = series.dropna().values
        if len(valid_data) > 5:
            outlier_result = oskill.detect_outliers_robust(
                valid_data, methods=["zscore", "iqr"],
                thresholds={"zscore": outlier_threshold_zscore},
            )
            n_outliers = outlier_result["n_outliers"]
            outlier_pct = n_outliers / len(valid_data)
        else:
            n_outliers = 0
            outlier_pct = 0.0
        outlier_score = max(0, 1.0 - outlier_pct * 10)
        field_result["outliers"] = {"n_outliers": n_outliers, "pct": outlier_pct, "score": outlier_score}

        # Freshness
        freshness_score = 1.0
        if check_freshness and isinstance(panel.index, pd.DatetimeIndex):
            last_valid = series.last_valid_index()
            if last_valid is not None:
                staleness = (now_timestamp - last_valid).total_seconds() / 86400
                freshness_score = max(0, 1.0 - staleness / freshness_max_days)
        field_result["freshness"] = {"score": freshness_score}

        # Drift detection
        drift_score = 1.0
        if baseline_panel is not None and col in baseline_panel.columns:
            base_vals = baseline_panel[col].dropna().values
            if len(base_vals) > 20 and len(valid_data) > 20:
                shift = oskill.distribution_shift_test(base_vals, valid_data)
                drift_score = 0.0 if shift["shift_detected"] else 1.0
        field_result["drift"] = {"score": drift_score}

        # Field score
        w = score_weights
        field_result["field_score"] = (
            gap_score * w.get("gap", 0.3) +
            outlier_score * w.get("outlier", 0.3) +
            freshness_score * w.get("freshness", 0.2) +
            drift_score * w.get("drift", 0.2)
        )
        per_field[col] = field_result

    # Overall score
    overall = float(np.mean([v["field_score"] for v in per_field.values()]))

    return {
        "per_field": per_field,
        "overall_score": overall,
        "issues_summary": {
            "fields_with_gaps": [k for k, v in per_field.items() if v["gaps"]["n_gaps"] > 0],
            "fields_with_outliers": [k for k, v in per_field.items() if v["outliers"]["n_outliers"] > 0],
            "stale_fields": [k for k, v in per_field.items() if v["freshness"]["score"] < 0.5],
            "fields_with_drift": [k for k, v in per_field.items() if v["drift"]["score"] < 0.5],
        },
        "panel_metadata": {
            "n_rows": len(panel), "n_columns": len(numeric_cols),
            "first_ts": str(panel.index[0]), "last_ts": str(panel.index[-1]),
        },
        "warnings": [],
    }


def cross_source_consistency_check(
    multi_source_data: pd.DataFrame,
    *,
    reference_source: str | None = None,
    consistency_threshold_corr: float = 0.95,
    include_outlier_detection: bool = True,
    shift_methods: list[str] | None = None,
) -> dict:
    """Multi-source data consistency check.

    Calls:
        oskill.distribution_shift_test, oskill.detect_outliers_robust,
        oprim.pearson_spearman_corr
    """
    if shift_methods is None:
        shift_methods = ["ks", "wasserstein"]
    if multi_source_data.empty:
        raise ValueError("multi_source_data must not be empty")
    if len(multi_source_data.columns) < 2:
        raise ValueError("multi_source_data must have at least 2 columns (sources)")

    sources = list(multi_source_data.columns)
    n_sources = len(sources)

    # Pairwise correlation (upper triangle only, symmetric fill)
    corr_data = {s: {s2: np.nan for s2 in sources} for s in sources}
    for i, s1 in enumerate(sources):
        corr_data[s1][s1] = 1.0
        for s2 in sources[i + 1:]:
            v1 = multi_source_data[s1].dropna()
            v2 = multi_source_data[s2].dropna()
            common = v1.index.intersection(v2.index)
            if len(common) > 10:
                corr_result = oprim.pearson_spearman_corr(
                    pd.Series(v1.loc[common].values),
                    pd.Series(v2.loc[common].values),
                )
                c = corr_result.get("pearson_r", np.nan)
            else:
                c = np.nan
            corr_data[s1][s2] = c
            corr_data[s2][s1] = c
    pairwise_corr = pd.DataFrame(corr_data)

    # Pairwise shift
    shift_data = {}
    for s1 in sources:
        row = {}
        for s2 in sources:
            if s1 == s2:
                row[s2] = False
            else:
                v1 = multi_source_data[s1].dropna().values
                v2 = multi_source_data[s2].dropna().values
                if len(v1) > 20 and len(v2) > 20:
                    shift = oskill.distribution_shift_test(v1, v2, methods=shift_methods)
                    row[s2] = shift["shift_detected"]
                else:
                    row[s2] = None
        shift_data[s1] = row
    pairwise_shift = pd.DataFrame(shift_data)

    # Outlier periods
    outlier_periods: dict[str, list] = {}
    if include_outlier_detection:
        for s in sources:
            vals = multi_source_data[s].dropna()
            if len(vals) > 10:
                result = oskill.detect_outliers_robust(vals.values)
                outlier_idx = vals.index[result["outlier_mask"]]
                outlier_periods[s] = list(outlier_idx)

    # Consistency scores
    consistency_scores = {}
    for s in sources:
        corrs = [pairwise_corr.loc[s, s2] for s2 in sources if s2 != s]
        valid_corrs = [c for c in corrs if not np.isnan(c)]
        consistency_scores[s] = float(np.mean(valid_corrs)) if valid_corrs else 0.0

    # Recommended source
    recommended = max(consistency_scores, key=consistency_scores.get) if consistency_scores else sources[0]

    # Find lowest correlation pair
    min_corr = 1.0
    min_pair = ""
    for i, s1 in enumerate(sources):
        for s2 in sources[i + 1:]:
            c = pairwise_corr.loc[s1, s2]
            if not np.isnan(c) and c < min_corr:
                min_corr = c
                min_pair = f"{s1}-{s2}"

    return {
        "pairwise_correlation": pairwise_corr,
        "pairwise_shift": pairwise_shift,
        "outlier_periods": outlier_periods,
        "consistency_scores": consistency_scores,
        "recommended_source": recommended,
        "summary": {
            "n_sources": n_sources,
            "all_consistent": all(c >= consistency_threshold_corr for c in consistency_scores.values()),
            "lowest_correlation_pair": min_pair,
        },
        "warnings": [],
    }
