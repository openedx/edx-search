""" import urls from search component to test it's operation when included within other django projects """
from django.conf.urls import patterns, include, url

# from django.contrib import admin
# admin.autodiscover()

import search

urlpatterns = patterns('', url(r'^search/', include(search.urls)),)
