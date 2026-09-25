from .ab_test import ABTest
from .survey import Survey
from .first_click import FirstClick
from .card_sort import CardSort
from .tree_test import TreeTest

MODULE_TYPES = {cls.type_name: cls for cls in (Survey, ABTest, FirstClick, CardSort, TreeTest)}
