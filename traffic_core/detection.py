"""Vehicle, plate and event orchestration for all Moda deployments."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import logging
from pathlib import Path as FilePath
import platform
import time

import cv2
import gi
import numpy as np
import pyds

gi.require_version("Gst", "1.0")
from gi.repository import Gst

from .config import SiteConfig
from .events import build_vehicle_message
from .fps import FpsCounter
from .geometry import contains_point
from .kafka import create_producer, publish
from .plate import (
    find_best_match,
    is_valid_license_plate_no_hyphen,
    merge_box,
    select_best_license_plate,
    update_vehicle_box,
)
from .deepstream import DeepStreamPipeline
from .tracking import TrackKey, TrackState, TrackingRegistry


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class _VehicleObservation:
    box: tuple[int, int, int, int]
    class_id: int
    object_id: int
    confidence: float
    plate_label: str
    plate_confidences: tuple[float, ...]

    @classmethod
    def from_merged_box(cls, value: list[object]) -> "_VehicleObservation":
        plate = value[-1]
        if not isinstance(plate, dict):
            raise ValueError("Merged vehicle detection has no plate metadata")
        return cls(
            box=(int(value[0]), int(value[1]), int(value[2]), int(value[3])),
            class_id=int(value[4]),
            object_id=int(value[5]),
            confidence=float(value[6]),
            plate_label=str(plate.get("label", "")),
            plate_confidences=tuple(plate.get("confidence", [])),
        )


class VehicleDetection(DeepStreamPipeline):
    PRIMARY_COMPONENT_ID = 1
    PLATE_COMPONENT_ID = 2
    CHARACTER_COMPONENT_ID = 3
    SKIP_SECONDARY_COMPONENT_ID = 9999
    TARGET_VEHICLE_CLASS_ID = 0
    CHARACTER_CONFIDENCE = 0.5
    EXIT_FRAME_GAP = 5
    TRACK_EXPIRY_SECONDS = 600
    FIRST_SYNTHETIC_OBJECT_ID = 90000

    def __init__(self, site_config: SiteConfig):
        logger.info("Initializing vehicle detection for %s", site_config.name)
        super().__init__()
        self.tracks = TrackingRegistry()
        self.producer = create_producer(site_config.kafka_bootstrap_servers)
        self.tracker_config = site_config.tracker_config_path
        self.pgie_config = site_config.vehicle_detection_config_path
        self.sgie_config = site_config.plate_detection_config_path
        self.tgie_config = site_config.plate_recognition_config_path
        self._build_pipeline(list(site_config.camera_urls))
        self.pending_events: set[TrackKey] = set()
        self.next_reentry_object_id = self.FIRST_SYNTHETIC_OBJECT_ID
        self.reference_license_plates = [
            line.strip()
            for line in FilePath(site_config.reference_plate_path)
            .read_text(encoding="utf-8")
            .splitlines()
            if line.strip()
        ]
        self.fps_streams = {
            f"stream{source_id}": FpsCounter(source_id)
            for source_id in range(len(site_config.camera_urls))
        }
        self.vehicle_confidence = site_config.vehicle_confidence
        self.plate_character_confidence = site_config.plate_character_confidence
        self.plate_collection_seconds = site_config.plate_collection_seconds
        self.output_topic = site_config.output_topic
        self.output_partition = site_config.output_partition
        self.publish_timeout_seconds = site_config.publish_timeout_seconds
        self.video_fps = site_config.video_fps
        self.vehicle_image_root = site_config.vehicle_image_root
        self.local_timezone = timezone(timedelta(hours=site_config.utc_offset_hours))
        self.trigger_codes = dict(site_config.trigger_codes)
        self.capture_zones = {
            key: list(value) for key, value in site_config.capture_zones.items()
        }
        self.inference_zones = {
            key: list(value) for key, value in site_config.inference_zones.items()
        }

    def _save_image_and_publish(
        self,
        key: TrackKey,
        state: TrackState,
        *,
        plate_number: str | None,
        best_match: str,
        match_score: float | str,
        requires_search: bool,
    ):
        if (
            state.capture_frame is None
            or state.capture_image is None
            or state.captured_at is None
        ):
            raise RuntimeError(f"Track {key} has no complete capture")

        trigger_code = self.trigger_codes[key.source_id]
        image_name = (
            f"{time.time_ns()}_{key.source_id}_{key.object_id}_"
            f"{plate_number}_{trigger_code}.jpg"
        )
        current_date = datetime.now(self.local_timezone).strftime("%Y%m%d")
        folder_path = FilePath(self.vehicle_image_root) / current_date
        folder_path.mkdir(parents=True, exist_ok=True)

        image_path = folder_path / image_name
        if not cv2.imwrite(str(image_path), state.capture_image):
            raise IOError(f"Failed to save vehicle image: {image_path}")

        message = build_vehicle_message(
            source_id=key.source_id,
            trigger_code=trigger_code,
            section_id=state.section_id,
            plate_number=plate_number,
            image_path=str(image_path),
            timestamp=state.captured_at,
            frame_number=state.capture_frame,
            video_fps=self.video_fps,
            raw_plate=state.raw_plate,
            best_match=best_match,
            match_score=match_score,
            requires_search=requires_search,
        )

        publish(
            self.producer,
            self.output_topic,
            self.output_partition,
            message,
            timeout_seconds=self.publish_timeout_seconds,
        )
        return message

    @staticmethod
    def _get_center(box):
        x1, y1, x2, y2 = box
        return ((x1 + x2) // 2, y2)

    @staticmethod
    def _utc_timestamp() -> str:
        return (
            datetime.now(timezone.utc)
            .isoformat(timespec="milliseconds")
            .replace("+00:00", "Z")
        )

    @staticmethod
    def _get_frame(gst_buffer, frame_meta):
        n_frame = pyds.get_nvds_buf_surface(hash(gst_buffer), frame_meta.batch_id)
        frame_copy = np.array(n_frame, copy=True, order="C")
        if platform.machine() == "aarch64":
            # Jetson maps the NvBufSurface for CPU access; release it once copied.
            pyds.unmap_nvds_buf_surface(hash(gst_buffer), frame_meta.batch_id)
        frame_copy = frame_copy[:, :, :-1]
        frame_copy = frame_copy[:, :, ::-1]
        return np.array(frame_copy)

    @staticmethod
    def _is_inside_polygon(point, polygon):
        return contains_point(point, polygon)

    def _flush_pending_events(self):
        """Publish all captured tracks before shutdown."""
        for key in list(self.pending_events):
            try:
                self._publish_track(key)
            except Exception as error:
                logger.error("Failed to publish pending track %s: %s", key, error)

    def _filter_inference_objects(self, pad, info, u_data):
        gst_buffer = info.get_buffer()
        if not gst_buffer:
            logger.error("Unable to get GstBuffer")
            return Gst.PadProbeReturn.OK
        batch_meta = pyds.gst_buffer_get_nvds_batch_meta(hash(gst_buffer))
        l_frame = batch_meta.frame_meta_list
        while l_frame is not None:
            try:
                frame_meta = pyds.NvDsFrameMeta.cast(l_frame.data)
            except StopIteration:
                break
            l_obj = frame_meta.obj_meta_list
            while l_obj is not None:
                try:
                    obj_meta = pyds.NvDsObjectMeta.cast(l_obj.data)
                except StopIteration:
                    break
                component_id = obj_meta.unique_component_id
                if component_id == self.PRIMARY_COMPONENT_ID:
                    left = int(obj_meta.rect_params.left)
                    top = int(obj_meta.rect_params.top)
                    right = int(obj_meta.rect_params.left + obj_meta.rect_params.width)
                    bottom = int(obj_meta.rect_params.top + obj_meta.rect_params.height)
                    center_foot = self._get_center([left, top, right, bottom])
                    source_id = int(frame_meta.source_id)
                    if not self._is_inside_polygon(
                        center_foot,
                        self.inference_zones[source_id],
                    ):
                        obj_meta.unique_component_id = self.SKIP_SECONDARY_COMPONENT_ID
                try:
                    l_obj = l_obj.next
                except StopIteration:
                    break
            try:
                l_frame = l_frame.next
            except StopIteration:
                break
        return Gst.PadProbeReturn.OK

    def _extract_frame_detections(self, frame_meta):
        plate_ids = {0, 1}
        tracker_boxes, plate_boxes, character_boxes = [], [], []

        object_list = frame_meta.obj_meta_list
        while object_list is not None:
            try:
                obj_meta = pyds.NvDsObjectMeta.cast(object_list.data)
            except StopIteration:
                break

            component_id = obj_meta.unique_component_id
            x1 = int(obj_meta.rect_params.left)
            y1 = int(obj_meta.rect_params.top)
            x2 = int(obj_meta.rect_params.left + obj_meta.rect_params.width)
            y2 = int(obj_meta.rect_params.top + obj_meta.rect_params.height)

            if component_id in {
                self.PRIMARY_COMPONENT_ID,
                self.SKIP_SECONDARY_COMPONENT_ID,
            } and obj_meta.class_id == self.TARGET_VEHICLE_CLASS_ID:
                tracker_boxes.append(
                    [x1, y1, x2, y2, obj_meta.class_id, obj_meta.object_id, obj_meta.confidence]
                )
            elif (
                component_id == self.PLATE_COMPONENT_ID
                and obj_meta.class_id in plate_ids
            ):
                if obj_meta.parent is not None:
                    plate_boxes.append(
                        [
                            x1,
                            y1,
                            x2,
                            y2,
                            obj_meta.object_id,
                            obj_meta.parent.object_id,
                        ]
                    )
            elif (
                component_id == self.CHARACTER_COMPONENT_ID
                and float(obj_meta.confidence) > self.CHARACTER_CONFIDENCE
            ):
                parent = obj_meta.parent
                vehicle_parent = parent.parent if parent is not None else None
                if vehicle_parent is not None:
                    character_boxes.append(
                        [
                            x1,
                            y1,
                            x2,
                            y2,
                            obj_meta.confidence,
                            obj_meta.obj_label,
                            vehicle_parent.object_id,
                        ]
                    )

            try:
                object_list = object_list.next
            except StopIteration:
                break

        grouped_plates = merge_box(plate_boxes, character_boxes)
        merged_vehicles = update_vehicle_box(tracker_boxes, grouped_plates)
        return [
            _VehicleObservation.from_merged_box(vehicle)
            for vehicle in merged_vehicles
        ]

    def _process_vehicle_observation(
        self,
        vehicle: _VehicleObservation,
        gst_buffer,
        frame_meta,
    ):
        if (
            vehicle.class_id != self.TARGET_VEHICLE_CLASS_ID
            or vehicle.confidence <= self.vehicle_confidence
        ):
            return

        source_id = int(frame_meta.source_id)
        key = TrackKey(source_id, vehicle.object_id)
        frame_number = frame_meta.frame_num
        box = list(vehicle.box)
        center_foot = self._get_center(box)
        plate_label = vehicle.plate_label
        plate_confidences = list(vehicle.plate_confidences)
        now = time.time()

        state = self.tracks.ensure(key)
        state.observe(now)

        state.update_raw_plate(plate_label)

        valid_plate = is_valid_license_plate_no_hyphen(plate_label)
        if valid_plate:
            state.add_plate(plate_label, plate_confidences)

        if state.active_reentry_id is not None:
            reassigned_key = TrackKey(source_id, state.active_reentry_id)
            reassigned_state = self.tracks.get(reassigned_key)
            if reassigned_state is not None:
                reassigned_state.observe(now)
                if valid_plate:
                    reassigned_state.add_plate(plate_label, plate_confidences)

        is_inside = self._is_inside_polygon(
            center_foot,
            self.capture_zones[source_id],
        )
        if is_inside:
            if not state.has_capture:
                state.record_capture(
                    image=self._get_frame(gst_buffer, frame_meta),
                    frame_number=frame_number,
                    timestamp=self._utc_timestamp(),
                    started_at=now,
                )
                state.reentry_armed = True
                self.pending_events.add(key)

        did_reenter = state.observe_zone(
            is_inside=is_inside,
            frame_number=frame_number,
            exit_frame_gap=self.EXIT_FRAME_GAP,
        )
        if not did_reenter:
            return

        reassigned_key = TrackKey(source_id, self.next_reentry_object_id)
        self.next_reentry_object_id += 1
        state.active_reentry_id = reassigned_key.object_id

        reassigned_state = self.tracks.ensure(reassigned_key)
        reassigned_state.parent_key = key
        reassigned_state.observe(now)
        reassigned_state.record_capture(
            image=self._get_frame(gst_buffer, frame_meta),
            frame_number=frame_number,
            timestamp=self._utc_timestamp(),
            started_at=now,
        )

        self.pending_events.add(reassigned_key)

    def _publish_track(self, key: TrackKey):
        state = self.tracks.get(key)
        if state is None:
            self.pending_events.discard(key)
            return

        best_plate, _, requires_search = select_best_license_plate(
            state.plate_values,
            state.plate_confidences,
            self.plate_character_confidence,
        )

        best_match, score = "", ""
        if best_plate and self.reference_license_plates:
            best_match, score = find_best_match(
                best_plate,
                self.reference_license_plates,
            )
        elif state.raw_plate:
            best_match, score = find_best_match(
                state.raw_plate,
                self.reference_license_plates,
            )

        self._save_image_and_publish(
            key,
            state,
            plate_number=best_plate,
            best_match=best_match,
            match_score=score,
            requires_search=requires_search,
        )

        original_key = state.parent_key
        if original_key is not None:
            original_state = self.tracks.get(original_key)
            if original_state is not None:
                original_state.active_reentry_id = None
                original_state.reentry_armed = True
        self.pending_events.discard(key)

    def _publish_ready_objects(self, now):
        for key in list(self.pending_events):
            state = self.tracks.get(key)
            if state is None:
                self.pending_events.discard(key)
                continue
            if state.collection_started_at is None:
                continue
            if now - state.collection_started_at > self.plate_collection_seconds:
                try:
                    self._publish_track(key)
                except Exception:
                    # Keep the track pending so a transient Kafka failure does
                    # not permanently break the detector -> analysis chain.
                    logger.exception("Could not publish completed track %s", key)

    def _expire_tracking_data(self, now):
        for key in list(self.tracks):
            state = self.tracks.get(key)
            if state is None:
                continue
            if (
                state.last_seen_at is None
                or now - state.last_seen_at <= self.TRACK_EXPIRY_SECONDS
            ):
                continue

            self.pending_events.discard(key)
            self.tracks.remove(key)

    def _process_inference_results(self, pad, info, u_data):
        gst_buffer = info.get_buffer()
        if not gst_buffer:
            logger.error("Unable to get GstBuffer")
            return Gst.PadProbeReturn.OK

        batch_meta = pyds.gst_buffer_get_nvds_batch_meta(hash(gst_buffer))
        frame_list = batch_meta.frame_meta_list
        while frame_list is not None:
            try:
                frame_meta = pyds.NvDsFrameMeta.cast(frame_list.data)
            except StopIteration:
                break

            if self.watchdog is not None:
                self.watchdog.mark(int(frame_meta.source_id))
            for vehicle in self._extract_frame_detections(frame_meta):
                self._process_vehicle_observation(vehicle, gst_buffer, frame_meta)

            now = time.time()
            self._publish_ready_objects(now)
            self._expire_tracking_data(now)
            self.fps_streams[f"stream{frame_meta.pad_index}"].get_fps()

            try:
                frame_list = frame_list.next
            except StopIteration:
                break

        return Gst.PadProbeReturn.OK

    def run(self):
        try:
            self._run_pipeline(
                tracker_probe=self._filter_inference_objects,
                inference_probe=self._process_inference_results,
            )
        except Exception:
            logger.error("DeepStream main loop failed")
            raise
        finally:
            try:
                self._flush_pending_events()
            except Exception as error:
                logger.error("Failed to flush pending tracks: %s", error)
            try:
                self.producer.flush(timeout=10)
            except Exception as error:
                logger.error("Failed to flush Kafka producer: %s", error)
            try:
                self.producer.close(timeout=10)
            except Exception as error:
                logger.error("Failed to close Kafka producer: %s", error)
