# Builds only the bbox parser of DeepStream-Yolo (NvDsInferParseYolo) for DeepStream 7.1.
#
# The full library also contains a TensorRT engine builder (yolo.cpp) that calls
# IBuilder::buildEngineWithConfig, which TensorRT 10 (JetPack 6) removed, so it no longer
# compiles. Engines are built by docker/prepare_engines.py with trtexec instead, and the
# infer*.txt files no longer set engine-create-func-name, so only the parser is needed.
#
# Sources: models/plate_recognition/nvdsinfer_custom_impl_Yolo (identical in
# object_detection/ and plate_detection_with_logo/).

CUDA_VER ?= 12.6
DS_INCLUDES := /opt/nvidia/deepstream/deepstream/sources/includes
CUDA_HOME := /usr/local/cuda-$(CUDA_VER)

CXXFLAGS := -std=c++14 -O2 -Wall -shared -fPIC -Wno-deprecated-declarations \
	-I$(DS_INCLUDES) -I$(CUDA_HOME)/include
NVCCFLAGS := -O2 --compiler-options '-fPIC' -I$(DS_INCLUDES) -I$(CUDA_HOME)/include
LIBS := -L$(CUDA_HOME)/lib64 -lnvinfer -lcudart -lstdc++fs

OBJS := nvdsparsebbox_Yolo.o utils.o nvdsparsebbox_Yolo_cuda.o
TARGET := libnvdsinfer_custom_impl_Yolo.so

all: $(TARGET)

%.o: %.cpp
	g++ -c -o $@ $(CXXFLAGS) $<

%.o: %.cu
	$(CUDA_HOME)/bin/nvcc -c -o $@ $(NVCCFLAGS) $<

$(TARGET): $(OBJS)
	g++ -shared -o $@ $(OBJS) $(LIBS)

clean:
	rm -f $(OBJS) $(TARGET)
