"""
Tests for the Typesense search engine.
"""
from unittest.mock import MagicMock, patch

import django.test
import httpx
import pytest
from django.test import override_settings
from typesense.exceptions import RequestMalformed

from search.typesense import (
    TypesenseEngine,
    _MAX_IDS_PER_DELETE_BATCH,
    _strip_html,
    _strip_html_from_content,
    get_search_params,
)

TYPESENSE_SETTINGS = {
    "TYPESENSE_API_KEY": "test-api-key",
    "TYPESENSE_URLS": [{"host": "localhost", "port": "8108", "protocol": "http"}],
    "TYPESENSE_COLLECTION_PREFIX": "test_",
}


# A minimal fake Typesense search response (as returned by the Python client).
def _make_typesense_response(hits=None, found=None):
    hits = hits or []
    return {
        "hits": hits,
        "facet_counts": [],
        "found": found if found is not None else len(hits),
        "out_of": len(hits),
        "page": 1,
        "request_params": {"collection_name": "test_collection", "per_page": 10, "q": ""},
        "search_cutoff": False,
        "search_time_ms": 1,
    }


@override_settings(**TYPESENSE_SETTINGS)
class GetSearchParamsTests(django.test.TestCase):
    """Unit tests for get_search_params()."""

    def test_none_exclude_dictionary_does_not_raise(self):
        """Passing exclude_dictionary=None should not raise AttributeError."""
        params = get_search_params(exclude_dictionary=None)
        assert "filter_by" not in params

    def test_empty_exclude_dictionary_produces_no_filter(self):
        params = get_search_params(exclude_dictionary={})
        assert "filter_by" not in params

    def test_exclude_dictionary_single_field(self):
        params = get_search_params(exclude_dictionary={"status": ["deleted"]})
        assert params["filter_by"] == "status:![`deleted`]"

    def test_exclude_dictionary_multiple_ids(self):
        params = get_search_params(exclude_dictionary={"id": ["id1", "id2", "id3"]})
        assert params["filter_by"] == "id:![`id1`, `id2`, `id3`]"

    def test_field_dictionary_and_exclude_dictionary_combined(self):
        params = get_search_params(
            field_dictionary={"course": "course-v1:org+x+run"},
            exclude_dictionary={"id": ["stale-id"]},
        )
        assert "course:=`course-v1:org+x+run`" in params["filter_by"]
        assert "id:![`stale-id`]" in params["filter_by"]

    def test_pagination_params(self):
        params = get_search_params(from_=50, size=25)
        assert params["offset"] == 50
        assert params["limit"] == 25


@override_settings(**TYPESENSE_SETTINGS)
class TypesenseEngineSearchTests(django.test.TestCase):
    """Tests for TypesenseEngine.search() fallback behaviour."""

    def _make_engine(self, mock_index):
        """Return a TypesenseEngine whose typesense_index is the given mock."""
        engine = TypesenseEngine.__new__(TypesenseEngine)
        engine.index_name = "courseware_content"
        engine._typesense_index = mock_index  # pylint: disable=protected-access
        return engine

    def test_search_success(self):
        """Normal search path returns processed results."""
        mock_index = MagicMock()
        mock_index.documents.search.return_value = _make_typesense_response(
            hits=[{"document": {"id": "block-1"}, "highlights": [], "text_match": 100}],
            found=1,
        )
        engine = self._make_engine(mock_index)
        result = engine.search(field_dictionary={"course": "course-v1:org+x+run"})
        assert result["total"] == 1
        assert result["results"][0]["data"]["id"] == "block-1"

    def test_search_falls_back_on_httpx_invalid_url(self):
        """
        When the Typesense client raises httpx.InvalidURL ('query too long'),
        search() must fall back to the multi-search POST endpoint.
        """
        mock_index = MagicMock()
        mock_index.documents.search.side_effect = httpx.InvalidURL(
            "URL component 'query' too long"
        )

        expected_response = _make_typesense_response(found=0)
        mock_client = MagicMock()
        mock_client.multi_search.perform.return_value = {"results": [expected_response]}

        engine = self._make_engine(mock_index)
        engine._typesense_index = mock_index  # pylint: disable=protected-access

        with patch("search.typesense.get_typesense_client", return_value=mock_client):
            result = engine.search(field_dictionary={"course": "course-v1:org+x+run"})

        assert mock_client.multi_search.perform.called
        assert result["total"] == 0

    def test_search_falls_back_on_request_malformed_too_long(self):
        """
        Existing behaviour: RequestMalformed('Query string exceeds max allowed length')
        also routes to multi-search.
        """
        mock_index = MagicMock()
        mock_index.documents.search.side_effect = RequestMalformed(
            "Query string exceeds max allowed length of 4096"
        )

        expected_response = _make_typesense_response(found=0)
        mock_client = MagicMock()
        mock_client.multi_search.perform.return_value = {"results": [expected_response]}

        engine = self._make_engine(mock_index)

        with patch("search.typesense.get_typesense_client", return_value=mock_client):
            result = engine.search(field_dictionary={"course": "course-v1:org+x+run"})

        assert mock_client.multi_search.perform.called
        assert result["total"] == 0

    def test_search_does_not_swallow_unrelated_httpx_invalid_url(self):
        """
        An httpx.InvalidURL that is NOT about length should be re-raised, not
        silently swallowed.
        """
        mock_index = MagicMock()
        mock_index.documents.search.side_effect = httpx.InvalidURL(
            "URL component 'host' is invalid"
        )

        engine = self._make_engine(mock_index)
        with pytest.raises(httpx.InvalidURL):
            engine.search(field_dictionary={"course": "course-v1:org+x+run"})

    def test_search_does_not_swallow_unrelated_request_malformed(self):
        """
        A RequestMalformed that is not a length error should be re-raised.
        """
        mock_index = MagicMock()
        mock_index.documents.search.side_effect = RequestMalformed(
            "Some other malformed request error"
        )

        engine = self._make_engine(mock_index)
        with pytest.raises(RequestMalformed):
            engine.search(field_dictionary={"course": "course-v1:org+x+run"})


@override_settings(**TYPESENSE_SETTINGS)
class TypesenseEngineRemoveTests(django.test.TestCase):
    """Tests for TypesenseEngine.remove() chunking."""

    def _make_engine(self, mock_index):
        engine = TypesenseEngine.__new__(TypesenseEngine)
        engine.index_name = "courseware_content"
        engine._typesense_index = mock_index  # pylint: disable=protected-access
        return engine

    def test_remove_empty_list_does_nothing(self):
        mock_index = MagicMock()
        engine = self._make_engine(mock_index)
        engine.remove([])
        mock_index.documents.delete.assert_not_called()

    def test_remove_small_batch_single_request(self):
        """IDs within the batch limit are deleted in one request."""
        mock_index = MagicMock()
        engine = self._make_engine(mock_index)
        ids = [f"block-{i}" for i in range(10)]
        engine.remove(ids)
        assert mock_index.documents.delete.call_count == 1

    def test_remove_large_batch_chunks_requests(self):
        """
        More IDs than _MAX_IDS_PER_DELETE_BATCH must be split across multiple
        delete requests so no single request has a URL query string that is too long.
        """
        mock_index = MagicMock()
        engine = self._make_engine(mock_index)
        total_ids = _MAX_IDS_PER_DELETE_BATCH * 3 + 1  # forces 4 batches
        ids = [f"block-{i}" for i in range(total_ids)]
        engine.remove(ids)
        assert mock_index.documents.delete.call_count == 4

    def test_remove_exact_batch_size(self):
        """Exactly _MAX_IDS_PER_DELETE_BATCH IDs fit in one request."""
        mock_index = MagicMock()
        engine = self._make_engine(mock_index)
        ids = [f"block-{i}" for i in range(_MAX_IDS_PER_DELETE_BATCH)]
        engine.remove(ids)
        assert mock_index.documents.delete.call_count == 1

    def test_remove_each_batch_contains_correct_ids(self):
        """Each batch's filter_by contains only its own slice of IDs."""
        mock_index = MagicMock()
        engine = self._make_engine(mock_index)
        ids = [f"id-{i}" for i in range(_MAX_IDS_PER_DELETE_BATCH + 2)]
        engine.remove(ids)

        calls = mock_index.documents.delete.call_args_list
        assert len(calls) == 2

        first_filter = calls[0][0][0]["filter_by"]
        second_filter = calls[1][0][0]["filter_by"]

        # First batch should contain exactly _MAX_IDS_PER_DELETE_BATCH entries
        assert first_filter.count("`id-") == _MAX_IDS_PER_DELETE_BATCH
        # Second batch should contain the remaining 2
        assert second_filter.count("`id-") == 2


class StripHtmlTests(django.test.TestCase):
    """Tests for the _strip_html and _strip_html_from_content helpers."""

    def test_strip_html_removes_tags(self):
        assert _strip_html("<p>Hello <strong>world</strong></p>") == "Hello world"

    def test_strip_html_unescapes_entities(self):
        assert _strip_html("&lt;b&gt;bold&lt;/b&gt; &amp; more") == "<b>bold</b> & more"

    def test_strip_html_normalises_whitespace(self):
        assert _strip_html("  foo   <br/>  bar  ") == "foo bar"

    def test_strip_html_plain_text_unchanged(self):
        text = "no html here"
        assert _strip_html(text) == text

    def test_strip_html_empty_string(self):
        assert _strip_html("") == ""

    def test_strip_html_from_content_dict(self):
        content = {
            "display_name": "<b>My Problem</b>",
            "body": "<p>Solve for <em>x</em> where x &gt; 0.</p>",
        }
        result = _strip_html_from_content(content)
        assert result == {
            "display_name": "My Problem",
            "body": "Solve for x where x > 0.",
        }

    def test_strip_html_from_content_nested(self):
        content = {"section": {"title": "<h2>Unit 1</h2>", "items": ["<li>a</li>", "<li>b</li>"]}}
        result = _strip_html_from_content(content)
        assert result == {"section": {"title": "Unit 1", "items": ["a", "b"]}}

    def test_strip_html_from_content_non_string_unchanged(self):
        content = {"count": 42, "active": True}
        assert _strip_html_from_content(content) == {"count": 42, "active": True}


@override_settings(**TYPESENSE_SETTINGS)
class ProcessDocumentTests(django.test.TestCase):
    """Tests for TypesenseEngine.process_document()."""

    def _make_engine(self):
        engine = TypesenseEngine.__new__(TypesenseEngine)
        engine.index_name = "courseware_content"
        return engine

    def test_html_stripped_from_content_field(self):
        """HTML in content sub-fields is stripped before the document is indexed."""
        engine = self._make_engine()
        doc = {
            "id": "block-v1:org+x+run+type@html+block@abc",
            "content": {
                "display_name": "<b>Intro</b>",
                "body": "<p>Welcome to <em>this</em> course &amp; enjoy!</p>",
            },
        }
        result = engine.process_document(doc)
        assert result["content"]["display_name"] == "Intro"
        assert result["content"]["body"] == "Welcome to this course & enjoy!"

    def test_non_content_fields_not_stripped(self):
        """Fields outside 'content' are not modified by HTML stripping."""
        engine = self._make_engine()
        doc = {
            "id": "block-v1:org+x+run+type@html+block@abc",
            "display_name": "<b>should not be stripped</b>",
            "content": {"body": "plain"},
        }
        result = engine.process_document(doc)
        assert result["display_name"] == "<b>should not be stripped</b>"

    def test_missing_content_field_handled(self):
        """Documents without a 'content' field are processed without error."""
        engine = self._make_engine()
        doc = {"id": "block-v1:org+x+run+type@problem+block@xyz"}
        result = engine.process_document(doc)
        assert result["id"] == "block-v1:org+x+run+type@problem+block@xyz"
