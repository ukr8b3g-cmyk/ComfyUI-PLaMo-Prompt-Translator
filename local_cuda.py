"""Replay a single PLaMo decode step. Transformers still controls generation."""

from contextlib import ExitStack, contextmanager
from functools import wraps
import logging

import torch
from transformers.modeling_outputs import CausalLMOutputWithPast


class _DecodeGraph:
    def __init__(self, model, forward, prefix, capacity):
        self.capacity = capacity
        device = model.device
        self.position = torch.zeros(1, dtype=torch.long, device=device)
        self.token = torch.zeros(1, 1, dtype=torch.long, device=device)
        self.mask = torch.ones(1, capacity, dtype=torch.long, device=device)
        self.slots = [
            tuple(t.new_zeros((*t.shape[:2], capacity, t.shape[-1])) for t in (layer.keys, layer.values))
            for layer in prefix.layers
        ]

        def position_query(module, args, tables):
            # The reviewed PFN attention takes the query's rotation from the final
            # KV slot. In a padded cache that slot must carry the *real* position.
            for table in tables:
                table[:, :, -1:].copy_(table.index_select(2, self.position))

        def step():
            return forward(input_ids=self.token, cache_position=self.position, past_key_values=self,
                           attention_mask=self.mask, use_cache=True, logits_to_keep=1).logits

        with torch.cuda.device(device), ExitStack() as hooks:
            for layer in model.model.layers.layers:
                hooks.enter_context(layer.mixer.rotary_emb.register_forward_hook(position_query))
            current = torch.cuda.current_stream(device)
            stream = torch.cuda.Stream(device=device)
            stream.wait_stream(current)
            with torch.cuda.stream(stream):
                for _ in range(3):
                    logits = step()
                self.logits = torch.empty_like(logits)
            current.wait_stream(stream)
            self.graph = torch.cuda.CUDAGraph()
            with torch.cuda.graph(self.graph, stream=stream, capture_error_mode="thread_local"):
                self.logits.copy_(step())

    def get_seq_length(self):
        # Used for mask dimensions. The real token position is passed to forward.
        return self.capacity - 1

    def update(self, keys, values, layer_idx, *args, **kwargs):
        k, v = self.slots[layer_idx]
        k.index_copy_(2, self.position, keys)
        v.index_copy_(2, self.position, values)
        return k, v

    def copy_prefix(self, prefix, length):
        for (k, v), layer in zip(self.slots, prefix.layers):
            k[:, :, :length].copy_(layer.keys)
            v[:, :, :length].copy_(layer.values)

    def replay(self, ids, cache, position):
        self.position.fill_(position)
        self.token.copy_(ids)
        self.graph.replay()
        # Keep the native cache valid, including when crossing the sliding-window
        # boundary and continuing through the normal model path.
        for idx, (k, v) in enumerate(self.slots):
            cache.update(k[:, :, position:position + 1], v[:, :, position:position + 1], idx)
        return CausalLMOutputWithPast(logits=self.logits, past_key_values=cache)


@contextmanager
def accelerate_decode(model, inputs, max_tokens):
    length = inputs.input_ids.shape[1]
    window = model.config.window_size
    graph = getattr(model, "_plamo_graph", None)
    if (graph is False or not window or length >= window or inputs.input_ids.shape[0] != 1
            or not inputs.attention_mask.all().item()):
        yield
        return
    previous_cache = None
    original = model.forward
    instance_forward = vars(model).get("forward")

    @wraps(original)
    def forward(*args, **kwargs):
        nonlocal graph, previous_cache
        ids = kwargs.get("input_ids")
        cache = kwargs.get("past_key_values")
        if graph is False or ids is None or ids.shape != (1, 1) or cache is None:
            return original(*args, **kwargs)
        position = cache.get_seq_length()
        if not isinstance(position, int) or not 0 < position < window:
            return original(*args, **kwargs)
        grow = graph is None or graph.capacity <= position
        if previous_cache is not cache or grow:
            try:
                if grow:
                    # Bound the work per generated token. Grow from the observed
                    # prefix rather than padding short translations to their output limit.
                    needed = min(length + max_tokens, max(128, position * 2))
                    capacity = min(window, 1 << (needed - 1).bit_length())
                    graph = model._plamo_graph = _DecodeGraph(model, original, cache, capacity)
                graph.copy_prefix(cache, position)
            except (RuntimeError, AttributeError, TypeError, ValueError) as exc:
                logging.warning("PLaMo CUDA graph unavailable; using standard generation: %s", exc)
                graph = model._plamo_graph = False
            if graph is False:
                return original(*args, **kwargs)
            previous_cache = cache
        return graph.replay(ids, cache, position)

    model.forward = forward
    try:
        yield
    finally:
        if instance_forward is None:
            del model.forward
        else:
            model.forward = instance_forward
