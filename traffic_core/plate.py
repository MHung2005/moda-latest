"""Pure license-plate grouping, validation and selection logic."""

from __future__ import annotations

from difflib import SequenceMatcher
import re
from typing import Sequence


Box = list[object]


def _intersection_over_union(left: Sequence[object], right: Sequence[object]) -> float:
    x1 = max(float(left[0]), float(right[0]))
    y1 = max(float(left[1]), float(right[1]))
    x2 = min(float(left[2]), float(right[2]))
    y2 = min(float(left[3]), float(right[3]))

    intersection = max(0.0, x2 - x1) * max(0.0, y2 - y1)
    left_area = (float(left[2]) - float(left[0])) * (
        float(left[3]) - float(left[1])
    )
    right_area = (float(right[2]) - float(right[0])) * (
        float(right[3]) - float(right[1])
    )
    union = left_area + right_area - intersection
    return intersection / union if union > 0 else 0.0


def _filter_overlaps(boxes: Sequence[Box]) -> list[Box]:
    filtered: list[Box] = []
    for box in sorted(boxes, key=lambda value: float(value[4]), reverse=True):
        if all(_intersection_over_union(box, existing) <= 0.5 for existing in filtered):
            filtered.append(box)
    return filtered


def group_boxes_with_height(lp_box: Sequence[object], char_boxes: Sequence[Box]) -> list[list[Box]]:
    """Group character boxes into plate rows using the legacy height heuristic."""

    groups: list[list[Box]] = []
    mean_heights: list[float] = []
    for box in sorted(_filter_overlaps(char_boxes), key=lambda value: int(value[1])):
        box_y2 = abs(float(box[3]) - float(lp_box[1]))
        matching_group: int | None = None
        for index, mean_height in enumerate(mean_heights):
            maximum = max(box_y2, mean_height)
            if maximum and min(box_y2, mean_height) / maximum >= 0.8:
                matching_group = index
                break

        if matching_group is None:
            groups.append([box])
            mean_heights.append(box_y2)
        else:
            groups[matching_group].append(box)
            mean_heights[matching_group] = (
                box_y2 + mean_heights[matching_group]
            ) / 2
    return groups


def merge_box(lp_boxes: Sequence[Box], char_boxes: Sequence[Box]) -> dict[int, dict[str, object]]:
    """Associate character detections with their parent tracked vehicle."""

    merged: dict[int, dict[str, object]] = {}
    for lp_box in lp_boxes:
        object_id = int(lp_box[-1])
        related = [box[:-1] for box in char_boxes if int(box[-1]) == object_id]
        rows = group_boxes_with_height(lp_box[:-2], related)

        labels: list[str] = []
        confidences: list[float] = []
        for row in rows:
            ordered = sorted(row, key=lambda value: int(value[0]))
            labels.append("".join(str(box[-1]) for box in ordered))
            confidences.extend(float(box[-2]) for box in ordered)
        merged[object_id] = {"label": labels, "confidence": confidences}
    return merged


def update_vehicle_box(
    tracker_boxes: list[Box], grouped_plates: dict[int, dict[str, object]]
) -> list[Box]:
    """Attach a normalized plate label to each tracked vehicle detection."""

    for vehicle_box in tracker_boxes:
        object_id = int(vehicle_box[5])
        plate = grouped_plates.get(object_id)
        normalized: dict[str, object] = {
            "label": "",
            "confidence": [],
        }
        if plate is not None:
            rows = list(plate.get("label", []))
            normalized = {
                "label": "".join(rows),
                "confidence": list(plate.get("confidence", [])),
            }
        vehicle_box.append(normalized)
    return tracker_boxes


_LICENSE_PLATE_PATTERNS = tuple(
    re.compile(pattern)
    for pattern in (
        r"^\d{2}[A-Z]\d{4,5}$",
        r"^\d{2}[A-Z]{2}\d{5}$",
        r"^(NG|NN)\d{5}$",
        r"^[A-Z]{2}\d{4,5}$",
    )
)


def is_valid_license_plate_no_hyphen(plate: str) -> bool:
    return any(pattern.fullmatch(plate) for pattern in _LICENSE_PLATE_PATTERNS)


def sequence_similarity(plate: str | None, reference: str | None) -> float:
    if plate is None or reference is None:
        return 0.0
    return SequenceMatcher(None, plate, reference).ratio()


def find_best_match(plate: str | None, reference_list: Sequence[str | None]) -> tuple[str, float]:
    references = [reference for reference in reference_list if reference]
    if not plate or not references:
        return "", 0.0
    best = max(references, key=lambda reference: sequence_similarity(plate, reference))
    return best, sequence_similarity(plate, best)


def select_best_license_plate(
    license_plates: Sequence[str],
    confidences: Sequence[Sequence[float]],
    threshold: float = 0.8,
) -> tuple[str | None, list[float] | None, bool]:
    """Build a plate from the highest-confidence character at each position."""

    valid_pairs = [
        (plate, list(scores))
        for plate, scores in zip(license_plates, confidences)
        if plate and len(scores) >= len(plate)
    ]
    if not valid_pairs:
        return None, None, False

    max_length = max(len(plate) for plate, _ in valid_pairs)
    same_length = [pair for pair in valid_pairs if len(pair[0]) == max_length]

    best_characters: list[str] = []
    best_confidences: list[float] = []
    for position in range(max_length):
        plate, scores = max(same_length, key=lambda pair: pair[1][position])
        best_characters.append(plate[position])
        best_confidences.append(float(scores[position]))

    requires_search = any(score < threshold for score in best_confidences)
    return "".join(best_characters), best_confidences, requires_search
