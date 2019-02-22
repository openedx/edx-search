""" Abstract SearchEngine with factory method """
# This will get called by tests, but pylint thinks that it is not used
from __future__ import absolute_import
from django.conf import settings

from .utils import _load_class


class SearchEngine(object):

    """ Base abstract SearchEngine object """

    index_name = "courseware"

    def __init__(self, index=None):
        if index:
            self.index_name = index

    def index(self, doc_type, sources, **kwargs):
        """ This operation is called to add documents of given type to the search index """
        raise NotImplementedError

    def remove(self, doc_type, doc_ids, **kwargs):
        """ This operation is called to remove documents of given type from the search index """
        raise NotImplementedError

    def search(self,
               query_string=None,
               field_dictionary=None,
               filter_dictionary=None,
               exclude_dictionary=None,
               facet_terms=None,
               **kwargs):  # pylint: disable=too-many-arguments
        """ This operation is called to search for matching documents within the search index """
        raise NotImplementedError

    def search_string(self, query_string, **kwargs):
        """ Helper function when primary search is for a query string """
        return self.search(query_string=query_string, **kwargs)

    def search_fields(self, field_dictionary, **kwargs):
        """ Helper function when primary search is for a set of matching fields """
        return self.search(field_dictionary=field_dictionary, **kwargs)

    @staticmethod
    def get_search_engine(index=None):
        """
        Returns the desired implementor (defined in settings)
        """
        search_engine_class = _load_class(getattr(settings, "SEARCH_ENGINE", None), None)
        return search_engine_class(index=index) if search_engine_class else None
