"""Group 7: Similarity & Retrieval modules."""

from __future__ import annotations

from typing import Literal

import numpy as np
import pandas as pd
from sklearn.cluster import DBSCAN

import oprim
import oskill


def smart_peer_finder(
    query: dict,
    candidates: list[dict],
    *,
    methods: list[str] | None = None,
    ensemble: Literal["mean_rank", "borda", "weighted"] = "mean_rank",
    weights: dict | None = None,
    top_k: int = 10,
    include_explanation: bool = True,
) -> dict:
    """Smart peer finder using multi-dimensional similarity.

    Calls:
        oskill.historical_analogy_search, oprim.cosine_similarity_batch,
        oprim.euclidean_distance_matrix, oprim.percentile_rank
    """
    if methods is None:
        methods = ["cosine", "euclidean"]
    if not candidates:
        raise ValueError("candidates must not be empty")
    if "signature" not in query:
        raise ValueError("query must have 'signature' key")

    query_sig = np.asarray(query["signature"], dtype=np.float64)

    # Build candidate signatures
    candidate_sigs = []
    candidate_ids = []
    for c in candidates:
        if "signature" in c:
            candidate_sigs.append(np.asarray(c["signature"], dtype=np.float64))
            candidate_ids.append(c.get("id", len(candidate_ids)))

    if not candidate_sigs:
        raise ValueError("No valid candidate signatures found")

    sig_matrix = np.array(candidate_sigs)
    n_candidates = len(candidate_sigs)
    top_k = min(top_k, n_candidates)

    # If timeseries available, use historical_analogy_search
    has_ts = "timeseries" in query and query["timeseries"] is not None
    ts_methods = [m for m in methods if m in ("dtw", "wasserstein")]
    sig_methods = [m for m in methods if m in ("cosine", "euclidean")]

    # Compute signature-based distances
    scores = np.zeros(n_candidates)
    method_scores: dict[str, np.ndarray] = {}

    if "cosine" in sig_methods:
        sims = oprim.cosine_similarity_batch(query_sig, sig_matrix)
        method_scores["cosine"] = 1.0 - sims  # distance

    if "euclidean" in sig_methods:
        dists = oprim.euclidean_distance_matrix(query_sig.reshape(1, -1), sig_matrix)
        method_scores["euclidean"] = dists[0]

    # Time series methods via oskill
    if has_ts and ts_methods:
        query_ts = query["timeseries"]
        if isinstance(query_ts, pd.DataFrame):
            query_arr = query_ts.select_dtypes(include=[np.number]).iloc[:, 0].values
        else:
            query_arr = np.asarray(query_ts)

        candidate_ts = []
        for c in candidates:
            ts = c.get("timeseries")
            if ts is not None:
                if isinstance(ts, pd.DataFrame):
                    candidate_ts.append(ts.select_dtypes(include=[np.number]).iloc[:, 0].values)
                else:
                    candidate_ts.append(np.asarray(ts))
            else:
                candidate_ts.append(query_arr)  # fallback

        matches = oskill.historical_analogy_search(
            query_arr, candidate_ts, methods=ts_methods,
            ensemble=ensemble, top_k=n_candidates,
        )
        for match in matches:
            idx = match["historical_idx"]
            for m, dist in match["distances_per_method"].items():
                if m not in method_scores:
                    method_scores[m] = np.full(n_candidates, np.inf)
                method_scores[m][idx] = dist

    # Ensemble ranking
    from scipy.stats import rankdata
    ranks = {m: rankdata(d, method="average") for m, d in method_scores.items()}

    if ensemble == "mean_rank":
        final_scores = np.mean(list(ranks.values()), axis=0)
    elif ensemble == "borda":
        final_scores = -np.sum([(n_candidates + 1) - r for r in ranks.values()], axis=0)
    elif ensemble == "weighted":
        w = weights or {}
        final_scores = np.zeros(n_candidates)
        for m, r in ranks.items():
            final_scores += w.get(m, 1.0) * r
    else:
        final_scores = np.mean(list(ranks.values()), axis=0)

    # Top-K
    top_indices = np.argsort(final_scores)[:top_k]

    matches_result = []
    for rank_pos, idx in enumerate(top_indices):
        entry = {
            "rank": rank_pos + 1,
            "candidate_id": candidate_ids[idx],
            "ensemble_score": float(final_scores[idx]),
            "methods_scores": {m: float(method_scores[m][idx]) for m in method_scores},
        }
        if include_explanation:
            best_method = min(method_scores.keys(), key=lambda m: ranks[m][idx])
            entry["explanation"] = f"Most similar on {best_method} (rank {int(ranks[best_method][idx])})"
        matches_result.append(entry)

    return {
        "matches": matches_result,
        "summary": {
            "n_candidates": n_candidates,
            "methods_used": list(method_scores.keys()),
            "primary_similarity_dimension": min(method_scores.keys(),
                                                 key=lambda m: np.min(method_scores[m])) if method_scores else None,
        },
        "warnings": [],
    }


def event_cascade_clusterer(
    events: pd.DataFrame,
    *,
    eps: float = 0.3,
    min_samples: int = 3,
    time_window_hours: float | None = None,
    include_outlier_detection: bool = True,
) -> dict:
    """Event cascade clustering using DBSCAN.

    Calls:
        oprim.cosine_similarity_batch, oskill.detect_outliers_robust, sklearn.cluster.DBSCAN
    """
    required = {"event_id", "timestamp", "embedding"}
    if not required.issubset(events.columns):
        raise ValueError(f"events must have columns: {required}")
    if len(events) == 0:
        raise ValueError("events must not be empty")

    df = events.copy()
    df["timestamp"] = pd.to_datetime(df["timestamp"])
    df = df.sort_values("timestamp").reset_index(drop=True)

    # Extract embeddings
    embeddings = np.array(df["embedding"].tolist(), dtype=np.float64)

    # Compute distance matrix using oprim
    # cosine distance = 1 - cosine_similarity, clipped to [0, 2]
    n = len(embeddings)
    dist_matrix = np.zeros((n, n))
    for i in range(n):
        sims = oprim.cosine_similarity_batch(embeddings[i], embeddings)
        dist_matrix[i] = np.clip(1.0 - sims, 0.0, 2.0)

    # Apply time window constraint if specified
    if time_window_hours is not None:
        timestamps = df["timestamp"].values
        for i in range(n):
            for j in range(n):
                time_diff = abs((timestamps[i] - timestamps[j]) / np.timedelta64(1, "h"))
                if time_diff > time_window_hours:
                    dist_matrix[i, j] = 2.0  # Beyond threshold

    # DBSCAN clustering
    clustering = DBSCAN(eps=eps, min_samples=min_samples, metric="precomputed")
    labels = clustering.fit_predict(dist_matrix)

    # Build clusters
    clusters = []
    unique_labels = set(labels)
    unique_labels.discard(-1)

    for cluster_id in sorted(unique_labels):
        member_mask = labels == cluster_id
        member_indices = np.where(member_mask)[0]
        member_ids = df.iloc[member_indices]["event_id"].tolist()
        timestamps_cluster = df.iloc[member_indices]["timestamp"]

        # Centroid: closest to mean embedding
        cluster_embeddings = embeddings[member_mask]
        centroid_emb = cluster_embeddings.mean(axis=0)
        dists_to_centroid = np.linalg.norm(cluster_embeddings - centroid_emb, axis=1)
        centroid_idx = member_indices[np.argmin(dists_to_centroid)]

        clusters.append({
            "cluster_id": int(cluster_id),
            "member_event_ids": member_ids,
            "n_members": len(member_ids),
            "first_ts": timestamps_cluster.min(),
            "last_ts": timestamps_cluster.max(),
            "span_hours": float((timestamps_cluster.max() - timestamps_cluster.min()).total_seconds() / 3600),
            "centroid_event_id": df.iloc[centroid_idx]["event_id"],
        })

    noise_mask = labels == -1
    noise_events = df.iloc[np.where(noise_mask)[0]]["event_id"].tolist()

    # Outlier detection on embedding norms
    outlier_events = None
    if include_outlier_detection and n > 5:
        norms = np.linalg.norm(embeddings, axis=1)
        outlier_result = oskill.detect_outliers_robust(norms)
        outlier_events = df.iloc[np.where(outlier_result["outlier_mask"])[0]]["event_id"].tolist()

    return {
        "clusters": clusters,
        "noise_events": noise_events,
        "outlier_events": outlier_events,
        "summary": {
            "n_events_total": n,
            "n_clusters": len(clusters),
            "n_noise": len(noise_events),
            "largest_cluster_size": max((c["n_members"] for c in clusters), default=0),
        },
    }
