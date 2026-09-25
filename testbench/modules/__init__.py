from .ab_test import ABTest
from .survey import Survey

MODULE_TYPES = {cls.type_name: cls for cls in (Survey, ABTest)}
