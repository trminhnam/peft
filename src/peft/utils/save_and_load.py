# coding=utf-8
# Copyright 2023-present the HuggingFace Inc. team.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

from .config import PeftType


def get_peft_model_state_dict(model, state_dict=None):
    """
    Get the state dict of the Peft model.

    Args:
        model (`PeftModel`): The Peft model. When using torch.nn.DistributedDataParallel, DeepSpeed or FSDP,
        the model should be teh underlying model/unwrapped model (i.e. model.module).
        state_dict (:
            obj:`dict`, `optional`): The state dict of the model. If not provided, the state dict of the model
        will be used.
    """
    if state_dict is None:
        state_dict = model.state_dict()
    if model.peft_config.peft_type == PeftType.LORA:
        # to_return = lora_state_dict(model, bias=model.peft_config.bias)
        # adapted from `https://github.com/microsoft/LoRA/blob/main/loralib/utils.py`
        # to directly with the state dict which is necessary when using DeepSpeed or FSDP
        bias = model.peft_config.bias
        if bias == "none":
            to_return = {k: state_dict[k] for k in state_dict if "lora_" in k}
        elif bias == "all":
            to_return = {k: state_dict[k] for k in state_dict if "lora_" in k or "bias" in k}
        elif bias == "lora_only":
            to_return = {}
            for k in state_dict:
                if "lora_" in k:
                    to_return[k] = state_dict[k]
                    bias_name = k.split("lora_")[0] + "bias"
                    if bias_name in state_dict:
                        to_return[bias_name] = state_dict[bias_name]
        else:
            raise NotImplementedError
    else:
        to_return = {}
        prompt_embeddings = model.get_prompt_embedding_to_save()
        to_return["prompt_embeddings"] = prompt_embeddings
    if model.modules_to_save is not None:
        for key, value in state_dict.items():
            if any(module_name in key for module_name in model.modules_to_save):
                to_return[key] = value
    return to_return


def set_peft_model_state_dict(model, peft_model_state_dict):
    """
    Set the state dict of the Peft model.

    Args:
        model (`PeftModel`): The Peft model.
        peft_model_state_dict (`dict`): The state dict of the Peft model.
    """

    model.load_state_dict(peft_model_state_dict, strict=False)
    if model.peft_config.peft_type != PeftType.LORA:
        model.prompt_encoder.embedding.load_state_dict(
            {"weight": peft_model_state_dict["prompt_embeddings"]}, strict=True
        )
    return model


def peft_model_load_and_dispatch(model, peft_model_state_dict, peft_config, max_memory=None):
    """
    Load the Peft model state dict and dispatch the model to the correct device.

    Args:
        model (`PeftModel`): The Pre-trained base model which has already been sharded and dispatched
        using `accelerate` functionalities.
        peft_model_state_dict (`dict`): The state dict of the Peft model.
        max_memory (`Dict`, *optional*):
            A dictionary device identifier to maximum memory. Will default to the maximum memory available for each GPU
            and the available CPU RAM if unset.
    """
    from accelerate import dispatch_model, infer_auto_device_map
    from accelerate.hooks import AlignDevicesHook, add_hook_to_module, remove_hook_from_submodules

    from ..mapping import get_peft_model

    remove_hook_from_submodules(model)
    model = get_peft_model(model, peft_config)
    model.print_trainable_parameters()
    set_peft_model_state_dict(model, peft_model_state_dict)
    device_map = infer_auto_device_map(model, max_memory=max_memory, no_split_module_classes=model._no_split_modules)
    model = dispatch_model(model, device_map=device_map)
    hook = AlignDevicesHook(io_same_device=True)
    if model.peft_config.peft_type == PeftType.LORA:
        add_hook_to_module(model.base_model.model, hook)
    else:
        remove_hook_from_submodules(model.prompt_encoder)
        add_hook_to_module(model.base_model, hook)
    return model