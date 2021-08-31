# edx-search [![Build Status](https://github.com/edx/edx-search/workflows/Python%20CI/badge.svg?branch=master)](https://github.com/edx/edx-search/actions?query=workflow%3A%22Python+CI%22) [![Coverage Status](https://coveralls.io/repos/edx/edx-search/badge.svg?branch=master&service=github)](https://coveralls.io/github/edx/edx-search?branch=master)

This is a django application to provide access to search services from within edx-platform applications.

Searching is accomplished by creating an index of documents, and then searching within that index for matching information. This application provides a way to add documents to the index, and then search for them.

## SearchEngine
The SearchEngine is an abstract object which may have multiple implementations _(at the time of writing there are 2 in existence - Elasticsearch and MockSearchEngine, which is primarily used for testing)_

To operate with the currently-specified SearchEngine from the django settings, one can invoke a static method on SearchEngine as follows:
```
search_engine = SearchEngine.get_search_engine(index="index_name")
```

Indexing and searching is always performed within the context of a specific index, and the index name is provided within the call to `get_search_engine`

The core operations available within the SearchEngine interface are as follows:
```
search_engine.index(doc_type, body, **kwargs)
search_engine.remove(doc_type, doc_id, **kwargs)
search_engine.search(
    query_string=None,
    field_dictionary=None,
    filter_dictionary=None,
    **kwargs
)
```

where

1. `index` - the operation to add a document to the index

2. `remove` - the operation to remove a document from the index

3. `search` - the operation to find matching documents within the index. `doc_type` is supported as an optional keyword parameter to return results only with a certain doc_type


## Index documents
Index documents are passed to the search application as python dictionaries, along with a `doc_type` document type, which is also optionally supported as a way to return only certain document types from a search.

### Example indexing operation
```
search_engine = SearchEngine.get_search_engine(index="test_index")
test_document = {
    "course_id": "COURSE_ID",
    "id": "object_id",
    "some_attribute": "some_value",
    "nested_attributes": {
        "another_attribute": "another_value",
    },
    "content": {
        "text": "This text will be searched completely"
    },
    "start_date": "2030-01-01T00:00:00+00:00",
}
search_engine.index("test_doc_type", test_document)
```

## Searching for documents
Once documents have been added to the index, there are many ways in which one may want to search for content

1. Search by matching field
2. Search if matching numeric / date field is within a certain range
3. Google-like search matching some text somewhere within the document contents
4. Search by matching field, allowing unspecified results to be included
5. Searches using a combination of these criteria

### Search by matching field
This is accomplished by passing a `field_dictionary` object with the desired values therein:
```
search_engine = SearchEngine.get_search_engine(index="test_index")
match_field_dict = {
    "some_attribute": "some_value"
}
search_result = search_engine.search(field_dictionary=match_field_dict)
```

More than one value can be used in the matching criteria:
```
search_engine = SearchEngine.get_search_engine(index="test_index")
match_field_dict = {
    "some_attribute": "some_value",
    "nested_attributes.another_attribute": "another_value"
}
search_result = search_engine.search(field_dictionary=match_field_dict)
```

_Notice the . notation for querying fields that are nested within the indexed object_

**Important notice:** searching in multivalue fields (i.e. a lists) have a special semantics - if search term is a
scalar value (i.e. string, number, etc.), search uses "contains in" predicate, so all documents containing specified
search value as one of the elements of multivalue field are included in result set. If search term is vector value (i.e. 
list, tuple, dictionary, etc.), search will result in undefined behavior, specific to underlying search engine; thus
using iterable as filter field value is discouraged. 

#### Search results
The `search_result` object returned from a call to `search` is a python dict object that contains the following fields:
```
took
total
max_score
results
```

where:

1. `took` is how many milliseconds the search engine took to process the query
2. `total` is the count of how many matches were found
3. `max_score` is the highest "score" match of the results - one cannot assume that there is an absolute scale of scores, but that a result with a higher score represents a thought to be better match than another result with a lower score
4. `results` is an array of result objects

Each result object is a python dict object that contains the following fields:
```
score
data
```

where:

1. `score` is the relative score of this record compared to others
2. `data` is a copy of the dict object stored within the index 

### Search if matching numeric / date field is within a certain range
This is a very similar situation as the matching field, in this case however the user wishes to find results witin a certain range.
For this purpose, this search app provides 2 classes within its `utils` namespace - `ValueRange` and `DateRage`

```
from search.utils import ValueRange, DateRange

search_engine = SearchEngine.get_search_engine(index="test_index")
range_specification = {
    "age": ValueRange(18, 39)
}
# Check for people that can vote, but cannot become president yet
search_result = search_engine.search(field_dictionary=range_specification)

already_started = {
    "start_date": DateRange(None, datetime.utcnow())
}
# Check for results that have started
search_result = search_engine.search(field_dictionary=already_started)
```

_Note that passing **None** in either range parameter indicates no lower/upper bound is necessary_

### Google-like search matching some text somewhere within the document contents
This is a very popular way to search textual content, looking for matches for a specific word or phrase. Objects will often want to be indexed with field values that may want to be matched, but not matched textually. As a result, this type of search query takes the search term and restricts matches to text found within the `content` subdictionary of any item.
```
search_engine = SearchEngine.get_search_engine(index="test_index")
search_result = search_engine.search(query_string="chocolate")
other_search_result = search_engine.search(query_string="chocolate heart")
```

### Search by matching field, allowing unspecified results to be included
Consider a search for items that are allowed to be viewed up until this date. Many items may include a `start_date` and one would want to look for items that have a `start_date` that is past `now`. However, content that does not specify a `start_date` may be desireable to include.

Another example could be where content specifies a group of users that have access (e.g. cohorting) - content that does not specify a cohort should be visible to users from all cohorts.

In order to accomplish this operation, simply pass the criteria to the `filter_dictionary` parameter instead of `field_dictionary`

```
search_engine = SearchEngine.get_search_engine(index="test_index")
cohort_filter = {
    "cohort": "CohortA"
}
# Check for all objects that members of CohortA can access (with cohort specification for CohortA or no cohort specification)
search_result = search_engine.search(filter_dictionary=cohort_filter)

from search.utils import DateRange
already_started = {
    "start_date": DateRange(None, datetime.utcnow())
}
# Check for results that have started, or don't specifiy a start_date
search_result = search_engine.search(filter_dictionary=already_started)
```

**Important notice:** same concerns about searching in multivalue fields apply here.

### Searches using a combination of these criteria
All of these criteria can be combined to present results that are desired. Consider a search that wants to return objects that:

1. Have a `doc_type` of `courseware_content`
2. Have a textual match for "chocolate"
3. Are within the course with id = "edX/DemoX/Demo_Course"
4. Have already been released - `start_date` is not in the future (if it is specified)

A search like this is accomplished in one call to the search engine:
```
from search.utils import DateRange

search_engine = SearchEngine.get_search_engine(index="test_index")
filter_fields = {
    "start_date": DateRange(None, datetime.utcnow())
}
match_fields = {
    "course": "edX/DemoX/Demo_Course"
}

search_result = search_engine.search(
    query_string="chocolate",
    field_dictionary=match_fields,
    filter_dictionary=filter_fields,
    doc_type="courseware_content"
)
```

_This example is so popular that the search app provides additional facility for performing this search_

## Paging search results
The call to `search_engine.search` also accepts parameters to allow client applications to fetch results from muiltiple "pages" of results. The parameters to use are:

1. `size` - the number of results to return in the page
2. `from_` - the zero-based index of the first result for the page


## Higher Level Operations
The app also provides a higher level operation in order to more easily integrate courseware search

### api.perform_search
```
perform_search(
    search_terms,
    user=None,
    size=10,
    from_=0,
    course_id=None
)
```

perform_search takes the complete search string and optionally a course_id, user, and paging arguments

#### SearchFilterGenerator
SearchFilterGenerator is a class that provides default filter_dictionary and field_dictionary arguments to be used in a search that is used for courseware.

By default, it implements a filter_dictionary with `start_date` filtering, and if a course_id is provided will implement a field_dictionary that matches upon course id.

Users of the `api.perform_search` or search app `views.do_search` view can choose to override the SearchFilterGenerator, providing the override object in django setting named `SEARCH_FILTER_GENERATOR`

### views.do_search
Currently the only http interface to the search app, django applications can use this by including search.urls in their urls.py:
```
url(r'^search/', include('search.urls'))
```

This http endpoint calls `api.perform_search` to interface with the SearchEngine, and allows for clients to add additional fields with values inferred from the results received.

#### SearchResultProcessor
SearchResultProcessor is a class that provides default properties for a result, in particular a text excerpt showing matches from within the text.

Users of the `views.do_search` view can choose to override the SearchResultProcessor, providing the override object in django setting named `SEARCH_RESULT_PROCESSOR`.

In particular, the base SearchResultProcessor adds in each python property alongside the properties within the results. So, the override class simply defines properties that it desires to be exposed alongside the results - for example, the LMS includes an `LmsSearchResultProcessor` that defines properties `url` and `location` which are used to infer the url / location for each search result. In this fashion, the search client can blend in information that it infers from the result fields, but it knows how to format / calculate.

Also, SearchResultProcessor overriders can override the member `should_remove` which allows the client app to determine if access should be excluded to the search result - for example, the LMS includes an implementation of this member that calls the LMS `has_access` as a last safety resort in case the end user does not have access to the result returned.

#### Testing
Tests use an Elasticsearch Docker container. To run tests locally use command:
```
make test_with_es
```
To simply run the container without starting the tests, run:
```
make test.start_elasticsearch
```
To stop an Elasticsearch Docker container, run:
```
make test.stop_elasticsearch
```
