"""Sequence-classification head for Gemma 4 (transformers has no official class yet;
PR #45294 is unmerged). Standard HF pattern (mirrors LlamaForSequenceClassification):
backbone -> per-token hidden states -> score head -> pool at each sequence's last
non-pad token. Dispatches between the gemma4 and gemma4_unified backbones by config.
Written in-repo so no third-party fork code is executed."""
import torch
from torch import nn
from transformers.modeling_outputs import SequenceClassifierOutputWithPast


def build_gemma4_seqcls(config_path_or_id, num_labels, id2label, label2id, torch_dtype=None):
    from transformers import AutoConfig

    config = AutoConfig.from_pretrained(config_path_or_id)
    text_config = getattr(config, "text_config", config)
    model_type = getattr(config, "model_type", "")

    if "unified" in model_type:
        from transformers import Gemma4UnifiedPreTrainedModel as Base
        from transformers import Gemma4UnifiedTextModel as Backbone
    else:
        from transformers import Gemma4PreTrainedModel as Base
        from transformers import Gemma4TextModel as Backbone

    class Gemma4ForSequenceClassificationCustom(Base):
        def __init__(self, cfg):
            super().__init__(cfg)
            self.num_labels = num_labels
            self.model = Backbone(getattr(cfg, "text_config", cfg))
            hidden = getattr(getattr(cfg, "text_config", cfg), "hidden_size")
            self.score = nn.Linear(hidden, num_labels, bias=False)
            self.post_init()

        def get_input_embeddings(self):
            return self.model.get_input_embeddings()

        def set_input_embeddings(self, value):
            self.model.set_input_embeddings(value)

        def forward(self, input_ids=None, attention_mask=None, labels=None, **kwargs):
            kwargs.pop("token_type_ids", None)
            outputs = self.model(input_ids=input_ids, attention_mask=attention_mask, **kwargs)
            hidden_states = outputs[0]
            logits = self.score(hidden_states)
            batch = input_ids.shape[0]
            if attention_mask is not None:
                last_idx = attention_mask.sum(dim=1) - 1
            else:
                last_idx = torch.full((batch,), input_ids.shape[1] - 1, device=input_ids.device)
            pooled = logits[torch.arange(batch, device=logits.device), last_idx]
            loss = None
            if labels is not None:
                loss = nn.functional.cross_entropy(pooled.float(), labels)
            return SequenceClassifierOutputWithPast(loss=loss, logits=pooled)

    config.num_labels = num_labels
    config.id2label = id2label
    config.label2id = label2id
    model = Gemma4ForSequenceClassificationCustom.from_pretrained(
        config_path_or_id, config=config, torch_dtype=torch_dtype,
    )
    return model
