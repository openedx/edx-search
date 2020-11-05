"""
Mocks for the lms.djangoapps.courseware.access module so tests can run.
"""

from django.contrib.auth.models import User, AnonymousUser
from opaque_keys.edx.keys import CourseKey


def has_access(user, action, obj):
    """
    Mock the LMS has_access function.
    """
    assert isinstance(user, (User, AnonymousUser)), 'Should have a user passed to it.'
    assert action == 'see_in_catalog', 'Only one action is needed for edx-search'
    assert isinstance(obj.id, CourseKey), 'expecting a course object to match LMS see_in_catalog check'
    return True  # avoid breaking `edx-search` tests
