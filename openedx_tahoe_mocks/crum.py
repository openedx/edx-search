"""
Mock django-crum.
"""

from unittest.mock import Mock
from django.contrib.auth.models import User


def get_current_request():
    """
    Mock the crum.get_current_request function.
    """
    request = Mock()
    request.user = User()
    return request