from django.conf.urls import patterns, include, url

# from django.contrib import admin
# admin.autodiscover()

from . import search

urlpatterns = patterns('',
    url(r'^search/', include(search.urls)),
)
