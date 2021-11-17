import os
import tempfile
from pathlib import Path

import pytest
from transformers import PreTrainedTokenizer, AutoTokenizer

from templates.triton import Configuration, ModelType


@pytest.fixture
def working_directory() -> tempfile.TemporaryDirectory:
    return tempfile.TemporaryDirectory()


@pytest.fixture
def conf(working_directory: tempfile.TemporaryDirectory):
    conf = Configuration(model_name="test", model_type=ModelType.ONNX, batch_size=0, nb_output=2, nb_instance=1, include_token_type=False, workind_directory=working_directory.name)
    return conf


def test_model_conf(conf: Configuration):
    expected = """
name: "test_model"
max_batch_size: 0
platform: "onnxruntime_onnx"
default_model_filename: "model.bin"

input [
    {
        name: "input_ids"
        data_type: TYPE_INT64
        dims: [-1, -1]
    },
    
    {
        name: "attention_mask"
        data_type: TYPE_INT64
        dims: [-1, -1]
    }

]

output {
    name: "output"
    data_type: TYPE_FP32
    dims: [-1, 2]
}

instance_group [
    {
      count: 1
      kind: KIND_GPU
    }
]
"""
    assert expected.strip() == conf.get_model_conf()


def test_tokenizer_conf(conf: Configuration):
    expected = """
name: "test_tokenize"
max_batch_size: 0
backend: "python"

input [
    {
        name: "TEXT"
        data_type: TYPE_STRING
        dims: [ -1 ]
    }
]

output [
    {
        name: "input_ids"
        data_type: TYPE_INT64
        dims: [-1, -1]
    },
    
    {
        name: "attention_mask"
        data_type: TYPE_INT64
        dims: [-1, -1]
    }

]

instance_group [
    {
      count: 1
      kind: KIND_GPU
    }
]
"""
    assert expected.strip() == conf.get_tokenize_conf()


def test_inference_conf(conf: Configuration):
    expected = """
name: "test_inference"
max_batch_size: 0
platform: "ensemble"

input [
    {
        name: "TEXT"
        data_type: TYPE_STRING
        dims: [ -1 ]
    }
]

output {
    name: "output"
    data_type: TYPE_FP32
    dims: [-1, 2]
}

ensemble_scheduling {
    step [
        {
            model_name: "test_tokenize"
            model_version: -1
            input_map {
            key: "TEXT"
            value: "TEXT"
        }
        output_map [
            {
                key: "input_ids"
                value: "input_ids"
            },
            
            {
                key: "attention_mask"
                value: "attention_mask"
            }
        ]
        },
        {
            model_name: "test_model"
            model_version: -1
            input_map [
                {
                    key: "input_ids"
                    value: "input_ids"
                },
                
                {
                    key: "attention_mask"
                    value: "attention_mask"
                }
            ]
        output_map {
                key: "output"
                value: "output"
            }
        }
    ]
}
"""
    assert expected.strip() == conf.get_inference_conf()


def test_create_folders(conf: Configuration, working_directory: tempfile.TemporaryDirectory):
    fake_model_path = os.path.join(working_directory.name, "fake_model")
    open(fake_model_path, 'a').close()
    tokenizer: PreTrainedTokenizer = AutoTokenizer.from_pretrained("philschmid/MiniLM-L6-H384-uncased-sst2")
    conf.create_folders(model_path=fake_model_path, tokenizer=tokenizer)
    for folder_name in [conf.model_folder_name, conf.tokenizer_folder_name, conf.inference_folder_name]:
        path = Path(conf.workind_directory).joinpath(folder_name)
        assert path.joinpath("config.pbtxt").exists()
        assert path.joinpath("1").exists()