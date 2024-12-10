""" Abstract SearchEngine with factory method """
# This will get called by tests, but pylint thinks that it is not used

from waffle import switch_is_active  # lint-amnesty, pylint: disable=invalid-django-waffle-import
from django.conf import settings

from .utils import _load_class

# .. toggle_name: edx_search.default_elastic_search
# .. toggle_implementation: WaffleSwitch
# .. toggle_default: False
# .. toggle_description: This flag forces the use of ElasticSearch.
#      It prevents errors from switching to OpenSearch before roll out.
# .. toggle_use_cases: temporary
# .. toggle_creation_date: 2022-7-11
# .. toggle_target_removal_date: None
# .. toggle_tickets: TNL-9899
# .. toggle_warnings: This temporary feature toggle does not have a target removal date.
DEFAULT_ELASTIC_SEARCH_SWITCH = 'edx_search.default_elastic_search'


class SearchEngine:
    """
    Base abstract SearchEngine object.
    """

    index_name = "courseware"

    def __init__(self, index=None):
        if index:
            self.index_name = index

    def index(self, sources, **kwargs):
        """
        Add documents to the search index.
        """
        raise NotImplementedError

    def remove(self, doc_ids, **kwargs):
        """
        Remove documents by ids from the search index.
        """
        raise NotImplementedError

    def search(self,
               query_string=None,
               field_dictionary=None,
               filter_dictionary=None,
               exclude_dictionary=None,
               aggregation_terms=None,
               log_search_params=False,
               **kwargs):  # pylint: disable=too-many-arguments
        """
        Search for matching documents within the search index.
        """
        raise NotImplementedError

    def search_string(self, query_string, **kwargs):
        """
        Helper function when primary search is for a query string.
        """
        return self.search(query_string=query_string, **kwargs)

    def search_fields(self, field_dictionary, **kwargs):
        """
        Helper function when primary search is for a set of matching fields.
        """
        return self.search(field_dictionary=field_dictionary, **kwargs)

    @staticmethod
    def get_search_engine(index=None):
        """
        Returns the desired implementor (defined in settings).
        """
        # TNL-9899
        #   When this switch is turned on, the ElasticSearch engine is returned.
        #   This ensures that changing to OpenSearch does not break the system.
        if switch_is_active(DEFAULT_ELASTIC_SEARCH_SWITCH):
            search_engine_class = _load_class("search.elastic.ElasticSearchEngine", None)
            return search_engine_class(index=index)
        search_engine_class = _load_class(getattr(settings, "SEARCH_ENGINE", None), None)
        return search_engine_class(index=index) if search_engine_class else None
