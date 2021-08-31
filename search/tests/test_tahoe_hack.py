"""
Tests for the Tahoe hack: hack_filter_discovery_results.

This tech-debt and we should implement a proper edx-search results processor:

 - Tech debt task: https://appsembler.atlassian.net/browse/RED-637
 - CourseDiscoveryResultProcessor implementation: https://github.com/appsembler/edx-search/pull/2
"""

from django.test import TestCase
from mock import patch, Mock

from search.api import hack_filter_discovery_results


class TestHackFilterDiscoveryResults(TestCase):

    @patch('search.api.hack_course_is_accessible_to_current_user', Mock(return_value=True))
    def test_all_have_access(self):
        pre_results = get_mock_course_discovery_search_results()
        results = hack_filter_discovery_results(pre_results)
        self.assertEqual(results['total'], 4)  # Should not change result count, no course should be denied
        self.assertEqual(results['access_denied_count'], 0)  # All courses should be allowed
        self.assertEqual(len(results['results']), 4)  # Result count should match `total`

    @patch('search.api.hack_course_is_accessible_to_current_user')
    def test_allow_two_out_of_four(self, mock_has_access):
        """
        Ensure `total` is counted correctly when removing
        """

        pre_results = get_mock_course_discovery_search_results()
        first_random_two_courses = {r['data']['id'] for r in pre_results['results'][:2]}

        def mock_course_is_accessible_to_current_user(course_id, action):
            """
            Grant access to the first two courses just to test access_denied_count/total calculations.
            """
            return course_id in first_random_two_courses

        mock_has_access.side_effect = mock_course_is_accessible_to_current_user
        results = hack_filter_discovery_results(pre_results)
        self.assertEqual(results['access_denied_count'], 2)  # Only two courses should be allowed
        self.assertEqual(len(results['results']), 2)  # Result count should match `total`
        self.assertEqual(results['total'], 2)  # Should count the remaining two courses


def get_mock_course_discovery_search_results():
    """
    Get similar data to edx-search's search/api.py course_discovery_search function.
    """
    return {
        "total": 4,
        "results": [
            {
                "data": {
                    "id": "course-v1:delta-rook CEDE 2021-08-02",
                },
            },
            {
                "data": {
                    "id": "course-v1:delta-rook Template 2019",
                },
            },
            {
                "data": {
                    "id": "course-v1:delta-rook OE201 2018",
                },
            },
            {
                "data": {
                    "id": "course-v1:delta-rook AVL101 2018",
                },
            },
        ],
        "facets": {
            "language": {"total": 12, "other": 0, "terms": {"en": 12}},
            "org": {"total": 12, "other": 0, "terms": {"delta-rook": 12}},
            "modes": {"total": 12, "other": 0, "terms": {"honor": 12}},
        },
        "max_score": 1,
        "took": 2,
        "access_denied_count": 0,
    }
