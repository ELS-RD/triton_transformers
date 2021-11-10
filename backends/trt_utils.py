from typing import List, Tuple, OrderedDict
import pycuda.driver as cuda
from numpy import ndarray
from pycuda._driver import Stream
from tensorrt import ICudaEngine, IExecutionContext
import tensorrt as trt
import numpy as np
from tensorrt.tensorrt import Runtime, Logger


def setup_binding_shapes(context: trt.IExecutionContext, host_inputs: List[np.ndarray], input_binding_idxs: List[int], output_binding_idxs: List[int]):
    # explicitly set dynamic input shapes, so dynamic output shapes can be computed internally
    for host_input, binding_index in zip(host_inputs, input_binding_idxs):
        context.set_binding_shape(binding_index, host_input.shape)
    assert context.all_binding_shapes_specified
    host_outputs = []
    device_outputs = []
    for binding_index in output_binding_idxs:
        output_shape = context.get_binding_shape(binding_index)
    # allocate buffers to hold output results after copying back to host
    buffer = np.empty(output_shape, dtype=np.float32)
    host_outputs.append(buffer)
    # allocate output buffers on device
    device_outputs.append(cuda.mem_alloc(buffer.nbytes))
    return host_outputs, device_outputs


def get_binding_idxs(engine: trt.ICudaEngine, profile_index: int):
    # calculate start/end binding indices for current context's profile
    # https://docs.nvidia.com/deeplearning/tensorrt/developer-guide/index.html#opt_profiles_bindings
    num_bindings_per_profile = engine.num_bindings // engine.num_optimization_profiles
    start_binding = profile_index * num_bindings_per_profile
    end_binding = start_binding + num_bindings_per_profile  # Separate input and output binding indices for convenience
    input_binding_idxs = []
    output_binding_idxs = []
    for binding_index in range(start_binding, end_binding):
        if engine.binding_is_input(binding_index):
            input_binding_idxs.append(binding_index)
        else:
            output_binding_idxs.append(binding_index)
    return input_binding_idxs, output_binding_idxs


def build_engine(runtime: Runtime, onnx_file_path: str, logger: Logger, min_shape: Tuple[int, int], optimal_shape: Tuple[int, int], max_shape: Tuple[int, int], workspace_size: int) -> ICudaEngine:
    with trt.Builder(logger) as builder, builder.create_network(flags=1 << int(trt.NetworkDefinitionCreationFlag.EXPLICIT_BATCH)) as network, trt.OnnxParser(network, logger) as parser:
        builder.max_batch_size = max_shape[0]  # max batch size
        config = builder.create_builder_config()
        config.max_workspace_size = workspace_size
        # CUBLAS_LT only for TensorRT >= 8
        config.set_tactic_sources(tactic_sources=1 << int(trt.TacticSource.CUBLAS) | 1 << int(trt.TacticSource.CUBLAS_LT))
        config.set_flag(trt.BuilderFlag.FP16)
        # https://github.com/NVIDIA/TensorRT/issues/1196 (sometimes big diff in output when going in FP16)
        config.set_flag(trt.BuilderFlag.STRICT_TYPES)
        with open(onnx_file_path, 'rb') as f:
            parser.parse(f.read())
        profile = builder.create_optimization_profile()
        for num_input in range(network.num_inputs):
            profile.set_shape(input=network.get_input(num_input).name, min=min_shape, opt=optimal_shape, max=max_shape)
            config.add_optimization_profile(profile)

        trt_engine = builder.build_serialized_network(network, config)
        engine: ICudaEngine = runtime.deserialize_cuda_engine(trt_engine)
        assert engine is not None, "error during engine generation :-("
        return engine


def save_engine(engine: ICudaEngine, engine_file_path: str):
    with open(engine_file_path, "wb") as f:
        f.write(engine.serialize())


def load_engine(runtime: Runtime, engine_file_path: str) -> ICudaEngine:
    with open(engine_file_path, "rb") as f:
        return runtime.deserialize_cuda_engine(f.read())


def infer_tensorrt(context: IExecutionContext, host_inputs: OrderedDict[str, np.ndarray], input_binding_idxs: List[int], output_binding_idxs: List[int], stream: Stream) -> np.ndarray:
    # warning: small change in output if int64 is used instead of int32
    input_list: List[ndarray] = [tensor.astype(np.int32) for tensor in host_inputs.values()]
    # allocate GPU memory for input tensors
    device_inputs = [cuda.mem_alloc(tensor.nbytes) for tensor in input_list]
    for h_input, d_input in zip(input_list, device_inputs):
        cuda.memcpy_htod_async(d_input, h_input)  # host to GPU
    # calculate input shape, bind it, allocate GPU memory for the output
    host_outputs, device_outputs = setup_binding_shapes(context, input_list, input_binding_idxs, output_binding_idxs)
    bindings = device_inputs + device_outputs
    context.execute_async_v2(bindings, stream.handle)
    for h_output, d_output in zip(host_outputs, device_outputs):
        cuda.memcpy_dtoh_async(h_output, d_output)  # GPU to host
    stream.synchronize()  # sync all CUDA ops
    return host_outputs