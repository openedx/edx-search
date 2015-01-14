""" Implementation of search interface to be used for tests where ElasticSearch is unavailable """
import copy
from search.manager import SearchEngine
from search.utils import ValueRange


def _find_field(doc, field_name):
    """ find the dictionary field corresponding to the . limited name """
    if not isinstance(doc, dict):
        return ValueError('Parameter `doc` should be a python dict object')

    if not isinstance(field_name, basestring):
        raise ValueError('Parameter `field_name` should be a string')

    immediate_field, remaining_path = field_name.split('.', 1) if '.' in field_name else (field_name, None)
    field_value = doc.get(immediate_field)

    if isinstance(field_value, dict):
        return _find_field(field_value, remaining_path)
    else:
        return field_value


def _filter_intersection(documents_to_search, dictionary_object, include_blanks=False):
    """
    Filters out documents that do not match all of the field values within the dictionary_object
    If include_blanks is True, then the document is considered a match if the field is not present
    """
    if not dictionary_object:
        return documents_to_search

    def value_matches(doc, field_name, field_value):
        """
        Does the document's field match the desired value, or is the field not present if include_blanks is True
        """
        compare_value = _find_field(doc, field_name)
        if compare_value is None:
            return include_blanks

        if isinstance(field_value, ValueRange):
            return (
                (field_value.lower is None or compare_value >= field_value.lower)
                and
                (field_value.upper is None or compare_value <= field_value.upper)
            )
        else:
            return compare_value == field_value

    filtered_documents = documents_to_search
    for field_name, field_value in dictionary_object.items():
        filtered_documents = [d for d in filtered_documents if value_matches(d, field_name, field_value)]

    return filtered_documents


def _process_query_string(documents_to_search, search_strings):
    """ keep the documents that contain at least one of the search strings provided """
    def has_string(dictionary_object, search_string):
        """ search for string in dictionary items, look down into nested dictionaries """
        for name in dictionary_object:
            if isinstance(dictionary_object[name], dict):
                return has_string(dictionary_object[name], search_string)
            elif search_string in dictionary_object[name]:
                return True
        return False

    documents_to_keep = []
    for search_string in search_strings:
        documents_to_keep.extend([d for d in documents_to_search if has_string(d["content"], search_string)])

    return documents_to_keep


class MockSearchEngine(SearchEngine):

    """
    Mock implementation of SearchEngine for test purposes
    """
    _mock_elastic = {}

    @staticmethod
    def _paginate_results(size, from_, raw_results):
        """ Give the correct page of results """
        results = raw_results
        if size:
            start = 0
            if from_ is not None:
                start = from_
            results = raw_results[start:start + size]

        return results

    @classmethod
    def destroy(cls):
        """ Clean out the dictionary for test resets """
        cls._mock_elastic = {}

    def __init__(self, index=None):
        super(MockSearchEngine, self).__init__(index)

    def index(self, doc_type, body):
        """ Add document of given type to the index """
        if self.index_name not in MockSearchEngine._mock_elastic:
            MockSearchEngine._mock_elastic[self.index_name] = {}

        _mock_index = MockSearchEngine._mock_elastic[self.index_name]
        if doc_type not in _mock_index:
            _mock_index[doc_type] = []

        _mock_index[doc_type].append(body)

    def remove(self, doc_type, doc_id):
        """ Remove document of type with given id from the index """
        _mock_index = MockSearchEngine._mock_elastic[self.index_name]
        # Simply redefine the set of documents where they have either no id or do not match the given id
        _mock_index[doc_type] = [d for d in _mock_index[doc_type] if "id" not in d or d["id"] != doc_id]

    def search(self, query_string=None, field_dictionary=None, filter_dictionary=None, **kwargs):
        """ Perform search upon documents within index """
        documents_to_search = []
        if "doc_type" in kwargs:
            if kwargs["doc_type"] in MockSearchEngine._mock_elastic[self.index_name]:
                documents_to_search = MockSearchEngine._mock_elastic[self.index_name][kwargs["doc_type"]]
        else:
            for doc_type in MockSearchEngine._mock_elastic[self.index_name]:
                documents_to_search.extend(MockSearchEngine._mock_elastic[self.index_name][doc_type])

        if field_dictionary:
            documents_to_search = _filter_intersection(documents_to_search, field_dictionary)

        if filter_dictionary:
            documents_to_search = _filter_intersection(documents_to_search, filter_dictionary, True)

        if query_string:
            documents_to_search = _process_query_string(documents_to_search, query_string.split(" "))

        # Finally, find duplicates and give them a higher score
        search_results = []
        max_score = 0
        while len(documents_to_search) > 0:
            current_doc = documents_to_search[0]
            score = len([d for d in documents_to_search if d == current_doc])
            if score > max_score:
                max_score = score
            documents_to_search = [d for d in documents_to_search if d != current_doc]

            data = copy.copy(current_doc)
            search_results.append(
                {
                    "score": score,
                    "data": data,
                }
            )

        results = MockSearchEngine._paginate_results(
            kwargs["size"] if "size" in kwargs else None,
            kwargs["from_"] if "from_" in kwargs else None,
            sorted(search_results, key=lambda k: k["score"])
        )
        return {
            "took": 10,
            "total": len(search_results),
            "max_score": max_score,
            "results": results
        }
