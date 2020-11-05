"""
Mocks for xmodule.modulestore.django tests can run.
"""

from unittest.mock import Mock
from opaque_keys.edx.keys import CourseKey


def get_course(course_key, depth=0):
    """
    Mock get_course function.
    """
    assert depth == 0, 'Avoid loading entire course.'
    assert isinstance(course_key, CourseKey), 'this function expects a valid course key'
    course = Mock()
    course.id = course_key
    return course


def modulestore():
    """
    Mock modulestore factory.
    """
    store = Mock()
    store.get_course = get_course
    return store
