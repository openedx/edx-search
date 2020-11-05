"""
Mocks for xmodule.modulestore.django tests can run.
"""

from unittest.mock import Mock


def get_course(course_key, depth=0):
    """
    Mock get_course function.
    """
    assert depth == 0, 'Avoid loading entire course.'
    assert str(course_key).startswith('course-v1:')
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
