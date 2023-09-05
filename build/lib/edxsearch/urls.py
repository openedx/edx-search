""" import urls from search component to test it's operation when included within other django projects """

import django
from django.urls import include, path

# from django.contrib import admin
# admin.autodiscover()

import search

# urlpatterns is the standard name to use here
# pylint: disable=invalid-name
urlpatterns = [path('search/', include(search.urls))]
