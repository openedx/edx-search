""" overridable filter object to inject fields to auto-filter upon within searches """

from datetime import datetime

from django.conf import settings

from .utils import _load_class, DateRange

from typing import Any, Dict, List, Optional, Union

class SearchFilterGenerator:

    """
    Class to provide a set of filters for the search.
    Users of this search app will override this class and update setting for SEARCH_FILTER_GENERATOR
    """
    @staticmethod
    def _normalise_to_list(value: Any) -> List[Any]:
        """
        Return *value* as a list without mutating it if it already is one.
        """
        if isinstance(value, (list, tuple, set)):
            return list(value)
        return [value]

    def filter_dictionary(self, *, field_filters=None, **_kwargs):
        filters = {
            "start_date": DateRange(None, datetime.utcnow())
        }
        if field_filters:
            for field, raw in field_filters.items():
                values = self._normalise_to_list(raw)
                if len(values) == 1:
                    filters[field] = {"term": {f"{field}.keyword": values[0]}}
                else:
                    filters[field] = {"terms": {f"{field}.keyword": values}}
        return filters

    def field_dictionary(self, **kwargs):
        """ base implementation which add course if provided """
        field_dictionary = {}
        if "course_id" in kwargs and kwargs["course_id"]:
            field_dictionary["course"] = kwargs["course_id"]

        return field_dictionary

    def exclude_dictionary(self, **kwargs):
        """ base implementation which excludes nothing """
        return {}

    @classmethod
    def generate_field_filters(cls, **kwargs):
        """
        Called from within search handler
        Finds desired subclass and adds filter information based upon user information
        """
        generator = _load_class(getattr(settings, "SEARCH_FILTER_GENERATOR", None), cls)()
        return (
            generator.field_dictionary(**kwargs),
            generator.filter_dictionary(**kwargs),
            generator.exclude_dictionary(**kwargs),
        )
