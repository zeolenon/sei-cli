"""Tests for search_units() hybrid lookup."""

from unittest.mock import MagicMock, patch
import pytest
from sei_cli.client import SEIClient


class TestSearchUnitsUnitIds:
    """Tests for the UNIT_IDS fast path."""

    def setup_method(self):
        self.client = SEIClient.__new__(SEIClient)

    def test_partial_match_returns_results(self):
        results = self.client.search_units("APODI")
        assert len(results) > 0
        for uid, name in results:
            assert "APODI" in name.upper()

    def test_case_insensitive_match(self):
        results_upper = self.client.search_units("APODI")
        results_lower = self.client.search_units("apodi")
        assert results_upper == results_lower

    def test_accent_normalization_does_not_crash(self):
        results = self.client.search_units("mossoro")
        assert isinstance(results, list)

    def test_exact_match_ranked_first(self):
        results = self.client.search_units("SIDAM")
        assert len(results) >= 1
        uid, name = results[0]
        assert name == "SIDAM"

    def test_no_match_falls_back_to_ajax(self):
        with patch.object(self.client, "_search_units_ajax", return_value=[]) as mock_ajax:
            results = self.client.search_units("ZZZZNOTEXISTENT")
        mock_ajax.assert_called_once_with("ZZZZNOTEXISTENT", "0")
        assert results == []

    def test_ajax_not_called_when_unit_ids_match(self):
        with patch.object(self.client, "_search_units_ajax") as mock_ajax:
            results = self.client.search_units("APODI")
        mock_ajax.assert_not_called()
        assert len(results) > 0

    def test_returns_list_of_id_name_tuples(self):
        results = self.client.search_units("APODI")
        for item in results:
            assert isinstance(item, tuple) and len(item) == 2
            uid, name = item
            assert isinstance(uid, str) and isinstance(name, str)

    def test_specific_known_unit_found(self):
        # "CMDO PABM APODI" -> id "110008367"
        results = self.client.search_units("PABM APODI")
        ids = [uid for uid, _ in results]
        assert "110008367" in ids

    def test_empty_when_no_match_and_ajax_empty(self):
        with patch.object(self.client, "_search_units_ajax", return_value=[]):
            results = self.client.search_units("ZZZNOMATCH999")
        assert results == []


class TestSearchUnitsAjaxFallback:
    """Tests for the AJAX fallback (_search_units_ajax)."""

    def setup_method(self):
        self.client = SEIClient.__new__(SEIClient)

    def test_ajax_parses_option_elements(self):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = '<option value="999">UNIT AJAX</option>'

        with patch.object(self.client, "_post", return_value=mock_resp):
            with patch.object(self.client, "_sei_url", return_value="http://sei/ajax"):
                results = self.client._search_units_ajax("AJAX")

        assert ("999", "UNIT AJAX") in results

    def test_ajax_returns_empty_on_non_200(self):
        mock_resp = MagicMock()
        mock_resp.status_code = 500

        with patch.object(self.client, "_post", return_value=mock_resp):
            with patch.object(self.client, "_sei_url", return_value="http://sei/ajax"):
                results = self.client._search_units_ajax("FAIL")

        assert results == []

    def test_ajax_returns_empty_on_exception(self):
        with patch.object(self.client, "_post", side_effect=Exception("network error")):
            with patch.object(self.client, "_sei_url", return_value="http://sei/ajax"):
                results = self.client._search_units_ajax("ERROR")

        assert results == []
