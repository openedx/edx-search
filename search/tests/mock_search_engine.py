""" Implementation of search interface to be used for tests where ElasticSearch is unavailable """

import copy
from collections import defaultdict
from datetime import datetime
import json
import os
import pytz

from django.conf import settings
from django.core.serializers.json import DjangoJSONEncoder

from search.elastic import RESERVED_CHARACTERS
from search.search_engine_base import SearchEngine
from search.utils import ValueRange, DateRange, _is_iterable


def json_date_to_datetime(json_date_string_value):
    ''' converts json date string to date object '''
    if "T" in json_date_string_value:
        if "." in json_date_string_value:
            format_string = "%Y-%m-%dT%H:%M:%S.%f"
        else:
            format_string = "%Y-%m-%dT%H:%M:%S"
        if json_date_string_value.endswith("Z"):
            format_string += "Z"

    else:
        format_string = "%Y-%m-%d"

    return datetime.strptime(
        json_date_string_value,
        format_string
    )


def _find_field(doc, field_name):
    """ find the dictionary field corresponding to the . limited name """
    if not isinstance(doc, dict):
        raise ValueError('Parameter `doc` should be a python dict object')

    if not isinstance(field_name, str):
        raise ValueError('Parameter `field_name` should be a string')

    immediate_field, remaining_path = field_name.split('.', 1) if '.' in field_name else (field_name, None)
    field_value = doc.get(immediate_field)

    if isinstance(field_value, dict):
        return _find_field(field_value, remaining_path)

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

        # if we have a string that we are trying to process as a date object
        if isinstance(field_value, (DateRange, datetime)):
            if isinstance(compare_value, str):
                compare_value = json_date_to_datetime(compare_value)

            field_has_tz_info = False
            if isinstance(field_value, DateRange):
                field_has_tz_info = (
                    (field_value.lower and field_value.lower.tzinfo is not None)
                    or (field_value.upper and field_value.upper.tzinfo is not None)
                )
            else:
                field_has_tz_info = field_value.tzinfo is not None

            if not field_has_tz_info:
                compare_value = compare_value.replace(tzinfo=None)
            elif compare_value.tzinfo is None:
                compare_value = compare_value.replace(tzinfo=pytz.UTC)

        if isinstance(field_value, ValueRange):
            return (
                (field_value.lower is None or compare_value >= field_value.lower)
                and (field_value.upper is None or compare_value <= field_value.upper)
            )
        if _is_iterable(compare_value) and not _is_iterable(field_value):
            return any((item == field_value for item in compare_value))

        if _is_iterable(field_value) and not _is_iterable(compare_value):
            return any((item == compare_value for item in field_value))

        if _is_iterable(compare_value) and _is_iterable(field_value):
            return any((str(item) in field_value for item in compare_value))

        return compare_value == field_value

    filtered_documents = documents_to_search
    for field_name, field_value in dictionary_object.items():
        filtered_documents = [d for d in filtered_documents if value_matches(d, field_name, field_value)]

    return filtered_documents


def _process_query_string(documents_to_search, query_string):
    """ keep the documents that contain at least one of the search strings provided """
    def _encode_string(string):
        """Encode a Unicode string in the same way as the Elasticsearch search engine."""
        string = string.translate(string.maketrans('', '', RESERVED_CHARACTERS))
        return string

    def has_string(dictionary_object, search_string):
        """ search for string in dictionary items, look down into nested dictionaries """
        for name in dictionary_object:
            if isinstance(dictionary_object[name], dict):
                return has_string(dictionary_object[name], search_string)
            if dictionary_object[name]:
                if search_string.lower() in _encode_string(dictionary_object[name].lower()):
                    return True
        return False

    search_strings = _encode_string(query_string).split(" ")
    documents_to_keep = []
    for search_string in search_strings:
        documents_to_keep.extend(
            [d for d in documents_to_search if "content" in d and has_string(d["content"], search_string)]
        )

    return documents_to_keep


def _process_exclude_dictionary(documents_to_search, exclude_dictionary):
    """ remove results that have fields that match in the exclude_dictionary """
    for exclude_property in exclude_dictionary:
        exclude_values = exclude_dictionary[exclude_property]
        if not isinstance(exclude_values, list):
            exclude_values = [exclude_values]
        documents_to_search = [
            document
            for document in documents_to_search
            if document.get(exclude_property) not in exclude_values
        ]
    return documents_to_search


def _count_aggregated_values(documents, aggregation_terms):
    """
    Calculate the counts for the aggregations provided:

    For each aggregation, count up the number of hits for each aggregation value, so
    that we can report back the breakdown of how many of each value there
    exist. Notice that the total is the total number of aggregation matches that
    we receive - a single document will get counted multiple times in the
    total if the aggregation field is multi-valued:

        e.g. a course may have a value for modes as ["honor", "validated"], and
        so the document will count as 1 towards the honor count, 1 towards the
        validated count, and 2 towards the total. (This may be a little
        surprising but matches the behaviour that elasticsearch presents)

    """
    aggregations = {}

    def process_aggregation(aggregate):
        """
        Find the values for this aggregation
        """
        aggregated_documents = [
            agg_document for agg_document in documents if aggregate in agg_document
        ]
        terms = defaultdict(int)

        def add_agg_value(agg_value):
            """
            Adds the discovered value to the counts for the selected aggregation.
            """
            if isinstance(agg_value, list):
                for individual_value in agg_value:
                    add_agg_value(individual_value)
            else:
                terms[agg_value] += 1

        for document in aggregated_documents:
            add_agg_value(document[aggregate])

        total = sum([terms[term] for term in terms])

        return total, terms

    for agg in aggregation_terms:
        total, terms = process_aggregation(agg)
        aggregations[agg] = {
            "total": total,
            "terms": terms,
        }

    return aggregations


class MockSearchEngine(SearchEngine):

    """
    Mock implementation of SearchEngine for test purposes
    """
    _mock_elastic = {}
    _disabled = False
    _file_name_override = None

    @classmethod
    def create_test_file(cls, file_name=None, index_content=None):
        """ creates test file from settings """
        if index_content:
            cls._mock_elastic = index_content
        else:
            cls._mock_elastic = {}
        if file_name:
            cls._file_name_override = file_name
        cls._write_to_file(create_if_missing=True)

    @classmethod
    def destroy_test_file(cls):
        """ creates test file from settings """
        file_name = cls._backing_file()
        if os.path.exists(file_name):
            os.remove(file_name)

        cls._file_name_override = None
        cls.destroy()
        cls.__disabled = False

    @classmethod
    def _backing_file(cls, create_if_missing=False):
        """ return path to test file to use for backing purposes """
        backing_file_name = getattr(settings, "MOCK_SEARCH_BACKING_FILE", None)
        if cls._file_name_override:
            backing_file_name = cls._file_name_override

        if not backing_file_name:
            cls._disabled = False
            return None

        if create_if_missing or os.path.exists(backing_file_name):
            cls._disabled = False
            return backing_file_name

        cls._disabled = True
        return None

    @classmethod
    def _write_to_file(cls, create_if_missing=False):
        """ write the index dict to the backing file """
        file_name = cls._backing_file(create_if_missing)
        if file_name:
            with open(file_name, "w+") as dict_file:
                json.dump(cls._mock_elastic, dict_file, cls=DjangoJSONEncoder)

    @classmethod
    def _load_from_file(cls):
        """ load the index dict from the contents of the backing file """
        file_name = cls._backing_file()
        if file_name and os.path.exists(file_name):
            with open(file_name, "r") as dict_file:
                cls._mock_elastic = json.load(dict_file)

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
    def load_index(cls, index_name):
        """ load the index, if necessary from the backed file """
        cls._load_from_file()
        if index_name not in cls._mock_elastic:
            cls._mock_elastic[index_name] = []
            cls._write_to_file()

        return cls._mock_elastic[index_name]

    @classmethod
    def add_documents(cls, index_name, sources):
        """
        Add documents to index.
        """
        cls.load_index(index_name).extend(sources)
        cls._write_to_file()

    @classmethod
    def remove_documents(cls, index_name, doc_ids):
        """
        Remove documents by id from index.
        """
        index = cls.load_index(index_name)
        cls._mock_elastic[index_name] = [d for d in index if "id" not in d or d["id"] not in doc_ids]
        cls._write_to_file()

    @classmethod
    def destroy(cls):
        """ Clean out the dictionary for test resets """
        cls._mock_elastic = {}
        cls._write_to_file()

    def __init__(self, index=None):
        super(MockSearchEngine, self).__init__(index)
        MockSearchEngine.load_index(self.index_name)

    def index(self, sources, **kwargs):
        """
        Add/update documents to the index.
        """
        if not MockSearchEngine._disabled:
            doc_ids = [s["id"] for s in sources if "id" in s]
            MockSearchEngine.remove_documents(self.index_name, doc_ids)
            MockSearchEngine.add_documents(self.index_name, sources)

    def remove(self, doc_ids):  # pylint: disable=arguments-differ
        """
        Remove documents with given ids from the index.
        """
        if not MockSearchEngine._disabled:
            MockSearchEngine.remove_documents(self.index_name, doc_ids)

    def search(self,
               query_string=None,
               field_dictionary=None,
               filter_dictionary=None,
               exclude_dictionary=None,
               aggregation_terms=None,
               **kwargs):  # pylint: disable=too-many-arguments
        """
        Perform search upon documents within index.
        """
        if MockSearchEngine._disabled:
            return {
                "took": 10,
                "total": 0,
                "max_score": 0,
                "results": []
            }

        documents_to_search = []
        index = MockSearchEngine.load_index(self.index_name)
        documents_to_search.extend(index)

        if field_dictionary:
            documents_to_search = _filter_intersection(documents_to_search, field_dictionary)

        if filter_dictionary:
            documents_to_search = _filter_intersection(documents_to_search, filter_dictionary, True)

        if query_string:
            documents_to_search = _process_query_string(documents_to_search, query_string)

        # Support deprecated argument of exclude_ids
        if "exclude_ids" in kwargs:
            if not exclude_dictionary:
                exclude_dictionary = {}
            if "id" not in exclude_dictionary:
                exclude_dictionary["id"] = []
            exclude_dictionary["id"].extend(kwargs["exclude_ids"])

        if exclude_dictionary:
            documents_to_search = _process_exclude_dictionary(documents_to_search, exclude_dictionary)

        # Finally, find duplicates and give them a higher score
        def score_documents(documents_to_search):
            """ Apply scoring to documents that have multiple matches """
            search_results = []
            max_score = 0
            while documents_to_search:
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
            return search_results, max_score

        search_results, max_score = score_documents(documents_to_search)

        results = MockSearchEngine._paginate_results(
            kwargs["size"] if "size" in kwargs else None,
            kwargs["from_"] if "from_" in kwargs else None,
            sorted(search_results, key=lambda k: k["score"])
        )

        response = {
            "took": 10,
            "total": len(search_results),
            "max_score": max_score,
            "results": results
        }

        if aggregation_terms:
            response["aggs"] = _count_aggregated_values(documents_to_search, aggregation_terms)

        return response
