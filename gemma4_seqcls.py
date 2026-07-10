"""Sequence-classification head for Gemma 4.

Transformers does not currently ship a public Gemma 4 sequence-classification
class in the target training environment, so this mirrors the standard
decoder-only sequence classifier pattern: run the text backbone, apply a score
head on each token, and pool the last non-pad token.
"""

import torch
from torch import nn
from transformers.modeling_outputs import SequenceClassifierOutputWithPast


def build_gemma4_seqcls(config_path_or_id, num_labels, id2label, label2id, torch_dtype=None):
    from transformers import AutoConfig

    config = AutoConfig.from_pretrained(config_path_or_id)
    model_type = getattr(config, "model_type", "")

    if "unified" in model_type:
        from transformers import Gemma4UnifiedPreTrainedModel as Base
        from transformers import Gemma4UnifiedModel as Backbone
    else:
        from transformers import Gemma4PreTrainedModel as Base
        from transformers import Gemma4Model as Backbone

    class Gemma4ForSequenceClassificationCustom(Base):
        def __init__(self, cfg):
            super().__init__(cfg)
            self.num_labels = num_labels
            self.model = Backbone(cfg)
            hidden_size = getattr(getattr(cfg, "text_config", cfg), "hidden_size")
            self.score = nn.Linear(hidden_size, num_labels, bias=False)
            self.post_init()

        def get_input_embeddings(self):
            return self.model.get_input_embeddings()

        def set_input_embeddings(self, value):
            self.model.set_input_embeddings(value)

        def forward(self, input_ids=None, attention_mask=None, labels=None, **kwargs):
            kwargs.pop("token_type_ids", None)
            outputs = self.model(input_ids=input_ids, attention_mask=attention_mask, **kwargs)
            hidden_states = outputs[0]
            token_logits = self.score(hidden_states)
            batch_size = input_ids.shape[0]
            if attention_mask is not None:
                positions = torch.arange(input_ids.shape[1], device=input_ids.device)
                last_idx = (
                    positions.unsqueeze(0)
                    .masked_fill(attention_mask.to(torch.bool).logical_not(), 0)
                    .max(dim=1)
                    .values
                )
            else:
                last_idx = torch.full((batch_size,), input_ids.shape[1] - 1, device=input_ids.device)
            logits = token_logits[torch.arange(batch_size, device=token_logits.device), last_idx]
            loss = None
            if labels is not None:
                loss = nn.functional.cross_entropy(logits.float(), labels)
            return SequenceClassifierOutputWithPast(loss=loss, logits=logits)

    config.num_labels = num_labels
    config.id2label = id2label
    config.label2id = label2id
    return Gemma4ForSequenceClassificationCustom.from_pretrained(
        config_path_or_id,
        config=config,
        torch_dtype=torch_dtype,
    )
