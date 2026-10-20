""" overridable initializer object used to inject environment settings """

from django.conf import settings

from .utils import _load_class


class SearchInitializer:

    """
    Class to set starting environment parameters for search app.
    Users of this search app will override this class and update setting for SEARCH_INITIALIZER
    """

    def initialize(self, **kwargs):
        """ empty base implementation """

    @classmethod
    def set_search_enviroment(cls, **kwargs):
        """
        Called from within search handler
        Finds desired subclass and calls initialize method
        """
        initializer = _load_class(getattr(settings, "SEARCH_INITIALIZER", None), cls)()
        return initializer.initialize(**kwargs)
