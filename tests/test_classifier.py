import json
import pytest
import requests
from pathlib import Path
from unittest.mock import Mock, patch, PropertyMock

from sorter.classifier import OllamaClient, validate_suggested_name, should_skip_rename
from sorter.scanner import FileEntry


TAXONOMY_YAML = """top_level:
  - Zeno
  - Family
categories:
  Zeno:
    - Documents
    - Downloads
"""

SINGLE_RESULT_VALID = json.dumps([
    {"path": "doc.pdf", "category_path": "Zeno/Documents", "confidence": 92, "reason": "invoice"}
])

BATCH_RESULT_VALID = json.dumps([
    {"path": "doc.pdf", "category_path": "Zeno/Documents", "confidence": 92, "reason": "invoice"},
    {"path": "photo.jpg", "category_path": "Zeno/Bilder", "confidence": 85, "reason": "photo"},
])


def make_entry(rel_path: str, name: str = None, mime: str = "text/plain", size: int = 100) -> FileEntry:
    return FileEntry(
        path=Path(f"/mnt/{rel_path}"),
        rel_path=rel_path,
        name=name or Path(rel_path).name,
        mime=mime,
        size=size,
        mtime=1000.0,
        is_dir=False,
        ctime=1000.0,
        atime=1000.0,
    )


class TestParseResponse:
    def test_valid_json(self):
        client = OllamaClient()
        result = client._parse_response(SINGLE_RESULT_VALID)
        assert len(result) == 1
        assert result[0]["path"] == "doc.pdf"
        assert result[0]["category_path"] == "Zeno/Documents"
        assert result[0]["confidence"] == 92

    def test_markdown_code_block(self):
        client = OllamaClient()
        text = "```json\n" + SINGLE_RESULT_VALID + "\n```"
        result = client._parse_response(text)
        assert len(result) == 1

    def test_markdown_no_lang(self):
        client = OllamaClient()
        text = "```\n" + SINGLE_RESULT_VALID + "\n```"
        result = client._parse_response(text)
        assert len(result) == 1

    def test_trailing_comma(self):
        client = OllamaClient()
        text = '[{"path": "x", "category_path": "Zeno/Docs", "confidence": 50, "reason": "test",}]'
        result = client._parse_response(text)
        assert len(result) == 1

    def test_malformed_json_raises(self):
        client = OllamaClient()
        with pytest.raises(json.JSONDecodeError):
            client._parse_response("this is not json")

    def test_batch_parsed(self):
        client = OllamaClient()
        result = client._parse_response(BATCH_RESULT_VALID)
        assert len(result) == 2

    def test_missing_fields_defaulted(self):
        client = OllamaClient()
        text = '[{"path": "x"}]'
        result = client._parse_response(text)
        assert result[0]["category_path"] == "_Unsorted_Review"
        assert result[0]["confidence"] == 0


class TestBuildBatchPrompt:
    def test_contains_taxonomy(self):
        client = OllamaClient()
        entries = [(make_entry("doc.pdf"), {"text": "hello", "metadata": {}})]
        prompt = client._build_batch_prompt(entries, TAXONOMY_YAML)
        assert TAXONOMY_YAML in prompt
        assert "EXISTING FOLDER TAXONOMY" in prompt

    def test_contains_file_info(self):
        client = OllamaClient()
        entries = [(make_entry("doc.pdf", mime="application/pdf"), {"text": "invoice content", "metadata": {"pages": 3}})]
        prompt = client._build_batch_prompt(entries, TAXONOMY_YAML)
        assert "Path: doc.pdf" in prompt
        assert "invoice content" in prompt

    def test_multiple_files(self):
        client = OllamaClient()
        entries = [
            (make_entry("a.txt"), {"text": "aaa", "metadata": {}}),
            (make_entry("b.txt"), {"text": "bbb", "metadata": {}}),
        ]
        prompt = client._build_batch_prompt(entries, TAXONOMY_YAML)
        assert "Path: a.txt" in prompt
        assert "Path: b.txt" in prompt
        assert "---" in prompt

    def test_prompt_contains_context_and_age(self):
        """The prompt must include folder context, context hint, and age info."""
        client = OllamaClient()
        entries = [(make_entry("Hypnose/hypnose_data/e00/file.au"),
                     {"text": "", "metadata": {}})]
        prompt = client._build_batch_prompt(entries, TAXONOMY_YAML)
        assert "Folder context:" in prompt
        assert "Context hint:" in prompt
        assert "Ages:" in prompt
        assert "Age classification:" in prompt
        # The prompt correctly mentions Audacity rules (not blanket .au delete)
        assert "AUDACITY" in prompt or "Audacity" in prompt


class TestClassifyBatch:
    def test_connection_error_raised(self):
        client = OllamaClient(endpoint="http://localhost:1", timeout=1)
        files = [make_entry("doc.pdf")]
        with pytest.raises(ConnectionError):
            client.classify_batch(files, TAXONOMY_YAML)

    @patch("sorter.classifier.requests.post")
    def test_successful_batch(self, mock_post):
        mock_resp = Mock()
        mock_resp.json.return_value = {"response": BATCH_RESULT_VALID}
        mock_resp.raise_for_status.return_value = None
        mock_post.return_value = mock_resp

        client = OllamaClient()
        files = [
            make_entry("doc.pdf", mime="application/pdf"),
            make_entry("photo.jpg", mime="image/jpeg"),
        ]
        results = client.classify_batch(files, TAXONOMY_YAML)
        assert len(results) == 2
        assert results[0]["category_path"] == "Zeno/Documents"
        assert results[1]["category_path"] == "Zeno/Bilder"

    @patch("sorter.classifier.requests.post")
    def test_missing_result_fallback(self, mock_post):
        mock_resp = Mock()
        mock_resp.json.return_value = {"response": json.dumps([{"path": "doc.pdf", "category_path": "Zeno/Documents", "confidence": 90, "reason": ""}])}
        mock_resp.raise_for_status.return_value = None
        mock_post.return_value = mock_resp

        client = OllamaClient()
        files = [
            make_entry("doc.pdf"),
            make_entry("missing.txt"),
        ]
        results = client.classify_batch(files, TAXONOMY_YAML)
        assert len(results) == 2
        assert results[0]["category_path"] == "Zeno/Documents"
        assert results[1]["category_path"] == "_Unsorted_Review"
        assert results[1]["confidence"] == 0

    @patch("sorter.classifier.requests.post")
    def test_malformed_json_triggers_fallback(self, mock_post):
        mock_resp = Mock()
        mock_resp.json.return_value = {"response": "not json at all"}
        mock_resp.raise_for_status.return_value = None
        mock_post.side_effect = [
            mock_resp,  # first call fails parse (primary model)
            mock_resp,  # fallback also fails
        ]

        mock_resp2 = Mock()
        mock_resp2.json.return_value = {"response": "not json at all"}
        mock_resp2.raise_for_status.return_value = None

        client = OllamaClient(model="primary", fallback_model="fallback")
        files = [make_entry("doc.pdf")]

        with patch.object(client, '_call_ollama', return_value="not json at all"):
            with patch.object(client, '_call_ollama_fallback', return_value="not json at all"):
                results = client.classify_batch(files, TAXONOMY_YAML)

        assert len(results) == 1
        assert results[0]["category_path"] == "_Unsorted_Review"
        assert results[0]["confidence"] == 0

    @patch("sorter.classifier.requests.post")
    def test_timeout_triggers_fallback(self, mock_post):
        mock_post.side_effect = requests.exceptions.Timeout("timeout")

        client = OllamaClient(model="primary", fallback_model="fallback")
        files = [make_entry("doc.pdf")]

        with patch.object(client, '_call_ollama_fallback', return_value=BATCH_RESULT_VALID):
            results = client.classify_batch(files, TAXONOMY_YAML)

        assert len(results) == 1
        assert results[0]["category_path"] == "Zeno/Documents"

    def test_classify_single_fallback(self):
        client = OllamaClient(endpoint="http://localhost:1", timeout=1)
        entry = make_entry("doc.pdf")
        result = client.classify_single(entry, {"text": "", "metadata": {}}, TAXONOMY_YAML)
        assert result["category_path"] == "_Unsorted_Review"
        assert result["confidence"] == 0


class TestFromConfig:
    def test_from_config(self, tmp_path):
        cfg = tmp_path / "config.yaml"
        cfg.write_text("""ollama:
  endpoint: "http://custom:11434"
  model: "custom-model"
  fallback_model: "fb-model"
  timeout: 30
  batch_size: 10
""")
        client = OllamaClient.from_config(cfg)
        assert client.endpoint == "http://custom:11434"
        assert client.model == "custom-model"
        assert client.fallback_model == "fb-model"
        assert client.timeout == 30
        assert client.batch_size == 10

    def test_retry_attempts_dead_config_removed(self):
        """tech-debt retry-attempts-not-wired.

        retry_attempts was a constructor default that no retry path ever read and
        from_config never passed. Removed — pin it so it cannot creep back unwired.
        """
        import inspect

        params = inspect.signature(OllamaClient.__init__).parameters
        assert "retry_attempts" not in params
        assert not hasattr(OllamaClient(), "retry_attempts")


class TestValidateSuggestedName:
    def test_none_input(self):
        assert validate_suggested_name(None, "test.docx") is None

    def test_empty_input(self):
        assert validate_suggested_name("", "test.docx") is None

    def test_valid_rename(self):
        result = validate_suggested_name("Büro Minimalbetrieb Kosten.docx", "Kostentabelle.docx")
        assert result is not None
        assert result.endswith(".docx")
        assert "Kosten" in result

    def test_extension_preservation(self):
        """If LLM drops/changes extension, original is re-attached."""
        result = validate_suggested_name("Invoice for services.pdf", "scan.pdf")
        assert result is not None
        assert result.endswith(".pdf")

    def test_unchanged_name_returns_none(self):
        """Same name + ext → no rename needed."""
        result = validate_suggested_name("report.docx", "report.docx")
        assert result is None

    def test_illegal_chars_stripped(self):
        name = "my/file:name*.txt"
        result = validate_suggested_name(name, "bad.txt")
        assert result is not None
        assert "/" not in result
        assert "*" not in result

    def test_too_long_returns_none(self):
        long_name = "a" * 81 + ".txt"
        result = validate_suggested_name(long_name, "short.txt")
        assert result is None

    def test_collapse_whitespace(self):
        result = validate_suggested_name("  My   Cool   File.txt", "ugly.txt")
        assert result == "My Cool File.txt"
        assert "  " not in result

    def test_empty_after_sanitization_returns_none(self):
        result = validate_suggested_name("_.txt", "blah.txt")
        assert result is None or result == "_.txt"


class TestShouldSkipRename:
    def test_image_mime(self):
        assert should_skip_rename("image/jpeg") is True
        assert should_skip_rename("image/png") is True

    def test_video_mime(self):
        assert should_skip_rename("video/mp4") is True
        assert should_skip_rename("video/x-matroska") is True

    def test_audio_mime(self):
        assert should_skip_rename("audio/mpeg") is True
        assert should_skip_rename("audio/wav") is True

    def test_text_mime(self):
        assert should_skip_rename("text/plain") is False

    def test_pdf_mime(self):
        assert should_skip_rename("application/pdf") is False

    def test_none_mime(self):
        assert should_skip_rename(None) is False


class TestSuggestedNameInParseResponse:
    def test_suggested_name_preserved(self):
        client = OllamaClient()
        text = json.dumps([
            {"path": "Kostentabelle.docx", "category_path": "Zeno/Documents",
             "confidence": 90, "reason": "cost overview",
             "suggested_name": "Büro Minimalbetrieb Kosten.docx"}
        ])
        results = client._parse_response(text)
        assert results[0]["suggested_name"] == "Büro Minimalbetrieb Kosten.docx"

    def test_no_suggested_name(self):
        client = OllamaClient()
        text = json.dumps([
            {"path": "report.docx", "category_path": "Zeno/Documents",
             "confidence": 90, "reason": "report"}
        ])
        results = client._parse_response(text)
        assert results[0].get("suggested_name") is None

# ── design-routing-fix: file prompt rules ──

from sorter.classifier import PROMPT_TEMPLATE


class TestFilePromptRules:
    def test_subject_first_and_design_rules(self):
        assert "SUBJECT first" in PROMPT_TEMPLATE
        assert "design assets" in PROMPT_TEMPLATE
        assert "Scanned certificates" in PROMPT_TEMPLATE

    def test_design_lives_under_documents(self):
        assert "never a top-level Zeno/Design" in PROMPT_TEMPLATE

    def test_prtl_is_design_not_video(self):
        assert ".prtl" in PROMPT_TEMPLATE
        assert "NOT video" in PROMPT_TEMPLATE
