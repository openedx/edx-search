""" expose courseware search http interface """
from django.conf.urls import patterns, url

from . import views

# urlpatterns is the standard name to use here
# pylint: disable=invalid-name
urlpatterns = patterns(
    '',
    url(r'^$', views.do_search, name='do_search'),
    url(r'^(?P<course_id>.+)$', views.do_search, name='do_search'),
)
