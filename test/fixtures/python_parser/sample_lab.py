import math
from pathlib import Path


def calculate_average(values):
    """Return the arithmetic mean of a non-empty list."""
    return sum(values) / len(values)


class GradeBook:
    def is_passing(self, grade):
        return grade >= 50