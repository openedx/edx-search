""" overridable filter object to inject fields to auto-filter upon within searches """

from datetime import datetime

from django.conf import settings

from .utils import _load_class, DateRange


class SearchFilterGenerator:

    """
    Class to provide a set of filters for the search.
    Users of this search app will override this class and update setting for SEARCH_FILTER_GENERATOR
    """

    # disabling pylint violations because overriders will want to use these
    # pylint: disable=unused-argument, no-self-use
    def filter_dictionary(self, **kwargs):
        """ base implementation which filters via start_date """
        return {"start_date": DateRange(None, datetime.utcnow())}

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
