""" overridable filter object to inject fields to auto-filter upon within searches """
from datetime import datetime

from django.conf import settings

from .utils import _load_class, DateRange


class SearchFilterGenerator(object):
    """

    Class to provide a set of filters for the search.

    Users of this search app will override this class and update setting for:

        PROGRAM_SEARCH_FILTER_GENERATOR

    """

    # disabling pylint violations because overriders will want to use these
    # pylint: disable=unused-argument, no-self-use
    def filter_dictionary(self, **kwargs):
        """ base implementation which filters via nothing """
        return {}

    def field_dictionary(self, **kwargs):
        """ base implementation which filters via nothing """
        return {}

    def exclude_dictionary(self, **kwargs):
        """ base implementation which excludes nothing """
        return {}

    def targeting_gate(self, **kwargs):
        """
        Optional audience-targeting gate for Learning Path catalog discovery.

        See ``search.filter_generator.SearchFilterGenerator.targeting_gate``.
        """
        return None

    @classmethod
    def _load_generator(cls):
        return _load_class(
            getattr(settings, "PROGRAM_SEARCH_FILTER_GENERATOR", None),
            cls
        )()

    @classmethod
    def generate_field_filters(cls, **kwargs):
        """
        Called from within search handler
        Finds desired subclass and adds filter information based upon user information
        """
        generator = cls._load_generator()
        return (
            generator.field_dictionary(**kwargs),
            generator.filter_dictionary(**kwargs),
            generator.exclude_dictionary(**kwargs),
        )

    @classmethod
    def generate_targeting_gate(cls, **kwargs):
        """Return the audience-targeting gate from the configured generator (or None)."""
        return cls._load_generator().targeting_gate(**kwargs)
