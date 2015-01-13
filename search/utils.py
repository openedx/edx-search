""" Utility classes to support others """
import importlib


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


class ValueRange(object):

    def __init__(self, lower=None, upper=None):
        self._lower = lower
        self._upper = upper

    @property
    def upper(self):
        return self._upper

    @property
    def lower(self):
        return self._lower

    @property
    def upper_string(self):
        return str(self._upper)

    @property
    def lower_string(self):
        return str(self._lower)


class DateRange(ValueRange):

    @property
    def upper_string(self):
        return self._upper.isoformat()

    @property
    def lower_string(self):
        return self._lower.isoformat()
