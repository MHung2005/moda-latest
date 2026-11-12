"""Small geometry helpers used by capture-zone logic."""

from __future__ import annotations

from typing import Sequence


Point = tuple[int, int]


def contains_point(point: Point, polygon: Sequence[Point]) -> bool:
    """Return whether a point is inside a polygon using ray casting."""
    x, y = point
    inside = False
    previous_x, previous_y = polygon[-1]
    for current_x, current_y in polygon:
        crosses_y = (current_y > y) != (previous_y > y)
        if crosses_y:
            crossing_x = (
                (previous_x - current_x)
                * (y - current_y)
                / (previous_y - current_y)
                + current_x
            )
            if x < crossing_x:
                inside = not inside
        previous_x, previous_y = current_x, current_y
    return inside
