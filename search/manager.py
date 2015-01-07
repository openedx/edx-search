""" Abstract SearchEngine with factory method """
from django.conf import settings


class SearchEngine(object):
    """ Base abstract SearchEngine object """

    index_name = "courseware"

    def __init__(self, index=None):
        if index:
            self.index_name = index

    def index(self, doc_type, body, **kwargs):
        raise NotImplementedError

    def remove(self, doc_type, id, **kwargs):
        raise NotImplementedError

    def search(self, query_string=None, field_dictionary=None, filter_dictionary=None, **kwargs):
        raise NotImplementedError

    def search_string(self, query_string, **kwargs):
        return self.search(query_string=query_string, **kwargs)

    def search_fields(self, field_dictionary, **kwargs):
        return self.search(field_dictionary=field_dictionary)

    @staticmethod
    def get_search_engine(index=None):
        return settings.SEARCH_ENGINE(index=index)
