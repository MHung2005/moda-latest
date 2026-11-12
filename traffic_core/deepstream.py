"""DeepStream/GStreamer pipeline shared by every Moda deployment."""

from __future__ import annotations

import configparser
import ctypes
import logging
import os

import gi


GST_HELPER_LIB = ctypes.CDLL(
    "/opt/nvidia/deepstream/deepstream/lib/libnvdsgst_helper.so"
)

gi.require_version("Gst", "1.0")
from gi.repository import GObject, Gst


logger = logging.getLogger(__name__)


class DeepStreamPipeline:
    """Build and manage the fixed vehicle/plate recognition pipeline."""

    MAX_SOURCES = 4
    IMAGE_WIDTH = 2688
    IMAGE_HEIGHT = 1520

    def __init__(self) -> None:
        self.tracker_config = ""
        self.pgie_config = ""
        self.sgie_config = ""
        self.tgie_config = ""

    def _add_sources(self, uri_names: list[str]) -> None:
        for source_id, uri in enumerate(uri_names):
            logger.info("Creating source %s", source_id)
            source_bin = self._create_source_bin(source_id, uri)
            self.g_source_bin_list[source_id] = source_bin
            self.g_num_sources += 1
            self.pipeline.add(source_bin)

        is_live = any(uri.startswith("rtsp://") for uri in uri_names)
        self.streammux.set_property("live-source", int(is_live))

    def _remove_source(self, source_id: int) -> bool:
        """Stop one source and release its streammux request pad exactly once."""
        source_bin = self.pipeline.get_by_name(f"source-bin-{source_id:02d}")
        if source_bin is None:
            logger.warning("Source %s is already absent", source_id)
            return False

        state_return = source_bin.set_state(Gst.State.NULL)
        if state_return == Gst.StateChangeReturn.FAILURE:
            logger.error("Failed to stop source %s", source_id)
            return False
        if state_return == Gst.StateChangeReturn.ASYNC:
            source_bin.get_state(Gst.CLOCK_TIME_NONE)

        sink_pad = self.streammux.get_static_pad(f"sink_{source_id}")
        if sink_pad is not None:
            sink_pad.send_event(Gst.Event.new_flush_stop(False))
            self.streammux.release_request_pad(sink_pad)

        self.pipeline.remove(source_bin)
        self.g_source_bin_list[source_id] = None
        self.g_num_sources = max(0, self.g_num_sources - 1)
        return True

    def _stop_all_sources(self) -> None:
        for source_id, source_bin in enumerate(list(self.g_source_bin_list)):
            if source_bin is not None:
                self._remove_source(source_id)

    def _on_decode_pad_added(self, decodebin, pad, source_id: int) -> None:
        caps = pad.get_current_caps() or pad.query_caps(None)
        if caps is None:
            raise RuntimeError(f"Source {source_id} produced a pad without capabilities")

        media_type = caps.get_structure(0).get_name()
        if "video" not in media_type:
            return

        sink_pad = self.streammux.get_request_pad(f"sink_{source_id}")
        if sink_pad is None:
            raise RuntimeError(f"Unable to request streammux pad for source {source_id}")
        if pad.link(sink_pad) != Gst.PadLinkReturn.OK:
            raise RuntimeError(f"Unable to link source {source_id} to streammux")

    def _on_decode_child_added(
        self,
        child_proxy,
        child,
        name,
        source_id: int,
    ) -> None:
        if "decodebin" in name:
            child.connect("child-added", self._on_decode_child_added, source_id)

    def _create_source_bin(self, source_id: int, uri: str):
        bin_name = f"source-bin-{source_id:02d}"
        source_bin = Gst.ElementFactory.make("uridecodebin", bin_name)
        if source_bin is None:
            raise RuntimeError(f"Unable to create URI decode bin for source {source_id}")

        source_bin.set_property("uri", uri)
        source_bin.connect("pad-added", self._on_decode_pad_added, source_id)
        source_bin.connect("child-added", self._on_decode_child_added, source_id)
        source_bin.connect("source-setup", self._on_source_setup, source_id)
        return source_bin

    @staticmethod
    def _on_source_setup(_uridecodebin, source, source_id: int) -> None:
        # Cameras sit behind NAT/Internet: UDP never arrives and rtspsrc wastes 5 s per
        # (re)connect before falling back. Default to TCP; MODA_RTSP_PROTOCOLS overrides
        # (e.g. "udp+tcp" or "" to keep the GStreamer default).
        protocols = os.environ.get("MODA_RTSP_PROTOCOLS", "tcp").strip()
        if not protocols or source.find_property("protocols") is None:
            return
        Gst.util_set_object_arg(source, "protocols", protocols)
        logger.info("Source %d: RTSP protocols=%s", source_id, protocols)

    def _on_bus_message(self, bus, message, loop) -> bool:
        message_type = message.type
        if message_type == Gst.MessageType.EOS:
            reason = "End-of-Stream received. Pipeline is shutting down."
            logger.info(reason)
            self._stop_all_sources()
            loop.quit()
        elif message_type == Gst.MessageType.WARNING:
            error, debug = message.parse_warning()
            warning = f"Warning: {error}: {debug}"
            logger.warning(warning)
        elif message_type == Gst.MessageType.ERROR:
            error, debug = message.parse_error()
            failure = f"Error: {error}: {debug}"
            logger.error(failure)
            self._stop_all_sources()
            loop.quit()
        elif message_type == Gst.MessageType.ELEMENT:
            structure = message.get_structure()
            if structure is not None and structure.has_name("stream-eos"):
                parsed, stream_id = structure.get_uint("stream-id")
                if parsed:
                    reason = f"Stream EOS for stream {stream_id}; removing source."
                    logger.info(reason)
                    self._remove_source(stream_id)
                    if self.g_num_sources == 0:
                        loop.quit()
        return True

    def _run_pipeline(self, tracker_probe, inference_probe) -> None:
        """Attach domain probes and run until the GStreamer loop stops."""
        loop = GObject.MainLoop()
        bus = self.pipeline.get_bus()
        bus.add_signal_watch()
        bus.connect("message", self._on_bus_message, loop)

        tracker_pad = self.tracker.get_static_pad("src")
        if tracker_pad is None:
            raise RuntimeError("Unable to get tracker src pad")
        tracker_pad.add_probe(Gst.PadProbeType.BUFFER, tracker_probe, None)

        inference_pad = self.tgie.get_static_pad("src")
        if inference_pad is None:
            raise RuntimeError("Unable to get character inference src pad")
        inference_pad.add_probe(Gst.PadProbeType.BUFFER, inference_probe, None)

        state_change = self.pipeline.set_state(Gst.State.PLAYING)
        if state_change == Gst.StateChangeReturn.FAILURE:
            raise RuntimeError("DeepStream pipeline failed to enter PLAYING state")
        try:
            loop.run()
        finally:
            self.pipeline.set_state(Gst.State.NULL)

    def _add_elements(self, elements: list) -> None:
        for element in elements:
            self.pipeline.add(element)

    def _link_elements(self, elements: list) -> None:
        for current, following in zip(elements, elements[1:]):
            if not current.link(following):
                raise RuntimeError(
                    f"Failed to link {current.get_name()} to {following.get_name()}"
                )

    @staticmethod
    def _make_element(factory_name: str, name: str, display_name: str):
        element = Gst.ElementFactory.make(factory_name, name)
        if element is None:
            raise RuntimeError(f"Unable to create {display_name} ({factory_name})")
        return element

    def _configure_tracker(self) -> None:
        parser = configparser.ConfigParser()
        loaded_files = parser.read(self.tracker_config)
        if not loaded_files or "tracker" not in parser:
            raise RuntimeError(f"Invalid tracker configuration: {self.tracker_config}")

        integer_properties = {
            "tracker-width": "tracker-width",
            "tracker-height": "tracker-height",
            "enable-past-frame": "enable_past_frame",
        }
        string_properties = {
            "ll-lib-file": "ll-lib-file",
            "ll-config-file": "ll-config-file",
        }
        for config_name, property_name in integer_properties.items():
            if parser.has_option("tracker", config_name):
                self.tracker.set_property(
                    property_name,
                    parser.getint("tracker", config_name),
                )
        for config_name, property_name in string_properties.items():
            if parser.has_option("tracker", config_name):
                self.tracker.set_property(
                    property_name,
                    parser.get("tracker", config_name),
                )

    def _build_pipeline(self, uri_names: list[str]) -> None:
        if not uri_names:
            raise ValueError("At least one camera URI is required")
        if len(uri_names) > self.MAX_SOURCES:
            raise ValueError(
                f"This pipeline supports at most {self.MAX_SOURCES} camera sources"
            )

        GObject.threads_init()
        Gst.init(None)

        self.pipeline = Gst.Pipeline.new("moda-traffic-pipeline")
        if self.pipeline is None:
            raise RuntimeError("Unable to create the GStreamer pipeline")

        self.g_source_bin_list = [None] * self.MAX_SOURCES
        self.g_num_sources = 0

        queues = [
            self._make_element("queue", f"queue{index}", f"Queue {index}")
            for index in range(1, 8)
        ]
        self.streammux = self._make_element(
            "nvstreammux", "nvstream-muxer", "NvStreamMux"
        )
        self.pgie = self._make_element(
            "nvinfer", "primary-inference", "Primary Nvinfer"
        )
        self.tracker = self._make_element("nvtracker", "tracker", "Tracker")
        self.sgie = self._make_element(
            "nvinfer", "secondary-inference", "Secondary Nvinfer"
        )
        self.tgie = self._make_element(
            "nvinfer", "third-inference", "Third Nvinfer"
        )
        self.nvvidconv = self._make_element(
            "nvvideoconvert", "convertor1", "Nvvideoconvert"
        )
        self.capsfilter = self._make_element(
            "capsfilter", "filter", "Capsfilter"
        )
        self.sink = self._make_element("fakesink", "fakesink", "Fakesink")

        self.streammux.set_property("width", self.IMAGE_WIDTH)
        self.streammux.set_property("height", self.IMAGE_HEIGHT)
        self.streammux.set_property("batch-size", len(uri_names))
        self.streammux.set_property("batched-push-timeout", 40000)
        self.streammux.set_property("attach-sys-ts", 0)

        self.pgie.set_property("config-file-path", self.pgie_config)
        if self.pgie.get_property("batch-size") != len(uri_names):
            self.pgie.set_property("batch-size", len(uri_names))
        self._configure_tracker()
        self.sgie.set_property("config-file-path", self.sgie_config)
        self.tgie.set_property("config-file-path", self.tgie_config)
        self.capsfilter.set_property(
            "caps",
            Gst.Caps.from_string("video/x-raw(memory:NVMM), format=RGBA"),
        )
        self.sink.set_property("qos", 0)

        pipeline_elements = [
            self.streammux,
            *queues,
            self.pgie,
            self.tracker,
            self.sgie,
            self.tgie,
            self.nvvidconv,
            self.capsfilter,
            self.sink,
        ]
        self._add_elements(pipeline_elements)
        self._link_elements(
            [
                self.streammux,
                queues[0],
                self.nvvidconv,
                queues[1],
                self.capsfilter,
                queues[2],
                self.pgie,
                queues[3],
                self.tracker,
                queues[4],
                self.sgie,
                queues[5],
                self.tgie,
                queues[6],
                self.sink,
            ]
        )
        self._add_sources(uri_names)
