""" Tests for timing functionality """

import datetime
from unittest.mock import patch, call

from django.test import TestCase
from django.test.utils import override_settings
from search.tests.mock_search_engine import MockSearchEngine
from search.utils import Timer
from search.api import emit_api_timing_event


@override_settings(SEARCH_ENGINE="search.tests.mock_search_engine.MockSearchEngine")
class TimingEventsTest(TestCase):
    """ Tests to see if timing events are emitted"""

    def setUp(self):
        super().setUp()
        MockSearchEngine.destroy()
        patcher = patch('search.api.track')
        self.mock_track = patcher.start()
        self.addCleanup(patcher.stop)

    def tearDown(self):
        MockSearchEngine.destroy()
        super().tearDown()

    def test_perform_search(self):
        search_term = "testing search"
        course_id = "mock.course.id"

        filter_generation_timer = Timer()
        filter_generation_timer.start()
        filter_generation_timer.stop()

        search_timer = Timer()
        search_timer.start()
        search_timer.stop()

        processing_timer = Timer()
        processing_timer.start()
        processing_timer.stop()

        emit_api_timing_event(search_term, course_id, filter_generation_timer, processing_timer, search_timer)
        timing_event_call = self.mock_track.emit.mock_calls[0]
        expected_call = call("edx.course.search.executed", {
            "search_term": search_term,
            "course_id": course_id,
            "filter_generation_time": {
                "start": filter_generation_timer.start_time,
                "end": filter_generation_timer.end_time,
                "elapsed": filter_generation_timer.elapsed_time,
            },
            "processing_time": {
                "start": processing_timer.start_time,
                "end": processing_timer.end_time,
                "elapsed": processing_timer.elapsed_time,
            },
            "search_time": {
                "start": search_timer.start_time,
                "end": search_timer.end_time,
                "elapsed": search_timer.elapsed_time,
            },
        })
        self.assertEqual(timing_event_call, expected_call)


class TimerTest(TestCase):
    """
    Timer Test Case
    """

    def test_start_timer(self):
        timer = Timer()
        timer.start()
        timer.stop()
        self.assertIsNotNone(timer.start_time)
        self.assertIsNotNone(timer.end_time)

    def test_elapsed_time(self):
        # pylint: disable=protected-access

        start = datetime.datetime(2024, 1, 1, 0, 0, 0, 0)
        end = start + datetime.timedelta(seconds=5)

        timer = Timer()
        timer._start_time = start
        timer._end_time = end

        self.assertEqual(timer.elapsed_time, 5)
        self.assertEqual(timer.start_time, start)
        self.assertEqual(timer.end_time, end)

    def test_elapsed_time_string(self):
        # pylint: disable=protected-access

        start = datetime.datetime(2024, 1, 1, 0, 0, 0, 0)
        end = start + datetime.timedelta(seconds=5)

        timer = Timer()
        timer._start_time = start
        timer._end_time = end

        self.assertEqual(timer.elapsed_time, 5)
        self.assertEqual(timer.start_time_string, "2024-01-01T00:00:00")
        self.assertEqual(timer.end_time_string, "2024-01-01T00:00:05")
        self.assertGreaterEqual(timer.end_time, timer.start_time)
