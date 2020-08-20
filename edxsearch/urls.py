""" import urls from search component to test it's operation when included within other django projects """

import django
from django.conf.urls import include, url

import search

# from django.contrib import admin
# admin.autodiscover()


# urlpatterns is the standard name to use here
# pylint: disable=invalid-name
urlpatterns = [url(r'^search/', include(search.urls))]
