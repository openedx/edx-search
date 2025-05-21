"""
Defines the dataclasses used in the search module.
"""
from dataclasses import dataclass
from enum import Enum
from typing import Literal


class FieldTypes(Enum):
    """
    Enum for field types.
    """
    STRING = "string"
    NUMBER = "number"
    BOOLEAN = "boolean"
    DATE = "date"
    DATETIME = "datetime"


@dataclass
class SortField:
    """
    Represents a field used for sorting in search results.
    """
    name: str
    type_: FieldTypes
    order: Literal["asc", "desc"] = "asc"
