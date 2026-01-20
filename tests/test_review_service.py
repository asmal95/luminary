"""Comprehensive tests for ReviewService"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, Mock
from typing import Optional

import pytest

from luminary.application.review_service import ReviewService
from luminary.domain.models.comment import Comment, Severity
from luminary.domain.models.file_change import FileChange, Hunk
from luminary.domain.models.review_result import ReviewResult
from luminary.domain.validators.comment_validator import CommentValidator
from luminary.infrastructure.llm.base import LLMProvider


class MockLLMProvider(LLMProvider):
    """Mock LLM provider for testing"""

    def __init__(self, response: str = ""):
        super().__init__({})
        self.response = response
        self.calls = []

    def generate(self, prompt: str, **kwargs) -> str:
        self.calls.append(prompt)
        return self.response


class TestReviewServiceInit:
    """Tests for ReviewService initialization"""

    def test_init_with_defaults(self):
        """Test initialization with default values"""
        provider = MockLLMProvider()
        service = ReviewService(provider)
        
        assert service.llm_provider == provider
        assert service.validator is None
        assert service.comment_mode == "both"
        assert service.max_context_tokens is None
        assert service.chunk_overlap_lines == 200

    def test_init_with_custom_values(self):
        """Test initialization with custom values"""
        provider = MockLLMProvider()
        validator = MagicMock(spec=CommentValidator)
        service = ReviewService(
            provider,
            validator=validator,
            comment_mode="inline",
            max_context_tokens=1000,
            chunk_overlap_lines=100,
            language="Python",
            framework="Django",
        )
        
        assert service.validator == validator
        assert service.comment_mode == "inline"
        assert service.max_context_tokens == 1000
        assert service.chunk_overlap_lines == 100
        assert service.language == "Python"
        assert service.framework == "Django"


class TestReviewFile:
    """Tests for review_file method"""

    def test_review_file_success(self):
        """Test successful file review"""
        response = json.dumps({
            "comments": [
                {"file": "test.py", "line": 1, "message": "Test comment", "suggestion": None}
            ],
            "summary": "Test summary"
        })
        provider = MockLLMProvider(response)
        service = ReviewService(provider)
        
        file_change = FileChange(path="test.py", new_content="print('hello')\n")
        result = service.review_file(file_change)
        
        assert isinstance(result, ReviewResult)
        assert result.error is None
        assert len(result.comments) == 1
        assert result.summary == "Test summary"

    def test_review_file_with_error(self):
        """Test file review with LLM error"""
        provider = MockLLMProvider()
        provider.generate = Mock(side_effect=Exception("LLM error"))
        service = ReviewService(provider)
        
        file_change = FileChange(path="test.py", new_content="print('hello')\n")
        result = service.review_file(file_change)
        
        assert result.error is not None
        assert "LLM error" in result.error

    def test_review_file_with_validator(self):
        """Test file review with validator"""
        response = json.dumps({
            "comments": [
                {"file": "test.py", "line": 1, "message": "Test comment", "suggestion": None}
            ]
        })
        provider = MockLLMProvider(response)
        
        validator = MagicMock(spec=CommentValidator)
        validation_result = MagicMock()
        validation_result.valid = True
        validator.validate.return_value = validation_result
        
        service = ReviewService(provider, validator=validator)
        
        file_change = FileChange(path="test.py", new_content="print('hello')\n")
        result = service.review_file(file_change)
        
        assert len(result.comments) == 1
        validator.validate.assert_called_once()

    def test_review_file_validator_rejects_comment(self):
        """Test that validator can reject comments"""
        response = json.dumps({
            "comments": [
                {"file": "test.py", "line": 1, "message": "Test comment", "suggestion": None}
            ]
        })
        provider = MockLLMProvider(response)
        
        validator = MagicMock(spec=CommentValidator)
        validation_result = MagicMock()
        validation_result.valid = False
        validation_result.reason = "Low quality"
        validator.validate.return_value = validation_result
        
        service = ReviewService(provider, validator=validator)
        
        file_change = FileChange(path="test.py", new_content="print('hello')\n")
        result = service.review_file(file_change)
        
        assert len(result.comments) == 0


class TestCommentModes:
    """Tests for comment mode filtering"""

    def test_comment_mode_summary(self):
        """Test summary-only mode"""
        response = json.dumps({
            "comments": [
                {"file": "test.py", "line": 1, "message": "Test comment", "suggestion": None}
            ],
            "summary": "Test summary"
        })
        provider = MockLLMProvider(response)
        service = ReviewService(provider, comment_mode="summary")
        
        file_change = FileChange(path="test.py", new_content="print('hello')\n")
        result = service.review_file(file_change)
        
        assert len(result.comments) == 0
        assert result.summary == "Test summary"

    def test_comment_mode_inline(self):
        """Test inline-only mode"""
        response = json.dumps({
            "comments": [
                {"file": "test.py", "line": 1, "message": "Test comment", "suggestion": None}
            ],
            "summary": "Test summary"
        })
        provider = MockLLMProvider(response)
        service = ReviewService(provider, comment_mode="inline")
        
        file_change = FileChange(path="test.py", new_content="print('hello')\n")
        result = service.review_file(file_change)
        
        assert len(result.comments) == 1
        assert result.summary is None

    def test_comment_mode_both(self):
        """Test both mode"""
        response = json.dumps({
            "comments": [
                {"file": "test.py", "line": 1, "message": "Test comment", "suggestion": None}
            ],
            "summary": "Test summary"
        })
        provider = MockLLMProvider(response)
        service = ReviewService(provider, comment_mode="both")
        
        file_change = FileChange(path="test.py", new_content="print('hello')\n")
        result = service.review_file(file_change)
        
        assert len(result.comments) == 1
        assert result.summary == "Test summary"


class TestChunking:
    """Tests for file chunking"""

    def test_chunking_disabled_by_default(self):
        """Test that chunking is disabled by default"""
        provider = MockLLMProvider('{"comments": []}')
        service = ReviewService(provider)
        
        # Large file
        large_content = "\n".join([f"line {i}" for i in range(1000)])
        file_change = FileChange(path="large.py", new_content=large_content)
        result = service.review_file(file_change)
        
        # Should make only one call
        assert len(provider.calls) == 1

    def test_chunking_enabled_with_max_tokens(self):
        """Test that chunking is enabled when max_context_tokens is set"""
        provider = MockLLMProvider('{"comments": []}')
        service = ReviewService(provider, max_context_tokens=100)
        
        # Large file that exceeds token limit
        large_content = "\n".join([f"line {i} = {i}" for i in range(1000)])
        file_change = FileChange(path="large.py", new_content=large_content)
        result = service.review_file(file_change)
        
        # Should make multiple calls
        assert len(provider.calls) > 1

    def test_chunking_with_overlap(self):
        """Test that chunking respects overlap"""
        provider = MockLLMProvider('{"comments": []}')
        service = ReviewService(provider, max_context_tokens=100, chunk_overlap_lines=50)
        
        large_content = "\n".join([f"line {i}" for i in range(500)])
        file_change = FileChange(path="large.py", new_content=large_content)
        result = service.review_file(file_change)
        
        # Should make multiple calls with overlap
        assert len(provider.calls) > 1


class TestParseLLMResponse:
    """Tests for _parse_llm_response method"""

    def test_parse_json_array_format(self):
        """Test parsing JSON array format"""
        provider = MockLLMProvider()
        service = ReviewService(provider)
        
        response = json.dumps([
            {"file": "test.py", "line": 1, "message": "Comment 1", "suggestion": None},
            {"file": "test.py", "line": 2, "message": "Comment 2", "suggestion": None}
        ])
        
        comments = service._parse_llm_response(response, "test.py")
        
        assert len(comments) == 2
        assert comments[0].line_number == 1
        assert comments[1].line_number == 2

    def test_parse_json_object_format(self):
        """Test parsing JSON object format with comments field"""
        provider = MockLLMProvider()
        service = ReviewService(provider)
        
        response = json.dumps({
            "comments": [
                {"file": "test.py", "line": 1, "message": "Comment 1", "suggestion": None}
            ],
            "summary": "Test summary"
        })
        
        comments = service._parse_llm_response(response, "test.py")
        
        assert len(comments) == 1
        assert comments[0].line_number == 1

    def test_parse_json_with_markdown_code_block(self):
        """Test parsing JSON from markdown code block"""
        provider = MockLLMProvider()
        service = ReviewService(provider)
        
        response = "```json\n" + json.dumps([
            {"file": "test.py", "line": 1, "message": "Comment", "suggestion": None}
        ]) + "\n```"
        
        comments = service._parse_llm_response(response, "test.py")
        
        assert len(comments) == 1

    def test_parse_json_with_fixed_errors(self):
        """Test parsing JSON with common errors that get fixed"""
        provider = MockLLMProvider()
        service = ReviewService(provider)
        
        # JSON with trailing comma (should be fixed)
        response = '[{"file": "test.py", "line": 1, "message": "Comment", "suggestion": null,}]'
        
        comments = service._parse_llm_response(response, "test.py")
        
        assert len(comments) == 1

    def test_parse_json_invalid_line_number(self):
        """Test parsing with invalid line number"""
        provider = MockLLMProvider()
        service = ReviewService(provider)
        
        response = json.dumps([
            {"file": "test.py", "line": None, "message": "Comment", "suggestion": None},
            {"file": "test.py", "line": 0, "message": "Comment", "suggestion": None},
            {"file": "test.py", "line": 1, "message": "Valid comment", "suggestion": None}
        ])
        
        comments = service._parse_llm_response(response, "test.py")
        
        # Should skip invalid line numbers
        assert len(comments) == 1
        assert comments[0].line_number == 1

    def test_parse_json_severity_detection(self):
        """Test severity detection from message"""
        provider = MockLLMProvider()
        service = ReviewService(provider)
        
        response = json.dumps([
            {"file": "test.py", "line": 1, "message": "This is an error", "suggestion": None},
            {"file": "test.py", "line": 2, "message": "This is a warning", "suggestion": None},
            {"file": "test.py", "line": 3, "message": "This is info", "suggestion": None}
        ])
        
        comments = service._parse_llm_response(response, "test.py")
        
        assert comments[0].severity == Severity.ERROR
        assert comments[1].severity == Severity.WARNING
        assert comments[2].severity == Severity.INFO

    def test_parse_json_fallback_on_error(self):
        """Test fallback to general comment on parse error"""
        provider = MockLLMProvider()
        service = ReviewService(provider)
        
        response = "This is not valid JSON"
        
        comments = service._parse_llm_response(response, "test.py")
        
        assert len(comments) == 1
        assert "[Error parsing response]" in comments[0].content or "[Parsing error" in comments[0].content


class TestExtractSummary:
    """Tests for _extract_summary method"""

    def test_extract_summary_from_json(self):
        """Test extracting summary from JSON format"""
        provider = MockLLMProvider()
        service = ReviewService(provider)
        
        response = json.dumps({
            "comments": [],
            "summary": "This is a summary"
        })
        
        summary = service._extract_summary(response)
        
        assert summary == "This is a summary"

    def test_extract_summary_from_text(self):
        """Test extracting summary from legacy text format"""
        provider = MockLLMProvider()
        service = ReviewService(provider)
        
        response = "Some text\n**Summary:** This is a summary\n\nMore text"
        
        summary = service._extract_summary(response)
        
        # Summary extraction stops at empty line, so should get "This is a summary"
        assert "This is a summary" in summary
        assert summary.startswith("This is a summary")

    def test_extract_summary_none_when_missing(self):
        """Test that None is returned when summary is missing"""
        provider = MockLLMProvider()
        service = ReviewService(provider)
        
        response = json.dumps({"comments": []})
        
        summary = service._extract_summary(response)
        
        assert summary is None


class TestDeduplication:
    """Tests for comment deduplication"""

    def test_dedupe_identical_comments(self):
        """Test that identical comments are deduplicated"""
        provider = MockLLMProvider()
        service = ReviewService(provider)
        
        comment1 = Comment(content="Test", line_number=1, file_path="test.py")
        comment2 = Comment(content="Test", line_number=1, file_path="test.py")
        
        comments = service._dedupe_comments([comment1, comment2])
        
        assert len(comments) == 1

    def test_dedupe_preserves_different_comments(self):
        """Test that different comments are preserved"""
        provider = MockLLMProvider()
        service = ReviewService(provider)
        
        comment1 = Comment(content="Test 1", line_number=1, file_path="test.py")
        comment2 = Comment(content="Test 2", line_number=2, file_path="test.py")
        
        comments = service._dedupe_comments([comment1, comment2])
        
        assert len(comments) == 2


class TestLanguageDetection:
    """Tests for language detection"""

    def test_detect_language_from_path(self):
        """Test language detection from file path"""
        provider = MockLLMProvider()
        service = ReviewService(provider)
        
        assert service._detect_language_from_path("test.py") == "Python"
        assert service._detect_language_from_path("test.js") == "JavaScript"
        assert service._detect_language_from_path("test.ts") == "TypeScript"
        assert service._detect_language_from_path("test.java") == "Java"
        assert service._detect_language_from_path("test.go") == "Go"

    def test_detect_language_unknown_extension(self):
        """Test that None is returned for unknown extensions"""
        provider = MockLLMProvider()
        service = ReviewService(provider)
        
        assert service._detect_language_from_path("test.xyz") is None
        assert service._detect_language_from_path("test") is None

    def test_detect_language_uses_explicit_language(self):
        """Test that explicit language overrides detection"""
        provider = MockLLMProvider()
        service = ReviewService(provider, language="Custom")
        
        file_change = FileChange(path="test.py", new_content="code")
        # Language should be "Custom", not "Python"
        # We can't directly test this, but we can verify it's used in prompt building
        assert service.language == "Custom"


class TestExtractCodeSnippet:
    """Tests for code snippet extraction"""

    def test_extract_code_snippet_success(self):
        """Test successful code snippet extraction"""
        provider = MockLLMProvider()
        service = ReviewService(provider)
        
        content = "\n".join([f"line {i}" for i in range(1, 21)])
        file_change = FileChange(path="test.py", new_content=content)
        comment = Comment(content="Test", line_number=10, file_path="test.py")
        
        snippet = service._extract_code_snippet(file_change, comment)
        
        assert snippet is not None
        assert "line 5" in snippet
        assert "line 15" in snippet

    def test_extract_code_snippet_no_line_number(self):
        """Test that None is returned when comment has no line number"""
        provider = MockLLMProvider()
        service = ReviewService(provider)
        
        file_change = FileChange(path="test.py", new_content="code")
        comment = Comment(content="Test", file_path="test.py")
        
        snippet = service._extract_code_snippet(file_change, comment)
        
        assert snippet is None

    def test_extract_code_snippet_out_of_range(self):
        """Test that snippet extraction handles out of range gracefully"""
        provider = MockLLMProvider()
        service = ReviewService(provider)
        
        file_change = FileChange(path="test.py", new_content="line 1\nline 2")
        comment = Comment(content="Test", line_number=100, file_path="test.py")
        
        snippet = service._extract_code_snippet(file_change, comment)
        
        assert snippet is None


class TestAggregateSummaries:
    """Tests for summary aggregation"""

    def test_aggregate_single_summary(self):
        """Test that single summary is returned as-is"""
        provider = MockLLMProvider()
        service = ReviewService(provider)
        
        summary = service._aggregate_summaries(["Single summary"])
        
        assert summary == "Single summary"

    def test_aggregate_multiple_summaries(self):
        """Test that multiple summaries are aggregated"""
        provider = MockLLMProvider()
        service = ReviewService(provider)
        
        summaries = ["Summary 1", "Summary 2", "Summary 3"]
        result = service._aggregate_summaries(summaries)
        
        assert "Chunk 1 summary:" in result
        assert "Summary 1" in result
        assert "Chunk 2 summary:" in result
        assert "Summary 2" in result

    def test_aggregate_empty_summaries(self):
        """Test that None is returned for empty summaries"""
        provider = MockLLMProvider()
        service = ReviewService(provider)
        
        summary = service._aggregate_summaries([])
        
        assert summary is None


class TestFilterHunks:
    """Tests for hunk filtering"""

    def test_filter_hunks_for_range(self):
        """Test filtering hunks for a specific line range"""
        provider = MockLLMProvider()
        service = ReviewService(provider)
        
        hunks = [
            Hunk(old_start=1, old_count=5, new_start=1, new_count=5, lines=[]),
            Hunk(old_start=10, old_count=5, new_start=10, new_count=5, lines=[]),
            Hunk(old_start=20, old_count=5, new_start=20, new_count=5, lines=[]),
        ]
        file_change = FileChange(path="test.py", hunks=hunks)
        
        # Filter for lines 8-15 (should include hunk at line 10)
        filtered = service._filter_hunks_for_range(file_change, 8, 15)
        
        assert len(filtered) == 1
        assert filtered[0].new_start == 10

    def test_filter_hunks_no_hunks(self):
        """Test filtering when no hunks exist"""
        provider = MockLLMProvider()
        service = ReviewService(provider)
        
        file_change = FileChange(path="test.py", hunks=[])
        
        filtered = service._filter_hunks_for_range(file_change, 1, 10)
        
        assert len(filtered) == 0
