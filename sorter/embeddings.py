"""Text embedding via local Ollama (nomic-embed-text) with persistent cache.

Provides deterministic semantic signals for folder centroid matching,
cosine similarity routing, and K-means clustering of loose files.

Depends on: requests, numpy (for centroid arithmetic)
"""
from __future__ import annotations

import hashlib
import json
import os
import time
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np
import requests

from sorter.scanner import FileEntry

# ── Defaults ──

DEFAULT_ENDPOINT = "http://localhost:11434"
DEFAULT_MODEL = "nomic-embed-text"
_CACHE_DIR = ".sort_logs"
_CACHE_FILE = "embeddings_cache.json"


# ── Embedding API calls ──


def embed_text(
    text: str,
    model: str = DEFAULT_MODEL,
    endpoint: str = DEFAULT_ENDPOINT,
) -> List[float]:
    """Embed a single text string via Ollama /api/embeddings.

    Returns a 768-dimensional vector (list of floats).
    """
    payload = {"model": model, "prompt": text}
    resp = requests.post(
        f"{endpoint.rstrip('/')}/api/embeddings",
        json=payload,
        timeout=30,
    )
    resp.raise_for_status()
    data = resp.json()
    return data.get("embedding", [])


def embed_batch(
    texts: List[str],
    model: str = DEFAULT_MODEL,
    endpoint: str = DEFAULT_ENDPOINT,
) -> List[List[float]]:
    """Embed multiple texts in one round-trip via Ollama /api/embed.

    Returns a list of vectors, one per input text.
    Falls back to sequential embed_text for each text if batch fails.
    """
    if not texts:
        return []
    try:
        payload = {"model": model, "input": texts}
        resp = requests.post(
            f"{endpoint.rstrip('/')}/api/embed",
            json=payload,
            timeout=60,
        )
        resp.raise_for_status()
        data = resp.json()
        return data.get("embeddings", [])
    except Exception:
        # Batch failed — fall back one-by-one
        return [embed_text(t, model, endpoint) for t in texts]


# ── Cache ──


def _cache_path(nas_root: Path) -> Path:
    return nas_root / _CACHE_DIR / _CACHE_FILE


def _cache_key(entry: FileEntry) -> str:
    """Deterministic key for a file entry: path + mtime + size."""
    return f"{entry.rel_path}:{entry.mtime}:{entry.size}"


def _cache_key_text(text: str) -> str:
    """Deterministic key for a raw text string."""
    return f"_text:{hashlib.sha256(text.encode()).hexdigest()[:16]}:{len(text)}"


class EmbeddingCache:
    """Persistent cache mapping (file identity → embedding vector).

    Avoids re-embedding files across runs.  Stored as JSON at
    .sort_logs/embeddings_cache.json under the NAS root.
    """

    def __init__(self, nas_root: Path):
        self.path = _cache_path(nas_root)
        self._data: Dict[str, List[float]] = {}
        self._dirty = False
        self._load()

    def _load(self):
        if self.path.exists():
            try:
                with open(self.path) as f:
                    self._data = {k: list(v) for k, v in json.load(f).items()}
            except (json.JSONDecodeError, OSError):
                self._data = {}

    def _save(self):
        if not self._dirty:
            return
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with open(self.path, "w") as f:
                json.dump(self._data, f)
        except OSError:
            pass  # CIFS may block writes; skip silently

    def get(self, entry: FileEntry) -> Optional[List[float]]:
        return self._data.get(_cache_key(entry))

    def set(self, entry: FileEntry, embedding: List[float]):
        self._data[_cache_key(entry)] = embedding
        self._dirty = True

    def flush(self):
        self._save()

    def __len__(self) -> int:
        return len(self._data)

    def cache_text(self, text: str, embedding: List[float]):
        """Cache an embedding for a raw text (not file-backed)."""
        self._data[_cache_key_text(text)] = embedding
        self._dirty = True

    def get_text(self, text: str) -> Optional[List[float]]:
        return self._data.get(_cache_key_text(text))


# ── Vector math (numpy helpers, no sklearn dependency here) ──


def cosine_similarity(a: List[float], b: List[float]) -> float:
    """Cosine similarity between two vectors.  1.0 = identical, 0.0 = orthogonal."""
    va = np.array(a, dtype=np.float64)
    vb = np.array(b, dtype=np.float64)
    norm_a = np.linalg.norm(va)
    norm_b = np.linalg.norm(vb)
    if norm_a < 1e-12 or norm_b < 1e-12:
        return 0.0
    return float(np.dot(va, vb) / (norm_a * norm_b))


def centroid(vectors: List[List[float]]) -> List[float]:
    """Compute the mean (centroid) of multiple vectors, L2-normalized."""
    if not vectors:
        return []
    arr = np.array(vectors, dtype=np.float64)
    mean = arr.mean(axis=0)
    norm = np.linalg.norm(mean)
    if norm < 1e-12:
        return [0.0] * len(mean)
    return (mean / norm).tolist()


# ── Resolve embedding for a file entry (text for embed target) ──


def text_for_embedding(entry: FileEntry, content_snippet: str = "") -> str:
    """Build a string from a file's identity to use as embedding input.

    Combines: name + folder path + content snippet + mime.
    """
    parts = [
        f"file: {entry.name}",
        f"path: {entry.rel_path}",
        f"type: {entry.mime}",
    ]
    if content_snippet:
        snippet = content_snippet.strip()[:300]
        if snippet:
            parts.append(f"content: {snippet}")
    return "\n".join(parts)


# ── Folder centroid computation ──

_CENTROIDS_FILE = "centroids.json"

# MIME types that have extractable text content
_EMBEDDABLE_MIMES = {
    "application/pdf",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    "application/msword",
    "application/vnd.ms-excel",
    "application/vnd.ms-powerpoint",
    "text/plain", "text/markdown", "text/x-python", "text/html",
    "text/csv", "text/yaml", "text/vcard", "application/json",
}


def _centroids_path(nas_root: Path) -> Path:
    return nas_root / _CACHE_DIR / _CENTROIDS_FILE


def _load_centroids(nas_root: Path) -> Dict[str, List[float]]:
    path = _centroids_path(nas_root)
    if path.exists():
        try:
            with open(path) as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            return {}
    return {}


def _save_centroids(nas_root: Path, centroids: Dict[str, List[float]]):
    path = _centroids_path(nas_root)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w") as f:
            json.dump(centroids, f)
    except OSError:
        pass  # CIFS mount may block


def build_folder_centroids(
    nas_root: Path,
    taxonomy_categories: Dict[str, List[str]],
    cache: EmbeddingCache,
    model: str = DEFAULT_MODEL,
    endpoint: str = DEFAULT_ENDPOINT,
    force_rebuild: bool = False,
) -> Dict[str, List[float]]:
    """Compute centroids for each taxonomy folder by embedding its resident files.

    Returns dict mapping category_path (e.g. 'Zeno/Documents') to centroid vector.

    Empty folders → seeded with folder-name embedding (weak).
    Results cached in .sort_logs/centroids.json.
    """
    if not force_rebuild:
        cached = _load_centroids(nas_root)
        if cached:
            return cached

    centroids: Dict[str, List[float]] = {}

    for top, subs in taxonomy_categories.items():
        for sub in subs:
            cat_path = f"{top}/{sub}"
            folder = nas_root / cat_path
            if not folder.exists() or not folder.is_dir():
                continue

            # Collect all resident file entries
            resident_files: List[FileEntry] = []
            for child in folder.rglob("*"):
                if child.is_dir():
                    continue
                # Compute rel_path relative to nas_root
                try:
                    rel = child.relative_to(nas_root)
                except ValueError:
                    continue
                # Basic MIME guess
                from sorter.scanner import get_mime
                mime = get_mime(child)
                resident_files.append(FileEntry(
                    path=child,
                    rel_path=str(rel),
                    name=child.name,
                    mime=mime,
                    size=child.stat().st_size,
                    mtime=child.stat().st_mtime,
                    is_dir=False,
                    ctime=child.stat().st_ctime,
                    atime=child.stat().st_atime,
                ))

            resident_embeddings: List[List[float]] = []
            for entry in resident_files:
                cached_emb = cache.get(entry)
                if cached_emb is not None:
                    resident_embeddings.append(cached_emb)
                    continue
                # Embed from filename + path only (no content extraction needed for centroid)
                embed_text_str = text_for_embedding(entry, "")
                try:
                    emb = embed_text(embed_text_str, model, endpoint)
                except Exception:
                    continue
                cache.set(entry, emb)
                resident_embeddings.append(emb)

            if resident_embeddings:
                centroids[cat_path] = centroid(resident_embeddings)
            else:
                # Empty folder: seed with folder-name embedding
                try:
                    name_emb = embed_text(f"folder: {cat_path}", model, endpoint)
                    centroids[cat_path] = name_emb
                except Exception:
                    continue

    _save_centroids(nas_root, centroids)
    return centroids


# ── Cosine matcher ──

def match_folder(
    file_embedding: List[float],
    centroids: Dict[str, List[float]],
    threshold: float = 0.85,
) -> Tuple[Optional[str], float]:
    """Match a file embedding against folder centroids.

    Returns (best_category_path, score) if cosine similarity >= threshold,
    or (None, 0.0) if no folder matches.

    Args:
        file_embedding: embedding vector for the file
        centroids: dict mapping category_path → centroid vector
        threshold: minimum cosine similarity to accept (default 0.85)
    """
    if not file_embedding or not centroids:
        return None, 0.0

    best_folder: Optional[str] = None
    best_score = 0.0

    for cat_path, centroid_vec in centroids.items():
        sim = cosine_similarity(file_embedding, centroid_vec)
        if sim >= threshold and sim > best_score:
            best_score = sim
            best_folder = cat_path

    return best_folder, best_score


# ── KMeans clustering for unmatched loose files ──

import re
from collections import Counter


def extract_keywords(file_names: List[str], top_n: int = 5) -> List[str]:
    """Extract top-N descriptive keywords from file names.

    Strips extensions, splits on non-alphanumeric boundaries, removes
    short tokens (≤2 chars), and scores by frequency across the group.
    Returns a list of most common descriptive tokens.
    """
    words: List[str] = []
    skip = {"", "a", "an", "the", "in", "on", "at", "to", "for", "of",
             "and", "or", "is", "it", "by", "with", "from", "as", "be"}
    for fname in file_names:
        stem = Path(fname).stem
        tokens = re.split(r"[\s_\-.,;:!?()\[\]{}]+", stem)
        words.extend(t.lower().strip() for t in tokens
                     if len(t) > 2 and t.lower().strip() not in skip)
    if not words:
        return []
    freq = Counter(words)
    return [w for w, _ in freq.most_common(top_n)]


def cluster_files(
    embeddings: List[List[float]],
    min_k: int = 2,
    max_k: int | None = None,
) -> List[dict]:
    """Cluster file embeddings using sklearn KMeans with auto-k selection.

    The optimal k is chosen by silhouette score (2 .. max_k).
    Returns list of Cluster dicts:
      [{"indices": [0, 1, ...],    # indices into the input list
        "centroid": [0.1, ...],    # cluster centroid vector
        "keywords": ["Q3", "budget"],  # extracted from names (names not passed here)
        "size": 3}, ...]

    Returns [] if < 2 vectors or silhouette fails.
    """
    if len(embeddings) < min_k:
        return []

    from sklearn.cluster import KMeans
    from sklearn.metrics import silhouette_score

    X = np.array(embeddings, dtype=np.float64)
    n = len(X)
    upper_k = max_k or min(n // 2, 15)
    upper_k = max(min_k, min(upper_k, n - 1))

    best_k = min_k
    best_score = -1.0
    best_model = None

    for k in range(min_k, upper_k + 1):
        model = KMeans(n_clusters=k, random_state=42, n_init="auto")
        labels = model.fit_predict(X)
        if len(set(labels)) < 2:
            continue
        score = silhouette_score(X, labels)
        if score > best_score:
            best_score = score
            best_k = k
            best_model = model

    if best_model is None:
        # Fallback: k=2
        best_k = min(2, n)
        best_model = KMeans(n_clusters=best_k, random_state=42, n_init="auto")
        best_model.fit(X)

    clusters: List[dict] = []
    for i in range(best_k):
        indices = [j for j, lbl in enumerate(best_model.labels_) if lbl == i]
        if not indices:
            continue
        cluster_vectors = X[indices]
        c_mean = cluster_vectors.mean(axis=0)
        norm = np.linalg.norm(c_mean)
        centroid_vec = (c_mean / norm).tolist() if norm > 1e-12 else [0.0] * X.shape[1]
        clusters.append({
            "indices": indices,
            "centroid": centroid_vec,
            "size": len(indices),
            "keywords": [],  # caller fills these with file names
        })

    # Sort by size descending
    clusters.sort(key=lambda c: -c["size"])
    return clusters


# ── Generative folder naming for clusters ──

_CLUSTER_NAME_PROMPT = """You are a NAS folder organizer. Given keywords extracted from a group of files,
propose a concise 2-4 word folder name that best represents this group.

Keywords: {keywords}
Number of files: {count}

Return ONLY a short folder name (2-4 words, no quotes, no punctuation)."""


def name_cluster(
    cluster: dict,
    endpoint: str = DEFAULT_ENDPOINT,
    model: str = "qwen2.5-coder:7b",
) -> str:
    """Generate a folder name for a cluster using the LLM.

    Args:
        cluster: dict with 'keywords', 'size' fields
        endpoint: ollama endpoint
        model: model to use (default nomic-embed-text, but qwen2.5-coder:7b gives better names)

    Returns a folder-safe name like "Q1_Financial_Reports", or "Cluster_N" as fallback.
    """
    keywords = cluster.get("keywords", [])
    count = cluster.get("size", 0)

    if not keywords:
        return f"Cluster_{count}_files"

    prompt = _CLUSTER_NAME_PROMPT.format(
        keywords=", ".join(keywords[:10]),
        count=count,
    )

    payload = {"model": model, "prompt": prompt, "stream": False}
    try:
        resp = requests.post(
            f"{endpoint.rstrip('/')}/api/generate",
            json=payload,
            timeout=30,
        )
        resp.raise_for_status()
        data = resp.json()
        raw = data.get("response", "").strip()
    except Exception:
        raw = ""

    # Clean up — remove quotes, punctuation, illegal chars
    if raw:
        raw = raw.strip('"\'`.,!?;: ')
        # Replace spaces with underscores for folder name
        raw = raw.replace(" ", "_")
        # Strip illegal filename characters
        raw = re.sub(r'[/\\:*?"<>|]', "", raw)
        # Truncate to 60 chars max
        raw = raw[:60]
        if raw:
            return raw
    return f"Cluster_{count}_files"