""" expose courseware search http interface """

from django.conf import settings
from django.urls import path, re_path

from . import views

COURSE_ID_PATTERN = getattr(settings, "COURSE_ID_PATTERN", r'(?P<course_id>[^/+]+(/|\+)[^/+]+(/|\+)[^/]+)')

# urlpatterns is the standard name to use here
urlpatterns = [
    path('', views.do_search, name='do_search'),
    re_path(r'^{}$'.format(COURSE_ID_PATTERN), views.do_search, name='do_search'),
    path('course_discovery/', views.course_discovery, name='course_discovery'),
]
