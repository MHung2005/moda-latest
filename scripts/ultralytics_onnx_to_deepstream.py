"""Append the DeepStream-Yolo output head to a raw Ultralytics detect ONNX.

Ultralytics:   output0 [B, 4 + nc, A]   (xc, yc, w, h in input pixels, then sigmoid class scores)
DeepStream-Yolo (NvDsInferParseYolo, decodeTensorYolo) expects, in this order:
               boxes [B, A, 4] (xc, yc, w, h), scores [B, A, 1], classes [B, A, 1] (float)

Usage: python to_deepstream.py <in.onnx> <out.onnx> [--dynamic-batch]
"""

from __future__ import annotations

import sys

import onnx
from onnx import TensorProto, helper


def convert(source: str, target: str, dynamic_batch: bool) -> None:
    model = onnx.load(source)
    graph = model.graph
    if len(graph.output) != 1:
        raise SystemExit(f"expected one output, got {[o.name for o in graph.output]}")
    raw = graph.output[0]
    dims = raw.type.tensor_type.shape.dim
    channels = dims[1].dim_value
    if channels <= 4:
        raise SystemExit(f"unexpected output shape {[d.dim_value or d.dim_param for d in dims]}")

    def const(name, values):
        return helper.make_node(
            "Constant", [], [name],
            value=helper.make_tensor(name, TensorProto.INT64, [len(values)], values),
        )

    nodes = [
        helper.make_node("Transpose", [raw.name], ["ds_t"], perm=[0, 2, 1]),
        const("ds_axes", [2]),
        const("ds_b_start", [0]), const("ds_b_end", [4]),
        const("ds_c_start", [4]), const("ds_c_end", [channels]),
        helper.make_node("Slice", ["ds_t", "ds_b_start", "ds_b_end", "ds_axes"], ["boxes"]),
        helper.make_node("Slice", ["ds_t", "ds_c_start", "ds_c_end", "ds_axes"], ["ds_cls"]),
        helper.make_node("ReduceMax", ["ds_cls"], ["scores"], axes=[2], keepdims=1),
        helper.make_node("ArgMax", ["ds_cls"], ["ds_argmax"], axis=2, keepdims=1),
        helper.make_node("Cast", ["ds_argmax"], ["classes"], to=TensorProto.FLOAT),
    ]
    graph.node.extend(nodes)
    del graph.output[:]
    batch = "batch" if dynamic_batch else dims[0].dim_value
    anchors = dims[2].dim_value or dims[2].dim_param
    for name, last in (("boxes", 4), ("scores", 1), ("classes", 1)):
        graph.output.append(helper.make_tensor_value_info(name, TensorProto.FLOAT, [batch, anchors, last]))
    if dynamic_batch:
        graph.input[0].type.tensor_type.shape.dim[0].ClearField("dim_value")
        graph.input[0].type.tensor_type.shape.dim[0].dim_param = "batch"
        del graph.value_info[:]  # stale static shapes; TensorRT re-infers them
    onnx.checker.check_model(model)
    onnx.save(model, target)
    print(f"wrote {target}")


if __name__ == "__main__":
    convert(sys.argv[1], sys.argv[2], "--dynamic-batch" in sys.argv[3:])
