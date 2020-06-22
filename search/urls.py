""" expose courseware search http interface """

from django.conf import settings
from django.conf.urls import url

from . import views

COURSE_ID_PATTERN = getattr(settings, "COURSE_ID_PATTERN", r'(?P<course_id>[^/+]+(/|\+)[^/+]+(/|\+)[^/]+)')

# urlpatterns is the standard name to use here
# pylint: disable=invalid-name
urlpatterns = [
    url(r'^$', views.do_search, name='do_search'),
    url(r'^{}$'.format(COURSE_ID_PATTERN), views.do_search, name='do_search'),
    url(r'^course_discovery/$', views.course_discovery, name='course_discovery'),
]
