# -*- coding: utf-8 -*-
"""Tests for audience-targeting discoverability gate in Elasticsearch discovery."""
from __future__ import absolute_import, division, print_function, unicode_literals

from datetime import datetime

from django.test import TestCase
from django.test.utils import override_settings
from mock import Mock, patch

from search.api import (
    course_discovery_search,
    mixed_content_discovery_search,
    programs_discovery_search,
)
from search.elastic import _process_targeting_gate, build_elasticsearch_query_dict
from search.filter_generator import SearchFilterGenerator as CourseSearchFilterGenerator
from search.program_filter_generator import SearchFilterGenerator as ProgramSearchFilterGenerator
from search.tests.mock_search_engine import MockSearchEngine, _process_targeting_gate as mock_process_gate
from search.tests.test_course_discovery import DemoCourse
from search.tests.utils import SearcherMixin, TEST_INDEX_NAME


FLAG = 'discoverable_only_by_targeted'


class TargetingGateQueryTestCase(TestCase):
    """Unit tests for ES filter construction."""

    def test_none_gate_is_noop(self):
        self.assertEqual(_process_targeting_gate(None), {})
        self.assertEqual(_process_targeting_gate({}), {})

    def test_incomplete_gate_is_noop(self):
        self.assertEqual(_process_targeting_gate({'flag_field': FLAG}), {})
        self.assertEqual(_process_targeting_gate({'key_field': 'course'}), {})

    def test_empty_allowed_hides_all_flagged(self):
        gate = {'flag_field': FLAG, 'key_field': 'course', 'allowed': []}
        result = _process_targeting_gate(gate)
        self.assertEqual(list(result.keys()), ['or'])
        self.assertEqual(len(result['or']), 1)
        self.assertIn('not', result['or'][0])

    def test_allowed_ids_add_terms_clause(self):
        gate = {
            'flag_field': FLAG,
            'key_field': 'course',
            'allowed': ['course-v1:Org+A+Run', 'course-v1:Org+B+Run'],
        }
        result = _process_targeting_gate(gate)
        self.assertEqual(len(result['or']), 2)
        self.assertEqual(
            result['or'][1],
            {'terms': {'course': ['course-v1:Org+A+Run', 'course-v1:Org+B+Run']}},
        )

    def test_build_query_includes_targeting_filter(self):
        gate = {'flag_field': FLAG, 'key_field': 'uuid', 'allowed': ['prog-1']}
        query = build_elasticsearch_query_dict(targeting_gate=gate)
        filters = query['filtered']['filter']['bool']['must']
        self.assertTrue(any('or' in clause for clause in filters))

    def test_build_query_without_gate_has_no_or_filter(self):
        query = build_elasticsearch_query_dict()
        self.assertEqual(query, {'match_all': {}})


class MockTargetingGateTestCase(TestCase):
    """Mock engine mirrors ES keep/hide semantics."""

    DOCS = [
        {'id': 'open', 'course': 'c-open', FLAG: False},
        {'id': 'missing', 'course': 'c-missing'},
        {'id': 'allowed', 'course': 'c-allowed', FLAG: True},
        {'id': 'hidden', 'course': 'c-hidden', FLAG: True},
    ]

    def test_keeps_unflagged_and_allowed(self):
        gate = {'flag_field': FLAG, 'key_field': 'course', 'allowed': ['c-allowed']}
        kept = mock_process_gate(self.DOCS, gate)
        self.assertEqual(
            sorted(doc['id'] for doc in kept),
            ['allowed', 'missing', 'open'],
        )

    def test_empty_allowed_hides_flagged(self):
        gate = {'flag_field': FLAG, 'key_field': 'course', 'allowed': []}
        kept = mock_process_gate(self.DOCS, gate)
        self.assertEqual(sorted(doc['id'] for doc in kept), ['missing', 'open'])


class GatingCourseFilterGenerator(CourseSearchFilterGenerator):
    """Test double that always returns a course targeting gate."""

    def targeting_gate(self, **kwargs):
        user = kwargs.get('user')
        if user is None:
            return None
        return {
            'flag_field': FLAG,
            'key_field': 'course',
            'allowed': getattr(user, 'allowed_courses', []),
        }


class GatingProgramFilterGenerator(ProgramSearchFilterGenerator):
    """Test double that always returns a program targeting gate."""

    def targeting_gate(self, **kwargs):
        user = kwargs.get('user')
        if user is None:
            return None
        return {
            'flag_field': FLAG,
            'key_field': 'uuid',
            'allowed': getattr(user, 'allowed_programs', []),
        }


@override_settings(SEARCH_ENGINE="search.tests.mock_search_engine.MockSearchEngine")
@override_settings(ELASTIC_FIELD_MAPPINGS={
    "start_date": {"type": "date"},
    "enrollment_start": {"type": "date"},
    "enrollment_end": {"type": "date"}
})
@override_settings(MOCK_SEARCH_BACKING_FILE=None)
@override_settings(COURSEWARE_INDEX_NAME=TEST_INDEX_NAME)
@override_settings(SEARCH_FILTER_GENERATOR="search.tests.test_targeting_gate.GatingCourseFilterGenerator")
@override_settings(SEARCH_SKIP_ENROLLMENT_START_DATE_FILTERING=True)
class CourseDiscoveryTargetingGateTestCase(TestCase, SearcherMixin):
    """course_discovery_search applies targeting_gate from the LMS generator."""

    def setUp(self):
        super(CourseDiscoveryTargetingGateTestCase, self).setUp()
        MockSearchEngine.destroy()
        self._searcher = None
        DemoCourse.reset_count()
        DemoCourse.get_and_index(self.searcher, {
            "course": "course-v1:Org+Open+Run",
            "discoverable_only_by_targeted": False,
            "content": {"display_name": "Open Course"},
        })
        DemoCourse.get_and_index(self.searcher, {
            "course": "course-v1:Org+Targeted+Run",
            "discoverable_only_by_targeted": True,
            "content": {"display_name": "Targeted Course"},
        })
        DemoCourse.get_and_index(self.searcher, {
            "course": "course-v1:Org+Secret+Run",
            "discoverable_only_by_targeted": True,
            "content": {"display_name": "Secret Course"},
        })

    def tearDown(self):
        MockSearchEngine.destroy()
        self._searcher = None
        super(CourseDiscoveryTargetingGateTestCase, self).tearDown()

    def test_generate_targeting_gate_none_without_user(self):
        self.assertIsNone(CourseSearchFilterGenerator.generate_targeting_gate())

    def test_anonymous_sees_all_when_gate_is_none(self):
        results = course_discovery_search()
        self.assertEqual(results['total'], 3)

    def test_learner_sees_open_plus_allowed_targeted(self):
        learner = Mock()
        learner.allowed_courses = ['course-v1:Org+Targeted+Run']
        results = course_discovery_search(user=learner)
        courses = sorted(hit['data']['course'] for hit in results['results'])
        self.assertEqual(courses, [
            'course-v1:Org+Open+Run',
            'course-v1:Org+Targeted+Run',
        ])
        self.assertEqual(results['total'], 2)

    def test_learner_with_no_matches_sees_only_open(self):
        learner = Mock()
        learner.allowed_courses = []
        results = course_discovery_search(user=learner)
        courses = [hit['data']['course'] for hit in results['results']]
        self.assertEqual(courses, ['course-v1:Org+Open+Run'])


@override_settings(SEARCH_ENGINE="search.tests.mock_search_engine.MockSearchEngine")
@override_settings(MOCK_SEARCH_BACKING_FILE=None)
@override_settings(PROGRAM_INDEX_NAME=TEST_INDEX_NAME)
@override_settings(PROGRAM_SEARCH_FILTER_GENERATOR="search.tests.test_targeting_gate.GatingProgramFilterGenerator")
class ProgramDiscoveryTargetingGateTestCase(TestCase, SearcherMixin):
    """programs_discovery_search applies targeting_gate from the program generator."""

    def setUp(self):
        super(ProgramDiscoveryTargetingGateTestCase, self).setUp()
        MockSearchEngine.destroy()
        self._searcher = None
        self.searcher.index(doc_type="program_info", sources=[
            {
                "id": "open-prog",
                "uuid": "uuid-open",
                "discoverable_only_by_targeted": False,
                "content": {"title": "Open Path"},
                "start": datetime(2014, 2, 1),
            },
            {
                "id": "targeted-prog",
                "uuid": "uuid-targeted",
                "discoverable_only_by_targeted": True,
                "content": {"title": "Targeted Path"},
                "start": datetime(2014, 2, 1),
            },
            {
                "id": "secret-prog",
                "uuid": "uuid-secret",
                "discoverable_only_by_targeted": True,
                "content": {"title": "Secret Path"},
                "start": datetime(2014, 2, 1),
            },
        ])

    def tearDown(self):
        MockSearchEngine.destroy()
        self._searcher = None
        super(ProgramDiscoveryTargetingGateTestCase, self).tearDown()

    def test_learner_sees_open_plus_allowed_targeted(self):
        learner = Mock()
        learner.allowed_programs = ['uuid-targeted']
        results = programs_discovery_search(user=learner)
        uuids = sorted(hit['data']['uuid'] for hit in results['results'])
        self.assertEqual(uuids, ['uuid-open', 'uuid-targeted'])
        self.assertEqual(results['total'], 2)

    def test_base_program_generator_gates_nothing(self):
        self.assertIsNone(ProgramSearchFilterGenerator().targeting_gate(user=Mock()))


@override_settings(SEARCH_FILTER_GENERATOR="search.tests.test_targeting_gate.GatingCourseFilterGenerator")
@override_settings(PROGRAM_SEARCH_FILTER_GENERATOR="search.tests.test_targeting_gate.GatingProgramFilterGenerator")
@override_settings(COURSEWARE_INDEX_NAME="courseware_index")
@override_settings(PROGRAM_INDEX_NAME="program_index")
class MixedDiscoveryTargetingGatePlumbingTestCase(TestCase):
    """mixed_content_discovery_search passes each index its own targeting_gate."""

    @patch('search.api.process_range_data', side_effect=lambda results: results)
    @patch('search.elastic.search_mixed_discovery')
    @patch('search.elastic.build_elasticsearch_query_dict')
    @patch('search.api.SearchEngine.get_search_engine')
    def test_mixed_passes_course_and_program_gates(
            self, mock_get_engine, mock_build_query, mock_mixed_search, _mock_process):
        from search.elastic import ElasticSearchEngine

        # Avoid ElasticSearchEngine.__init__ / cluster contact; isinstance must still pass.
        mock_get_engine.return_value = object.__new__(ElasticSearchEngine)
        mock_build_query.side_effect = [{'match_all': {}}, {'match_all': {}}]
        mock_mixed_search.return_value = {'took': 1, 'total': 0, 'results': [], 'facets': {}}

        learner = Mock()
        learner.allowed_courses = ['course-v1:Org+Targeted+Run']
        learner.allowed_programs = ['uuid-targeted']

        mixed_content_discovery_search(user=learner)

        self.assertEqual(mock_build_query.call_count, 2)
        course_gate = mock_build_query.call_args_list[0][1]['targeting_gate']
        program_gate = mock_build_query.call_args_list[1][1]['targeting_gate']
        self.assertEqual(course_gate['key_field'], 'course')
        self.assertEqual(course_gate['allowed'], ['course-v1:Org+Targeted+Run'])
        self.assertEqual(program_gate['key_field'], 'uuid')
        self.assertEqual(program_gate['allowed'], ['uuid-targeted'])
