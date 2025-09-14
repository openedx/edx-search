""" Utility classes to support others """
import importlib
import datetime
from collections.abc import Iterable
from typing import Any

from django.utils import timezone


UTC_OFFSET_SUFFIX = "__utcoffset"
IS_NULL_SUFFIX = "__is_null"


def convert_doc_datatypes(doc: dict[str, Any], *, record_nulls=False) -> dict[str, Any]:
    """
    Recursively replace datatypes that our search engine doesn't support, so we
    can store data into the search index.

    This is used by the Typesense engine, and could also be used by Meilisearch
    if we decide to keep both Typesense and Meilisearch.

    - `datetime` values become timestamps.
    - `None` values are removed (Meilisearch ignores them per
      https://www.meilisearch.com/docs/learn/engine/datatypes#null
      and Typesense throws an error if you try to set None/null values on
      some field types, and ignores it on other field types.)

    For Typesense, specify record_nulls=True to insert a second field marking
    fields that have null values - this is the only way to reliably filter for
    fields that are null, which Typesense otherwise doesn't support.
    - https://typesense.org/docs/guide/tips-for-searching-common-types-of-data.html#searching-for-null-or-empty-values
    - https://typesense.org/docs/guide/tips-for-filtering.html#filtering-for-empty-fields
    - https://github.com/typesense/typesense/issues/790
    """
    processed = {}
    for key, value in doc.items():
        if isinstance(value, timezone.datetime):
            # Convert datetime objects to timestamp, and store the timezone in a
            # separate field with a suffix given by UTC_OFFSET_SUFFIX.
            # Most (all?) datetimes are UTC, so the actual offset is not usually
            # super important, but the presence of the offset field is used to
            # detect timestamp fields and convert them back to datetime values
            # when loading search results.
            processed[key] = value.timestamp()
            utcoffset = value.utcoffset().seconds if value.tzinfo else 0
            processed[f"{key}{UTC_OFFSET_SUFFIX}"] = utcoffset
        elif isinstance(value, dict):
            processed[key] = convert_doc_datatypes(value, record_nulls=record_nulls)
        elif value is None:
            if record_nulls:
                processed[f"{key}{IS_NULL_SUFFIX}"] = True
            else:
                continue  # Ignore this NULL value - the search engine will ignore it anyways.
        else:
            # Index the value, unmodified.
            # Pray that there are not datetime objects inside lists.
            # If there are, they will be converted to str by the DocumentEncoder.
            processed[key] = value
    return processed


def restore_doc_datatypes(search_result: dict[str, Any]) -> dict[str, Any]:
    """
    Convert data values from the search index back into the more detailed
    python data types that we want, before displaying results to the user.

    This is the opposite of `convert_doc_datatypes()`.
    """
    processed = {}
    for key, value in search_result.items():
        if key.endswith(UTC_OFFSET_SUFFIX):
            utcoffset = value
            timestamp_key = key[: -len(UTC_OFFSET_SUFFIX)]
            timestamp = search_result[timestamp_key]
            tz = (
                timezone.get_fixed_timezone(timezone.timedelta(seconds=utcoffset))
                if utcoffset
                else None
            )
            processed[timestamp_key] = timezone.datetime.fromtimestamp(timestamp, tz=tz)
        elif key.endswith(IS_NULL_SUFFIX):
            orig_key = key[: -len(IS_NULL_SUFFIX)]
            processed[orig_key] = None
        elif isinstance(value, dict):
            processed[key] = restore_doc_datatypes(value)
        else:
            processed[key] = value
    return processed


def _load_class(class_path, default):
    """ Loads the class from the class_path string """
    if class_path is None:
        return default

    component = class_path.rsplit('.', 1)
    result_processor = getattr(
        importlib.import_module(component[0]),
        component[1],
        default
    ) if len(component) > 1 else default

    return result_processor


def _is_iterable(item):
    """ Checks if an item is iterable (list, tuple, generator), but not string """
    return isinstance(item, Iterable) and not isinstance(item, str)


class ValueRange:

    """ Object to represent a range of values """

    def __init__(self, lower=None, upper=None):
        self._lower = lower
        self._upper = upper

    @property
    def upper(self):
        """ return class member _upper as a proerty value """
        return self._upper

    @property
    def lower(self):
        """ return class member _lower as a proerty value """
        return self._lower

    @property
    def upper_string(self):
        """ return string representation of _upper as a proerty value """
        return str(self._upper)

    @property
    def lower_string(self):
        """ return string representation of _upper as a proerty value """
        return str(self._lower)


class DateRange(ValueRange):

    """ Implemetation of ValueRange for Date """
    @property
    def upper_string(self):
        """ use isoformat for _upper date's string format """
        return self._upper.isoformat()

    @property
    def lower_string(self):
        """ use isoformat for _lower date's string format """
        return self._lower.isoformat()


class Timer:

    """ Simple timer class to measure elapsed time """
    def __init__(self):
        self._start_time = None
        self._end_time = None

    def start(self):
        """ Start the timer """
        self._start_time = datetime.datetime.now()

    def stop(self):
        """ Stop the timer """
        self._end_time = datetime.datetime.now()

    @property
    def start_time(self):
        """ Return the start time """
        return self._start_time

    @property
    def end_time(self):
        """ Return the end time """
        return self._end_time

    @property
    def start_time_string(self):
        """ use isoformat for the start time """
        return self._start_time.isoformat()

    @property
    def end_time_string(self):
        """ use isoformat for the end time """
        return self._end_time.isoformat()

    @property
    def elapsed_time(self):
        """ Return the elapsed time """
        return (self._end_time - self._start_time).seconds


def normalize_bool(value):
    """ Normalize a value to a boolean. """
    if isinstance(value, bool):
        return value

    if isinstance(value, str):
        value = value.lower()
        if value in ('y', 'yes', 't', 'true', 'on', '1'):
            return True
        if value in ('n', 'no', 'f', 'false', 'off', '0'):
            return False

        raise ValueError(f"Invalid truth value: '{value}'")

    return bool(value)
