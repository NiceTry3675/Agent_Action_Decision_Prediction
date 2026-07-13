# SIM early-turn mixed-loss card

## Frozen contract

- Parent recipe: exact `kd_sieve_ca_s42` seed-42 champion.
- Only quality variable: `--sim-early-turn-loss-scale 0.25`.
- Scope: original non-replay `sess_sim` rows with `turn_index <= 2`.
- Normalization: raw `.25/1.0` multipliers are divided by their mean inside
  each SIM true class. AU stays `1.0`; replay keeps its existing `.5` weight.
- The multiplier is applied after hard/KD interpolation, so it scales the whole
  head/backbone hard+KD loss. Consensus still gates only the hard backbone.
- Full refit audit: target `15,818/64,975` SIM originals. Early multiplier
  range `.2590-.5797`; later multiplier range `1.0361-2.3186`.

The frozen-head proxy is research evidence only: three order seeds gave Full
Macro deltas `+.001251/+.001116/+.001496`, but scale `.25` was selected on the
same fixed surface and the proxy had no replay/full fine-tune. Public is the
decision metric.

## Launch

The canonical one-arm plan is `colab/sim_early_turn_loss_plan.json`. Lane A is
the expected exchange because its teacher and consensus anchors already use
`AADP_exchange` paths.

```bash
.venv/bin/python colab/aadp_colab.py push a

# Human terminal only if lane A is not already mounted and ready:
.venv/bin/python colab/aadp_colab.py up a --gpu A100

.venv/bin/python colab/aadp_colab.py launch a colab/run_plan_remote.py -- \
  --plan colab/sim_early_turn_loss_plan.json
```

The run must print:

```text
SIM early-turn mixed-loss scale: raw=0.25 target=15818/64975 normalization=SIM-true-class-mean1 AU/replay=unchanged
```

It must also retain the champion contracts: distill matched `70000/80000`,
Weak4 alpha rows `28782`, consensus histogram
`12776/3806/4811/48607`, and zero AMP skips across 15,000 optimizer steps.

After `run.alive=false`, successful auto-collect, and `release_safe=true`, run
normal `down a` before pulling the explicit run/model names.

## Package after collection

Create a separate deploy directory, retain tokenizer/config/meta, replace only
the fp16 `model.safetensors` with `model.int8.safetensors`, and verify 512 real
rows before packaging. Do not add bias, rules, sparse, graph, or prior layers.

```bash
cp -a \
  experiments/incoming/models/kd_sieve_ca_simearly025_refit_s42 \
  experiments/incoming/models/kd_sieve_ca_simearly025_refit_s42_int8

.venv/bin/python quantize_checkpoint.py quantize \
  --input experiments/incoming/models/kd_sieve_ca_simearly025_refit_s42/hf_model/model.safetensors \
  --output experiments/incoming/models/kd_sieve_ca_simearly025_refit_s42_int8/hf_model/model.int8.safetensors

.venv/bin/python quantize_checkpoint.py verify \
  --model-dir experiments/incoming/models/kd_sieve_ca_simearly025_refit_s42 \
  --quantized experiments/incoming/models/kd_sieve_ca_simearly025_refit_s42_int8/hf_model/model.int8.safetensors \
  --data-dir open/data --samples 512 --device cuda

rm experiments/incoming/models/kd_sieve_ca_simearly025_refit_s42_int8/hf_model/model.safetensors

.venv/bin/python package_submission.py \
  --hf-dir experiments/incoming/models/kd_sieve_ca_simearly025_refit_s42_int8 \
  --no-sparse --out kd_ca_sim025_s42.zip
```

Before the Public slot, smoke from a clean extraction on GPU and CPU, verify
the exact `id,action` contract and valid labels, archive root entries, ZIP size,
and compare runtime against the `5:58` champion anchor.
