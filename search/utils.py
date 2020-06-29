""" Utility classes to support others """

import importlib
import collections


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
    return isinstance(item, collections.Iterable) and not isinstance(item, str)


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
