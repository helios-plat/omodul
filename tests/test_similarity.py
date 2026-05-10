"""Tests for Group 7: Similarity modules."""

import numpy as np
import pandas as pd
import pytest

from omodul.similarity import event_cascade_clusterer, smart_peer_finder


class TestSmartPeerFinder:
    def test_basic_search(self):
        rng = np.random.default_rng(42)
        query = {"signature": rng.normal(0, 1, 10)}
        candidates = [{"id": f"C{i}", "signature": rng.normal(0, 1, 10)} for i in range(20)]
        result = smart_peer_finder(query, candidates, top_k=5)
        assert len(result["matches"]) == 5
        assert result["matches"][0]["rank"] == 1

    def test_with_explanation(self):
        rng = np.random.default_rng(42)
        query = {"signature": rng.normal(0, 1, 10)}
        candidates = [{"id": f"C{i}", "signature": rng.normal(0, 1, 10)} for i in range(5)]
        result = smart_peer_finder(query, candidates, include_explanation=True)
        assert "explanation" in result["matches"][0]

    def test_identical_candidate_rank_1(self):
        sig = np.array([1.0, 2.0, 3.0, 4.0, 5.0])
        query = {"signature": sig}
        candidates = [
            {"id": "other", "signature": np.random.default_rng(42).normal(0, 1, 5)},
            {"id": "same", "signature": sig.copy()},
        ]
        result = smart_peer_finder(query, candidates, methods=["cosine"])
        assert result["matches"][0]["candidate_id"] == "same"

    def test_empty_candidates_raises(self):
        with pytest.raises(ValueError, match="empty"):
            smart_peer_finder({"signature": np.array([1, 2])}, [])

    def test_missing_signature_raises(self):
        with pytest.raises(ValueError, match="signature"):
            smart_peer_finder({}, [{"id": "x", "signature": np.array([1])}])

    def test_with_timeseries(self):
        rng = np.random.default_rng(42)
        query = {"signature": rng.normal(0, 1, 5), "timeseries": pd.DataFrame({"x": rng.normal(0, 1, 20)})}
        candidates = [
            {"id": f"C{i}", "signature": rng.normal(0, 1, 5),
             "timeseries": pd.DataFrame({"x": rng.normal(0, 1, 20)})}
            for i in range(5)
        ]
        result = smart_peer_finder(query, candidates, methods=["cosine", "dtw"])
        assert len(result["matches"]) > 0


class TestEventCascadeClusterer:
    def test_basic_clustering(self):
        rng = np.random.default_rng(42)
        n = 30
        # Create 2 clusters + noise
        emb_cluster1 = rng.normal([1, 0, 0], 0.1, (10, 3))
        emb_cluster2 = rng.normal([0, 1, 0], 0.1, (10, 3))
        emb_noise = rng.normal(0, 1, (10, 3))
        embeddings = np.vstack([emb_cluster1, emb_cluster2, emb_noise])

        events = pd.DataFrame({
            "event_id": [f"E{i}" for i in range(n)],
            "timestamp": pd.date_range("2023-01-01", periods=n, freq="h"),
            "embedding": list(embeddings),
        })
        result = event_cascade_clusterer(events, eps=0.5, min_samples=3)
        assert "clusters" in result
        assert result["summary"]["n_events_total"] == n
        assert result["summary"]["n_clusters"] >= 1

    def test_with_time_window(self):
        rng = np.random.default_rng(42)
        n = 20
        events = pd.DataFrame({
            "event_id": [f"E{i}" for i in range(n)],
            "timestamp": pd.date_range("2023-01-01", periods=n, freq="D"),
            "embedding": [rng.normal(0, 0.1, 5) for _ in range(n)],
        })
        result = event_cascade_clusterer(events, eps=0.3, min_samples=2, time_window_hours=48)
        assert "clusters" in result

    def test_outlier_detection(self):
        rng = np.random.default_rng(42)
        n = 15
        embeddings = rng.normal(0, 0.1, (n, 5))
        embeddings[-1] = rng.normal(0, 10, 5)  # outlier
        events = pd.DataFrame({
            "event_id": [f"E{i}" for i in range(n)],
            "timestamp": pd.date_range("2023-01-01", periods=n, freq="h"),
            "embedding": list(embeddings),
        })
        result = event_cascade_clusterer(events, include_outlier_detection=True)
        assert result["outlier_events"] is not None

    def test_empty_raises(self):
        with pytest.raises(ValueError, match="empty"):
            event_cascade_clusterer(pd.DataFrame(columns=["event_id", "timestamp", "embedding"]))

    def test_missing_columns_raises(self):
        with pytest.raises(ValueError, match="columns"):
            event_cascade_clusterer(pd.DataFrame({"x": [1]}))
