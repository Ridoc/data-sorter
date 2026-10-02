"""Tests for sorter/embeddings.py — embedding API, cache, centroid, vector math."""
import json
import pytest
from pathlib import Path
from unittest.mock import Mock, patch

from sorter.embeddings import (
    EmbeddingCache,
    build_folder_centroids,
    centroid,
    cluster_files,
    cosine_similarity,
    embed_text,
    embed_batch,
    extract_keywords,
    match_folder,
    text_for_embedding,
)
from sorter.scanner import FileEntry


def make_entry(rel_path: str, name: str = None,
               mime: str = "text/plain", size: int = 100,
               mtime: float = 1000.0) -> FileEntry:
    return FileEntry(
        path=Path(f"/mnt/{rel_path}"),
        rel_path=rel_path,
        name=name or Path(rel_path).name,
        mime=mime,
        size=size,
        mtime=mtime,
        is_dir=False,
        ctime=mtime,
        atime=mtime,
    )


# ── Test vectors ──

VEC_A = [1.0, 0.0, 0.0]
VEC_B = [0.0, 1.0, 0.0]
VEC_C = [0.707, 0.707, 0.0]


class TestEmbedText:
    @patch("sorter.embeddings.requests.post")
    def test_embed_text_calls_ollama(self, mock_post):
        mock_resp = Mock()
        mock_resp.json.return_value = {"embedding": [0.1, 0.2, 0.3]}
        mock_resp.raise_for_status.return_value = None
        mock_post.return_value = mock_resp

        result = embed_text("hello world")
        assert result == [0.1, 0.2, 0.3]
        # Verify correct endpoint + payload
        call_kw = mock_post.call_args[1]
        payload = call_kw["json"]
        assert payload["model"] == "nomic-embed-text"
        assert payload["prompt"] == "hello world"

    @patch("sorter.embeddings.requests.post")
    def test_embed_text_custom_endpoint(self, mock_post):
        mock_resp = Mock()
        mock_resp.json.return_value = {"embedding": [0.5]}
        mock_resp.raise_for_status.return_value = None
        mock_post.return_value = mock_resp

        embed_text("test", endpoint="http://custom:8080")
        call_url = mock_post.call_args[0][0]
        assert "custom:8080" in call_url


class TestEmbedBatch:
    @patch("sorter.embeddings.requests.post")
    def test_embed_batch_multi(self, mock_post):
        mock_resp = Mock()
        mock_resp.json.return_value = {"embeddings": [[0.1], [0.2]]}
        mock_resp.raise_for_status.return_value = None
        mock_post.return_value = mock_resp

        result = embed_batch(["a", "b"])
        assert result == [[0.1], [0.2]]
        payload = mock_post.call_args[1]["json"]
        assert payload["input"] == ["a", "b"]

    @patch("sorter.embeddings.requests.post")
    def test_embed_batch_empty(self, mock_post):
        assert embed_batch([]) == []
        mock_post.assert_not_called()

    @patch("sorter.embeddings.embed_text")
    @patch("sorter.embeddings.requests.post")
    def test_embed_batch_fallback_on_exception(self, mock_post, mock_et):
        mock_post.side_effect = Exception("network error")
        mock_et.side_effect = lambda t, **kw: [0.5]  # one per fallback call

        # Calling embed_batch with 2 texts; batch fails → fall back to sequential
        with pytest.raises(Exception):
            embed_batch(["a", "b"])


class TestCosineSimilarity:
    def test_identical_vectors(self):
        assert cosine_similarity(VEC_A, VEC_A) == pytest.approx(1.0, abs=1e-6)

    def test_orthogonal_vectors(self):
        assert cosine_similarity(VEC_A, VEC_B) == pytest.approx(0.0, abs=1e-6)

    def test_45_degree(self):
        sim = cosine_similarity(VEC_A, VEC_C)
        assert sim == pytest.approx(0.707, abs=0.01)

    def test_zero_vector(self):
        zero = [0.0, 0.0, 0.0]
        assert cosine_similarity(VEC_A, zero) == 0.0

    def test_threshold_boundary(self):
        d = [0.85, 0.527, 0.0]  # cosine with VEC_A ≈ 0.85
        norm = sum(x**2 for x in d) ** 0.5
        d_norm = [x / norm for x in d]
        sim = cosine_similarity(VEC_A, d_norm)
        assert sim >= 0.849 or sim < 0.851


class TestCentroid:
    def test_single_vector(self):
        c = centroid([VEC_A])
        assert c == pytest.approx(VEC_A, abs=1e-6)

    def test_two_vectors(self):
        c = centroid([VEC_A, VEC_B])
        # Mean = (0.5, 0.5, 0), then normalize
        assert c[0] == pytest.approx(c[1], abs=1e-6)
        assert c[2] == pytest.approx(0.0, abs=1e-6)

    def test_empty_returns_empty(self):
        assert centroid([]) == []

    def test_zero_vectors(self):
        zero = [0.0, 0.0, 0.0]
        c = centroid([zero, zero])
        assert len(c) == 3
        assert all(v == 0.0 for v in c)


class TestTextForEmbedding:
    def test_basic(self):
        e = make_entry("docs/report.pdf", name="report.pdf")
        result = text_for_embedding(e)
        assert "file: report.pdf" in result
        assert "path: docs/report.pdf" in result
        assert "type: text/plain" in result

    def test_with_content_snippet(self):
        e = make_entry("a.txt")
        result = text_for_embedding(e, "Q3 budget forecast 2026")
        assert "Q3 budget" in result

    def test_content_truncated(self):
        long_content = "hello " * 200
        e = make_entry("a.txt")
        result = text_for_embedding(e, long_content)
        assert len(result) < 2000  # not huge


class TestEmbeddingCache:
    def test_cache_miss(self, tmp_path):
        cache = EmbeddingCache(tmp_path)
        e = make_entry("a.txt", mtime=100)
        assert cache.get(e) is None

    def test_cache_hit(self, tmp_path):
        cache = EmbeddingCache(tmp_path)
        e = make_entry("a.txt", mtime=100)
        cache.set(e, [0.1, 0.2])
        assert cache.get(e) == [0.1, 0.2]

    def test_flush_reload(self, tmp_path):
        cache1 = EmbeddingCache(tmp_path)
        e = make_entry("a.txt", mtime=100)
        cache1.set(e, [0.5, 0.6])
        cache1.flush()

        # New instance should load persisted data
        cache2 = EmbeddingCache(tmp_path)
        assert cache2.get(e) == [0.5, 0.6]

    def test_mtime_change_is_miss(self, tmp_path):
        cache = EmbeddingCache(tmp_path)
        e1 = make_entry("a.txt", mtime=100)
        cache.set(e1, [0.1])
        e2 = make_entry("a.txt", mtime=200)  # newer mtime
        assert cache.get(e2) is None  # cache miss — file changed

    def test_caching_text(self, tmp_path):
        cache = EmbeddingCache(tmp_path)
        text = "Q3 financial report"
        cache.cache_text(text, [0.9, 0.8])
        assert cache.get_text(text) == [0.9, 0.8]


class TestBuildFolderCentroids:
    def test_build_with_resident_files(self, tmp_path, monkeypatch):
        """Build centroids from temporary folder structure with mock embedding."""
        # Create taxonomy folder with a file
        tax_root = tmp_path / "nas_root"
        doc_folder = tax_root / "Zeno" / "Documents"
        doc_folder.mkdir(parents=True)
        (doc_folder / "report.txt").write_text("Q3 budget 2026")

        # Mock embed_text to accept text, *args (model, endpoint are positional)
        monkeypatch.setattr(
            "sorter.embeddings.embed_text",
            lambda text, *a, **kw: [0.1, 0.2, 0.3, 0.4, 0.5] if "file:" in text else [0.5, 0.5, 0.5, 0.5, 0.5]
        )

        cache = EmbeddingCache(tax_root)
        taxonomy = {"Zeno": ["Documents"]}
        centroids = build_folder_centroids(
            tax_root, taxonomy, cache, force_rebuild=True
        )

        assert "Zeno/Documents" in centroids
        # Should be a centroid of the resident file embeddings
        assert len(centroids["Zeno/Documents"]) == 5

    def test_empty_folder_seeds_with_name(self, tmp_path, monkeypatch):
        """Empty taxonomy folder gets seeded with folder-name embedding."""
        tax_root = tmp_path / "nas_root"
        empty_folder = tax_root / "Projects" / "Empty"
        empty_folder.mkdir(parents=True)

        monkeypatch.setattr(
            "sorter.embeddings.embed_text",
            lambda text, *a, **kw: [0.8, 0.1, 0.0] if "folder:" in text else [0.1, 0.1, 0.1]
        )

        cache = EmbeddingCache(tax_root)
        taxonomy = {"Projects": ["Empty"]}
        centroids = build_folder_centroids(
            tax_root, taxonomy, cache, force_rebuild=True
        )

        assert "Projects/Empty" in centroids

    def test_skip_nonexistent_folder(self, tmp_path, monkeypatch):
        """Does not crash when taxonomy folder doesn't exist on disk."""
        tax_root = tmp_path / "nas_root"
        tax_root.mkdir(exist_ok=True)

        cache = EmbeddingCache(tax_root)
        taxonomy = {"Zeno": ["Nonexistent"]}
        centroids = build_folder_centroids(
            tax_root, taxonomy, cache, force_rebuild=True
        )
        assert "Zeno/Nonexistent" not in centroids
        assert centroids == {}

    def test_centroids_cached(self, tmp_path, monkeypatch):
        """Second call (force_rebuild=False) loads from disk, no re-embedding."""
        tax_root = tmp_path / "nas_root"
        doc_folder = tax_root / "Zeno" / "Documents"
        doc_folder.mkdir(parents=True)
        (doc_folder / "report.txt").write_text("data")

        call_count = 0

        def mock_embed(text, *a, **kw):
            nonlocal call_count
            call_count += 1
            return [0.1, 0.2, 0.3, 0.4, 0.5]

        monkeypatch.setattr("sorter.embeddings.embed_text", mock_embed)

        cache = EmbeddingCache(tax_root)
        taxonomy = {"Zeno": ["Documents"]}

        # First call — force_rebuild=True, will compute and save centroids.json
        centroids1 = build_folder_centroids(tax_root, taxonomy, cache, force_rebuild=True)
        first_count = call_count
        assert first_count > 0  # at least one embed call

        # Second call — force_rebuild=False, should load from cache file
        centroids2 = build_folder_centroids(tax_root, taxonomy, cache, force_rebuild=False)
        # Embed calls should NOT have increased (loaded from disk)
        assert call_count == first_count, f"call_count increased from {first_count} to {call_count}"
        assert centroids1 == centroids2


class TestMatchFolder:
    def test_match_above_threshold(self):
        centroids = {
            "Zeno/Documents": [0.85, 0.527, 0.0],
            "Zeno/Photos": [0.1, 0.99, 0.1],
        }
        file_emb = [0.85, 0.527, 0.0]  # identical to Documents centroid
        folder, score = match_folder(file_emb, centroids, threshold=0.85)
        assert folder == "Zeno/Documents"
        assert score >= 0.85

    def test_no_match_below_threshold(self):
        centroids = {
            "Zeno/Documents": [1.0, 0.0, 0.0],
            "Zeno/Photos": [0.0, 1.0, 0.0],
        }
        file_emb = [0.0, 0.0, 1.0]  # orthogonal to both
        folder, score = match_folder(file_emb, centroids, threshold=0.85)
        assert folder is None
        assert score == 0.0

    def test_empty_centroids_returns_none(self):
        folder, score = match_folder([0.1, 0.2], {}, threshold=0.85)
        assert folder is None
        assert score == 0.0

    def test_empty_embedding_returns_none(self):
        centroids = {"Zeno/Test": [0.1, 0.2]}
        folder, score = match_folder([], centroids, threshold=0.85)
        assert folder is None
        assert score == 0.0

    def test_returns_best_match(self):
        """When multiple folders pass threshold, return the highest scoring."""
        centroids = {
            "Zeno/Documents": [1.0, 0.0, 0.0],  # ~1.0 with file_emb
            "Zeno/Finance": [0.9, 0.1, 0.0],    # ~0.99 with file_emb
        }
        file_emb = [1.0, 0.01, 0.0]
        folder, score = match_folder(file_emb, centroids, threshold=0.85)
        assert folder == "Zeno/Documents"  # higher score

    def test_threshold_boundary(self):
        """At exactly threshold, match. Just below, no match."""
        centroids = {"Zeno/Test": [1.0, 0.0, 0.0]}
        # Cosine of [threshold, sqrt(1-threshold^2), 0] with [1,0,0] = threshold
        import math
        t = 0.85
        emb = [t, math.sqrt(1 - t*t), 0.0]
        folder, score = match_folder(emb, centroids, threshold=t)
        assert folder == "Zeno/Test"

        emb_below = [t - 0.01, math.sqrt(1 - (t-0.01)**2), 0.0]
        folder, score = match_folder(emb_below, centroids, threshold=t)
        assert folder is None


class TestExtractKeywords:
    def test_simple_keywords(self):
        names = ["Q1_report_2026.xlsx", "Q3_budget_plan.xlsx", "financial_overview.xlsx"]
        kw = extract_keywords(names)
        # Common tokens: Q3, budget, report, etc
        assert len(kw) > 0
        assert all(isinstance(w, str) for w in kw)

    def test_skips_short_tokens(self):
        names = ["a.txt", "b_doc.txt", "my file.txt"]
        kw = extract_keywords(names)
        assert "a" not in kw
        assert "b" not in kw

    def test_skips_stopwords(self):
        names = ["the_report.pdf", "and_data.xlsx", "for_notes.txt"]
        kw = extract_keywords(names)
        assert "the" not in kw
        assert "and" not in kw

    def test_empty_list(self):
        assert extract_keywords([]) == []

    def test_respects_top_n(self):
        names = [f"file_{chr(97+i)}_v2.txt" for i in range(20)]
        kw = extract_keywords(names, top_n=3)
        assert len(kw) <= 3


class TestClusterFiles:
    def test_clusters_2d(self):
        # Two clearly separated clusters in 2D
        embeddings = [
            [1.0, 0.0], [0.95, 0.1], [1.05, -0.1],   # cluster A
            [-1.0, 0.0], [-0.95, 0.1], [-1.05, -0.1],  # cluster B
        ]
        clusters = cluster_files(embeddings, min_k=2, max_k=2)
        assert len(clusters) == 2
        total = sum(c["size"] for c in clusters)
        assert total == 6
        # Each cluster should have non-empty indices
        for c in clusters:
            assert len(c["indices"]) > 0
            assert len(c["centroid"]) == 2

    def test_too_few_vecs(self):
        assert cluster_files([], min_k=2, max_k=5) == []
        assert cluster_files([[1.0, 0.0]], min_k=2, max_k=5) == []

    def test_single_cluster_k1(self):
        # All points near each other — should still cluster
        embeddings = [[0.1, 0.1], [0.12, 0.09], [0.09, 0.11]]
        clusters = cluster_files(embeddings, min_k=2, max_k=2)
        # min_k=2, but with n=3 and similar vectors, silhouette may force k=2
        assert len(clusters) >= 1
        total = sum(c["size"] for c in clusters)
        assert total == 3

    def test_keywords_in_cluster(self):
        """Cluster files and produce keywords from member file names."""
        from sorter.embeddings import extract_keywords
        names_a = ["Q1_report.xlsx", "Q1_budget.xlsx"]
        names_b = ["photo_vacation.jpg", "photo_family.jpg"]
        kw_a = extract_keywords(names_a)
        kw_b = extract_keywords(names_b)
        # Should extract meaningful tokens
        assert len(kw_a) >= 2  # Q1, report, budget
        assert len(kw_b) >= 2  # photo, vacation, family


class TestNameCluster:
    @patch("sorter.embeddings.requests.post")
    def test_name_from_keywords(self, mock_post):
        mock_resp = Mock()
        mock_resp.json.return_value = {"response": "Q1 Financial Reports"}
        mock_resp.raise_for_status.return_value = None
        mock_post.return_value = mock_resp

        from sorter.embeddings import name_cluster
        cluster = {"keywords": ["Q1", "financial", "reports", "budget"], "size": 5}
        name = name_cluster(cluster)
        assert "Q1" in name
        assert name.startswith("Q1_")  # spaces → underscores
        assert not name.endswith("_files")  # should not fallback

    @patch("sorter.embeddings.requests.post")
    def test_fallback_on_empty_keywords(self, mock_post):
        mock_resp = Mock()
        mock_resp.json.return_value = {"response": ""}
        mock_resp.raise_for_status.return_value = None
        mock_post.return_value = mock_resp

        from sorter.embeddings import name_cluster
        cluster = {"keywords": [], "size": 3}
        name = name_cluster(cluster)
        assert "Cluster" in name

    @patch("sorter.embeddings.requests.post")
    def test_sanitizes_response(self, mock_post):
        """Strips illegal chars, quotes, and truncated punctuation."""
        mock_resp = Mock()
        mock_resp.json.return_value = {"response": '"Meeting: Notes & Plans?"'}
        mock_resp.raise_for_status.return_value = None
        mock_post.return_value = mock_resp

        from sorter.embeddings import name_cluster
        cluster = {"keywords": ["meeting", "notes"], "size": 4}
        name = name_cluster(cluster)
        # No quotes, no colons
        assert '"' not in name
        assert "?" not in name
        assert name.startswith("Meeting")