# Detector image for JetPack 6.2 (L4T R36.4) / DeepStream 7.1 / TensorRT 10 / Python 3.10.
# The JetPack 5 image (deepstream-l4t:6.3 + pyds 1.1.8) does not run on L4T R36.
ARG DEEPSTREAM_BASE_IMAGE=nvcr.io/nvidia/deepstream:7.1-triton-multiarch
FROM ${DEEPSTREAM_BASE_IMAGE}

ARG PYDS_WHEEL_URL=https://github.com/NVIDIA-AI-IOT/deepstream_python_apps/releases/download/v1.2.0/pyds-1.2.0-cp310-cp310-linux_aarch64.whl

ENV PYTHONPATH=/app \
    PYTHONUNBUFFERED=1 \
    CUDA_VER=12.6
WORKDIR /app

# DeepStream 7.x images ship without some GStreamer plugins (parsers/codecs used by
# uridecodebin for RTSP); user_additional_install.sh installs them.
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        python3-gi \
        python3-gst-1.0 \
        python3-pip \
    && if [ -x /opt/nvidia/deepstream/deepstream/user_additional_install.sh ]; then \
        /opt/nvidia/deepstream/deepstream/user_additional_install.sh; \
    fi \
    && rm -rf /var/lib/apt/lists/*

# pyds 1.2.0 is the binding for DeepStream 7.1; it is built against NumPy 1.x.
RUN python3 -m pip install --no-cache-dir \
        "${PYDS_WHEEL_URL}" \
        "numpy<2" \
        kafka-python==2.0.2 \
        opencv-python-headless==4.10.0.84

# YOLO bbox parser built for CUDA 12.6 / TensorRT 10 from the sources under models/
# (no prebuilt .so is kept in the repository). infer*.txt load it from /opt/moda.
COPY models/plate_recognition/nvdsinfer_custom_impl_Yolo/nvdsparsebbox_Yolo.cpp \
     models/plate_recognition/nvdsinfer_custom_impl_Yolo/nvdsparsebbox_Yolo_cuda.cu \
     models/plate_recognition/nvdsinfer_custom_impl_Yolo/utils.cpp \
     models/plate_recognition/nvdsinfer_custom_impl_Yolo/utils.h \
     /build/yolo_parser/
COPY docker/yolo_parser.mk /build/yolo_parser/Makefile
RUN make -C /build/yolo_parser -j"$(nproc)" \
    && install -D /build/yolo_parser/libnvdsinfer_custom_impl_Yolo.so \
        /opt/moda/libnvdsinfer_custom_impl_Yolo.so \
    && rm -rf /build

COPY docker/prepare_engines.py /app/docker/prepare_engines.py
COPY docker/entrypoint.sh /usr/local/bin/moda-entrypoint
RUN sed -i 's/\r$//' /usr/local/bin/moda-entrypoint && chmod +x /usr/local/bin/moda-entrypoint

COPY traffic_core /app/traffic_core
COPY config /app/config

ENTRYPOINT ["moda-entrypoint"]
