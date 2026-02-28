#!/usr/bin/env python3
"""
Unit tests for the three new features in discover.py:
  1. _fetch_trends / _match_trends_to_categories / _search_trends_for_category
  2. _search_users_by_keyword / _get_dynamic_authors (with cache)
  3. _fetch_quotations / _expand_with_quotations

All HTTP calls are mocked — no real API key needed.
"""

import json
import sys
import os
import tempfile
import unittest
from datetime import datetime, timezone, timedelta
from pathlib import Path
from unittest.mock import patch, MagicMock

# Ensure scripts/ is importable
sys.path.insert(0, str(Path(__file__).parent))

# Patch TWITTERAPI_IO_KEY before importing discover
os.environ.setdefault("TWITTERAPI_IO_KEY", "test-key-12345")

import discover


# ── Helpers ──────────────────────────────────────────────────

def _recent_created_at(hours_ago: float = 2.0) -> str:
    dt = datetime.now(timezone.utc) - timedelta(hours=hours_ago)
    return dt.strftime("%a %b %d %H:%M:%S %z %Y")


def _make_tweet(tid: str, text: str, likes: int = 50, hours_ago: float = 2.0) -> dict:
    return {
        "id": tid,
        "text": text,
        "createdAt": _recent_created_at(hours_ago),
        "likeCount": likes,
        "bookmarkCount": 5,
        "retweetCount": 10,
        "replyCount": 3,
        "quoteCount": 2,
        "viewCount": 1000,
        "url": f"https://twitter.com/user/status/{tid}",
        "lang": "en",
        "author": {
            "userName": "testuser",
            "name": "Test User",
            "followers": 10000,
            "isBlueVerified": False,
        },
    }


def _make_user(username: str, followers: int = 5000) -> dict:
    return {
        "userName": username,
        "followers": followers,
        "name": username.capitalize(),
    }


def _make_response(body: dict, status: int = 200) -> MagicMock:
    r = MagicMock()
    r.status_code = status
    r.json.return_value = body
    r.raise_for_status = MagicMock()
    return r


# ── Tests ─────────────────────────────────────────────────────

class TestFetchTrends(unittest.TestCase):

    def test_parses_trends_correctly(self):
        fake_response = _make_response({
            "trends": [
                {"name": "ChatGPT", "target": {"query": "ChatGPT"}, "rank": 1, "meta_description": "AI model"},
                {"name": "Python", "target": {"query": "Python"},  "rank": 2, "meta_description": "coding language"},
                {"name": "Figma",  "target": {"query": "Figma"},   "rank": 3, "meta_description": "design tool"},
            ],
            "status": "success",
        })
        with patch("discover._request_with_backoff", return_value=fake_response):
            result = discover._fetch_trends(woeid=1)
        self.assertEqual(len(result), 3)
        self.assertEqual(result[0]["name"], "ChatGPT")
        self.assertEqual(result[0]["query"], "ChatGPT")
        self.assertEqual(result[0]["rank"], 1)

    def test_returns_empty_on_error(self):
        with patch("discover._request_with_backoff", side_effect=Exception("network error")):
            result = discover._fetch_trends(woeid=1)
        self.assertEqual(result, [])

    def test_skips_trends_with_empty_name(self):
        fake_response = _make_response({
            "trends": [
                {"name": "", "target": {"query": ""}, "rank": 1, "meta_description": ""},
                {"name": "OpenAI", "target": {"query": "OpenAI"}, "rank": 2, "meta_description": "ai company"},
            ]
        })
        with patch("discover._request_with_backoff", return_value=fake_response):
            result = discover._fetch_trends()
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["name"], "OpenAI")


class TestMatchTrendsToCategories(unittest.TestCase):

    def test_maps_ai_trend_to_general_ai(self):
        trends = [
            {"name": "OpenAI", "query": "OpenAI", "rank": 1, "description": "ai company release"},
        ]
        matched = discover._match_trends_to_categories(trends)
        self.assertIn("OpenAI", [t["name"] for t in matched["General AI"]])

    def test_maps_coding_trend(self):
        trends = [
            {"name": "GitHub Copilot", "query": "GitHub Copilot", "rank": 1, "description": "coding assistant"},
        ]
        matched = discover._match_trends_to_categories(trends)
        ai_coding_names = [t["name"] for t in matched["AI Coding"]]
        self.assertIn("GitHub Copilot", ai_coding_names)

    def test_respects_max_per_cat(self):
        # Generate more trends than TRENDS_MAX_PER_CAT for one category
        trends = [
            {"name": f"AI Tool {i}", "query": f"ai tool {i}", "rank": i, "description": "ai model agent"}
            for i in range(10)
        ]
        matched = discover._match_trends_to_categories(trends)
        for cat_trends in matched.values():
            self.assertLessEqual(len(cat_trends), discover.TRENDS_MAX_PER_CAT)

    def test_no_match_returns_empty_lists(self):
        trends = [
            {"name": "SuperBowl", "query": "SuperBowl", "rank": 1, "description": "football game"},
        ]
        matched = discover._match_trends_to_categories(trends)
        total_matched = sum(len(v) for v in matched.values())
        self.assertEqual(total_matched, 0)


class TestSearchUsersAndDynamicAuthors(unittest.TestCase):

    def test_filters_low_follower_users(self):
        fake_response = _make_response({
            "users": [
                _make_user("biginfluencer", followers=50000),
                _make_user("tinyaccount",  followers=100),   # should be filtered
                _make_user("mediumaccount", followers=1000),
            ]
        })
        with patch("discover._request_with_backoff", return_value=fake_response):
            result = discover._search_users_by_keyword("AI marketing expert")
        self.assertIn("biginfluencer", result)
        self.assertIn("mediumaccount", result)
        self.assertNotIn("tinyaccount", result)

    def test_returns_empty_on_error(self):
        with patch("discover._request_with_backoff", side_effect=Exception("timeout")):
            result = discover._search_users_by_keyword("AI research")
        self.assertEqual(result, [])

    def test_dynamic_authors_cache_hit(self):
        category = "General AI"
        cached_usernames = ["expert1", "expert2"]
        # Write a fresh cache entry
        cache = {
            category: {
                "usernames": cached_usernames,
                "cached_at": datetime.now(timezone.utc).isoformat(),
            }
        }
        with tempfile.TemporaryDirectory() as tmpdir:
            cache_path = Path(tmpdir) / "dynamic_authors_cache.json"
            cache_path.write_text(json.dumps(cache), encoding="utf-8")
            with patch("discover._dyn_authors_cache_path", return_value=cache_path):
                # _search_users_by_keyword should NOT be called (cache hit)
                with patch("discover._search_users_by_keyword") as mock_search:
                    result = discover._get_dynamic_authors(category)
                    mock_search.assert_not_called()
        self.assertEqual(result, cached_usernames)

    def test_dynamic_authors_cache_miss_calls_api(self):
        category = "AI Coding"
        # Expired cache
        old_cache = {
            category: {
                "usernames": ["old_user"],
                "cached_at": (datetime.now(timezone.utc) - timedelta(hours=48)).isoformat(),
            }
        }
        new_usernames = ["fresh_user1", "fresh_user2"]
        with tempfile.TemporaryDirectory() as tmpdir:
            cache_path = Path(tmpdir) / "dynamic_authors_cache.json"
            cache_path.write_text(json.dumps(old_cache), encoding="utf-8")
            with patch("discover._dyn_authors_cache_path", return_value=cache_path):
                with patch("discover._search_users_by_keyword", return_value=new_usernames):
                    result = discover._get_dynamic_authors(category)
        self.assertEqual(result, new_usernames)


class TestQuoteTweets(unittest.TestCase):

    def test_fetch_quotations_parses_response(self):
        fake_quotes = [_make_tweet(f"q{i}", f"Quote tweet comment {i}") for i in range(5)]
        fake_response = _make_response({"tweets": fake_quotes})
        with patch("discover._request_with_backoff", return_value=fake_response):
            result = discover._fetch_quotations("original123", max_items=20)
        self.assertEqual(len(result), 5)

    def test_fetch_quotations_returns_empty_on_error(self):
        with patch("discover._request_with_backoff", side_effect=Exception("404")):
            result = discover._fetch_quotations("bad_id")
        self.assertEqual(result, [])

    def test_expand_with_quotations_picks_top_n_by_engagement(self):
        # High engagement tweet should be expanded
        high_eng = _to_candidate_helper("high_eng", likes=1000)
        low_eng  = _to_candidate_helper("low_eng",  likes=10)
        candidates = [low_eng, high_eng]
        seen_ids = {"high_eng", "low_eng"}

        quote_tweet = _make_tweet("q1", "Great analysis on this! " + "word " * 5)
        fake_response = _make_response({"tweets": [quote_tweet]})

        with patch("discover._fetch_quotations", return_value=[quote_tweet]) as mock_fetch:
            with patch("discover._sleep"):
                old_top_n = discover.QUOTES_TOP_N
                discover.QUOTES_TOP_N = 1  # only expand top 1
                result = discover._expand_with_quotations("General AI", candidates, seen_ids)
                discover.QUOTES_TOP_N = old_top_n

        # Should have expanded the high-engagement tweet
        mock_fetch.assert_called_once_with("high_eng", max_items=discover.QUOTES_MAX_PER_TWEET)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["id"], "q1")
        self.assertEqual(result[0]["source"], "quote")
        self.assertEqual(result[0]["quoted_id"], "high_eng")

    def test_expand_deduplicates_known_ids(self):
        candidate = _to_candidate_helper("orig", likes=500)
        seen_ids = {"orig", "q_already_seen"}  # q1 already in seen

        quote_tweet = _make_tweet("q_already_seen", "This is a duplicate " + "word " * 5)
        with patch("discover._fetch_quotations", return_value=[quote_tweet]):
            with patch("discover._sleep"):
                result = discover._expand_with_quotations("General AI", [candidate], seen_ids)

        # Should be empty — already deduped
        self.assertEqual(result, [])

    def test_expand_filters_old_tweets(self):
        candidate = _to_candidate_helper("orig", likes=500)
        seen_ids = {"orig"}

        old_tweet = _make_tweet("old_q", "Old quote tweet content " + "x " * 5, hours_ago=72)
        with patch("discover._fetch_quotations", return_value=[old_tweet]):
            with patch("discover._sleep"):
                result = discover._expand_with_quotations("General AI", [candidate], seen_ids)

        self.assertEqual(result, [])

    def test_expand_disabled_returns_empty(self):
        old_val = discover.QUOTES_ENABLED
        discover.QUOTES_ENABLED = False
        result = discover._expand_with_quotations("General AI", [_to_candidate_helper("x")], set())
        discover.QUOTES_ENABLED = old_val
        self.assertEqual(result, [])


def _to_candidate_helper(tid: str, likes: int = 100) -> dict:
    tw = _make_tweet(tid, "Sample tweet with enough words for ranking " + "word " * 3, likes=likes)
    return discover._to_candidate("General AI", tw, source="keyword")


if __name__ == "__main__":
    unittest.main(verbosity=2)
