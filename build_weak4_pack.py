import argparse
import hashlib
import json
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

from safetensors.torch import load_file

from script import ALL_CLASSES, validate_weak4_specialist_config


REPO = Path(__file__).resolve().parent
WEAK4_CLASSES = ALL_CLASSES[:4]
EXPECTED_LORA_TARGETS = {
    "q_proj",
    "k_proj",
    "v_proj",
    "o_proj",
    "gate_proj",
    "up_proj",
    "down_proj",
}
EXPECTED_PEFT_VERSION = "0.19.1"
EXPECTED_FINAL_RESUME_SUFFIX = "/kd_m8_refit/hf_model"
REQUIRED_GATE_CHECKS = {
    "confirm_delta_positive",
    "confirm_rescue_gt_harm",
    "full_delta_at_least_0.005",
    "no_weak4_drop_at_least_0.005",
    "list_directory_no_drop",
    "alpha_plateau_has_neighbor",
    "nonweak_identity",
}


def load_json(path):
    with Path(path).open(encoding="utf-8") as f:
        return json.load(f)


def sha256_file(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as f:
        while chunk := f.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def resolve_lora_artifact(path):
    path = Path(path).resolve()
    if (path / "adapter_config.json").is_file():
        adapter_dir = path
        meta_path = path.parent / "hf_meta.json"
    elif (path / "hf_model" / "adapter_config.json").is_file():
        adapter_dir = path / "hf_model"
        meta_path = path / "hf_meta.json"
    else:
        raise ValueError(f"{path} does not contain a PEFT adapter directly or under hf_model/")
    required = [
        adapter_dir / "adapter_config.json",
        adapter_dir / "adapter_model.safetensors",
        adapter_dir / "weak4_training_provenance.json",
    ]
    missing = [str(item) for item in required if not item.is_file()]
    if missing:
        raise ValueError(f"LoRA artifact is incomplete: {missing}")
    if not meta_path.is_file():
        raise ValueError(f"LoRA artifact metadata is missing: {meta_path}")
    return adapter_dir, meta_path


def assert_empty_main_stack(base_dir, meta):
    if list(meta.get("classes") or []) != ALL_CLASSES:
        raise ValueError("base pack classes do not match canonical ALL_CLASSES")
    bias = meta.get("class_bias")
    if not isinstance(bias, list) or len(bias) != len(ALL_CLASSES):
        raise ValueError("base pack class_bias must contain 14 entries")
    nonzero = [(idx, value) for idx, value in enumerate(bias) if float(value) != 0.0]
    if nonzero:
        raise ValueError(f"base pack class_bias must be exactly zero: {nonzero[:5]}")
    forbidden_meta = {
        "rule_boosts": meta.get("rule_boosts"),
        "encoders": meta.get("encoders"),
        "cascade": meta.get("cascade"),
        "prior_calibration": meta.get("prior_calibration"),
        "test_batch_graph_backfill": meta.get("test_batch_graph_backfill"),
        "weak4_specialist": meta.get("weak4_specialist"),
        "compile": meta.get("compile"),
    }
    active = {key: value for key, value in forbidden_meta.items() if value}
    if active:
        raise ValueError(f"base pack has active post-processing/routing: {sorted(active)}")
    forbidden_files = [
        base_dir / "sparse_svc.pkl",
        base_dir / "sparse_meta.json",
        base_dir / "leak_lookup.json.gz",
    ]
    present = [str(path) for path in forbidden_files if path.exists()]
    if present:
        raise ValueError(f"base pack has forbidden post-processing artifacts: {present}")


def expected_target_shapes(base_config):
    hidden = int(base_config["hidden_size"])
    intermediate = int(base_config["intermediate_size"])
    heads = int(base_config["num_attention_heads"])
    kv_heads = int(base_config.get("num_key_value_heads", heads))
    head_dim = int(base_config.get("head_dim", hidden // heads))
    return {
        "q_proj": (heads * head_dim, hidden),
        "k_proj": (kv_heads * head_dim, hidden),
        "v_proj": (kv_heads * head_dim, hidden),
        "o_proj": (hidden, heads * head_dim),
        "gate_proj": (intermediate, hidden),
        "up_proj": (intermediate, hidden),
        "down_proj": (hidden, intermediate),
    }


def validate_adapter_contract(adapter_dir, base_config):
    config = load_json(adapter_dir / "adapter_config.json")
    if config.get("peft_type") != "LORA" or str(config.get("peft_version", "")) != EXPECTED_PEFT_VERSION:
        raise ValueError(
            f"Weak4 adapter must be PEFT LORA {EXPECTED_PEFT_VERSION}, "
            f"got type={config.get('peft_type')!r} version={config.get('peft_version')!r}"
        )
    if int(config.get("r", 0)) != 16 or float(config.get("lora_alpha", 0.0)) != 32.0:
        raise ValueError("Weak4 adapter must use LoRA r=16 and lora_alpha=32")
    if abs(float(config.get("lora_dropout", -1.0)) - 0.05) > 1e-12:
        raise ValueError("Weak4 adapter must use lora_dropout=0.05")
    if set(config.get("target_modules") or []) != EXPECTED_LORA_TARGETS:
        raise ValueError(
            f"Weak4 adapter target_modules mismatch: {config.get('target_modules')}"
        )
    if "score" not in set(config.get("modules_to_save") or []):
        raise ValueError("Weak4 adapter must save the score module")
    if config.get("task_type") != "SEQ_CLS" or str(config.get("bias", "none")).lower() != "none":
        raise ValueError("Weak4 adapter must use task_type=SEQ_CLS and bias=none")
    if any(bool(config.get(key)) for key in ("fan_in_fan_out", "use_rslora", "use_dora")):
        raise ValueError("Weak4 adapter uses an unsupported LoRA variant")
    if config.get("rank_pattern") or config.get("alpha_pattern"):
        raise ValueError("Weak4 adapter must not use per-module rank/alpha patterns")

    state = load_file(adapter_dir / "adapter_model.safetensors", device="cpu")
    pairs = {}
    score_keys = []
    unmatched = []
    for key, tensor in state.items():
        if key.endswith(".lora_A.weight"):
            stem = key.removesuffix(".lora_A.weight")
            pairs.setdefault(stem, {})["A"] = tensor
        elif key.endswith(".lora_B.weight"):
            stem = key.removesuffix(".lora_B.weight")
            pairs.setdefault(stem, {})["B"] = tensor
        elif key.endswith(".score.weight"):
            score_keys.append((key, tensor))
        else:
            unmatched.append(key)
    if unmatched:
        raise ValueError(f"Weak4 adapter has unmatched tensors: {unmatched[:5]}")
    incomplete = [stem for stem, pair in pairs.items() if set(pair) != {"A", "B"}]
    if incomplete:
        raise ValueError(f"Weak4 adapter has incomplete A/B pairs: {incomplete[:5]}")

    layer_count = int(base_config["num_hidden_layers"])
    expected_pair_count = layer_count * len(EXPECTED_LORA_TARGETS)
    if len(pairs) != expected_pair_count:
        raise ValueError(
            f"Weak4 adapter pair count mismatch: {len(pairs)} != {expected_pair_count}"
        )
    shapes = expected_target_shapes(base_config)
    seen = set()
    for stem, pair in pairs.items():
        match = re.search(r"\.layers\.(\d+)\..*\.([^.]+)$", stem)
        if not match:
            raise ValueError(f"cannot parse adapter layer/target from {stem}")
        layer_id = int(match.group(1))
        target = match.group(2)
        if not 0 <= layer_id < layer_count or target not in EXPECTED_LORA_TARGETS:
            raise ValueError(f"unexpected adapter target: layer={layer_id} target={target}")
        seen.add((layer_id, target))
        out_features, in_features = shapes[target]
        if tuple(pair["A"].shape) != (16, in_features):
            raise ValueError(f"LoRA A shape mismatch for {stem}: {tuple(pair['A'].shape)}")
        if tuple(pair["B"].shape) != (out_features, 16):
            raise ValueError(f"LoRA B shape mismatch for {stem}: {tuple(pair['B'].shape)}")
    expected_seen = {
        (layer_id, target)
        for layer_id in range(layer_count)
        for target in EXPECTED_LORA_TARGETS
    }
    if seen != expected_seen:
        raise ValueError("Weak4 adapter layer/target coverage is incomplete")
    hidden = int(base_config["hidden_size"])
    if len(score_keys) != 1 or tuple(score_keys[0][1].shape) != (len(ALL_CLASSES), hidden):
        raise ValueError(
            f"Weak4 adapter score tensor mismatch: "
            f"{[(key, tuple(tensor.shape)) for key, tensor in score_keys]}"
        )
    return config


def validate_inputs(base_dir, lora_path, tuner_path):
    base_dir = Path(base_dir).resolve()
    hf_dir = base_dir / "hf_model"
    meta_path = base_dir / "hf_meta.json"
    if not hf_dir.is_dir() or not meta_path.is_file():
        raise ValueError(f"{base_dir} must contain hf_model/ and hf_meta.json")
    if not (hf_dir / "model.int8.safetensors").is_file():
        raise ValueError("Weak4 pack base must use model.int8.safetensors")
    if not (hf_dir / "model.int8.safetensors.meta.json").is_file():
        raise ValueError("int8 base is missing model.int8.safetensors.meta.json")
    int8_meta = load_json(hf_dir / "model.int8.safetensors.meta.json")
    if int8_meta.get("format") != "int8-rowwise-v1":
        raise ValueError(f"unsupported int8 base format: {int8_meta.get('format')!r}")
    base_meta = load_json(meta_path)
    assert_empty_main_stack(base_dir, base_meta)
    if not bool(base_meta.get("final_refit", False)):
        raise ValueError("deployment base metadata must identify a final refit")
    base_config = load_json(hf_dir / "config.json")

    adapter_dir, lora_meta_path = resolve_lora_artifact(lora_path)
    lora_meta = load_json(lora_meta_path)
    if list(lora_meta.get("classes") or []) != ALL_CLASSES:
        raise ValueError("LoRA artifact classes do not match canonical ALL_CLASSES")
    if lora_meta.get("train_label_filter") != "weak4":
        raise ValueError("LoRA artifact was not trained with --train-label-filter weak4")
    expected_recipe = {
        "seed": 42,
        "epochs": 2,
        "train_batch_size": 16,
        "grad_accum_steps": 1,
        "gradient_checkpointing": True,
        "learning_rate": 1e-4,
        "weight_decay": 0.01,
        "label_smoothing": 0.02,
        "loss": "focal",
        "focal_gamma": 2.0,
        "class_weight_power": 0.5,
        "optim": "adamw",
        "bf16": False,
        "max_length": 384,
    }
    recipe_mismatches = {
        key: (lora_meta.get(key), expected)
        for key, expected in expected_recipe.items()
        if lora_meta.get(key) != expected
    }
    if recipe_mismatches:
        raise ValueError(f"LoRA artifact recipe mismatch: {recipe_mismatches}")
    if int(lora_meta.get("lora_r", 0)) != 16:
        raise ValueError("LoRA artifact metadata must record lora_r=16")
    if lora_meta.get("replay_mode") != "none":
        raise ValueError("Weak4 LoRA artifact must be trained without replay")
    if not bool(lora_meta.get("final_refit", False)):
        raise ValueError("deployment LoRA must be the final-refit artifact, not a screen adapter")
    if lora_meta.get("base_model") != base_meta.get("base_model"):
        raise ValueError("LoRA and int8 base identify different pretrained model families")
    lora_bias = lora_meta.get("class_bias")
    if not isinstance(lora_bias, list) or len(lora_bias) != len(ALL_CLASSES):
        raise ValueError("Weak4 LoRA artifact must contain a 14-entry zero class_bias")
    if any(float(value) != 0.0 for value in lora_bias):
        raise ValueError("Weak4 LoRA artifact must not contain tuned class bias")
    adapter_config = validate_adapter_contract(adapter_dir, base_config)
    training_provenance = load_json(adapter_dir / "weak4_training_provenance.json")
    resume_from = str(training_provenance.get("resume_from", "")).replace("\\", "/").rstrip("/")
    if not resume_from.endswith(EXPECTED_FINAL_RESUME_SUFFIX):
        raise ValueError(
            "deployment LoRA must warm-start from kd_m8_refit/hf_model; "
            f"recorded resume_from={resume_from!r}"
        )
    peft_version = str((training_provenance.get("packages") or {}).get("peft", ""))
    if peft_version != EXPECTED_PEFT_VERSION:
        raise ValueError(
            f"Weak4 adapter must be trained with peft=={EXPECTED_PEFT_VERSION}, got {peft_version!r}"
        )
    config_source = str(adapter_config.get("base_model_name_or_path", "")).replace("\\", "/").rstrip("/")
    if not config_source.endswith(EXPECTED_FINAL_RESUME_SUFFIX):
        raise ValueError(f"adapter_config base_model_name_or_path is not the final refit: {config_source}")

    tuner_path = Path(tuner_path).resolve()
    tuner = load_json(tuner_path)
    if int(tuner.get("schema_version", 0)) != 1:
        raise ValueError(f"unsupported tuner schema: {tuner.get('schema_version')}")
    if list(tuner.get("classes") or []) != ALL_CLASSES:
        raise ValueError("tuner classes do not match canonical ALL_CLASSES")
    if list(tuner.get("weak4_classes") or []) != WEAK4_CLASSES:
        raise ValueError("tuner Weak4 class order is invalid")
    if tuner.get("rows") != 14001 or tuner.get("split") != "session" or tuner.get("seed") != 42:
        raise ValueError("tuner report does not describe the fixed 14,001-row session/seed42 anchor")
    gate = tuner.get("gate") or {}
    checks = gate.get("checks") or {}
    if gate.get("quality_gate_evaluated") is not True:
        raise ValueError("pack builder requires a normal screen-quality tuner report, not verify output")
    if set(checks) != REQUIRED_GATE_CHECKS:
        raise ValueError(
            f"tuner gate check keys mismatch: expected={sorted(REQUIRED_GATE_CHECKS)} "
            f"actual={sorted(checks)}"
        )
    if not all(value is True for value in checks.values()):
        raise ValueError("tuner gate checks are absent or contain a failure")
    if gate.get("passed") is not True or gate.get("passed") != all(checks.values()):
        raise ValueError("tuner pre-registered promotion gate did not pass")
    serializer_name = tuner.get("serializer_name")
    if lora_meta.get("serializer_name") != serializer_name:
        raise ValueError(
            "LoRA serializer and tuner serializer differ: "
            f"{lora_meta.get('serializer_name')} != {serializer_name}"
        )
    if int(lora_meta.get("max_length", -1)) != int(tuner.get("max_length", -2)):
        raise ValueError("LoRA and tuner max_length differ")
    if int(lora_meta.get("batch_size", -1)) != int(tuner.get("batch_size", -2)):
        raise ValueError("LoRA eval/inference batch_size and tuner batch_size differ")
    return base_dir, base_meta, adapter_dir, lora_meta, tuner_path, tuner


def git_sha():
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"],
            cwd=REPO,
            text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
    except (OSError, subprocess.CalledProcessError):
        return "unknown"


def build_pack(base_dir, lora_path, tuner_path, output_dir, overwrite=False):
    (
        base_dir,
        base_meta,
        adapter_dir,
        lora_meta,
        tuner_path,
        tuner,
    ) = validate_inputs(base_dir, lora_path, tuner_path)
    output_dir = Path(output_dir).resolve()
    if output_dir.exists() and not overwrite:
        raise FileExistsError(f"output already exists: {output_dir}; pass --overwrite to replace it")
    output_dir.parent.mkdir(parents=True, exist_ok=True)

    specialist = {
        "enabled": True,
        "classes": list(range(4)),
        "alpha": float(tuner["selected_alpha"]),
        "route_fraction": float(tuner["route_fraction"]),
        "serializer_name": tuner["serializer_name"],
        "max_length": int(tuner["max_length"]),
        "batch_size": int(tuner["batch_size"]),
        "lora_dir": "lora_weak",
    }
    validate_weak4_specialist_config(specialist, ALL_CLASSES)
    pack_meta = dict(base_meta)
    pack_meta["weak4_specialist"] = specialist
    pack_meta["weak4_provenance"] = {
        "builder_git_sha": git_sha(),
        "builder_sha256": sha256_file(REPO / "build_weak4_pack.py"),
        "script_sha256": sha256_file(REPO / "script.py"),
        "train_transformer_sha256": sha256_file(REPO / "train_transformer.py"),
        "base_dir": str(base_dir),
        "lora_dir": str(adapter_dir),
        "tuner_json": str(tuner_path),
        "adapter_config_sha256": sha256_file(adapter_dir / "adapter_config.json"),
        "adapter_model_sha256": sha256_file(adapter_dir / "adapter_model.safetensors"),
        "base_int8_sha256": sha256_file(base_dir / "hf_model" / "model.int8.safetensors"),
        "base_config_sha256": sha256_file(base_dir / "hf_model" / "config.json"),
        "base_meta_sha256": sha256_file(base_dir / "hf_meta.json"),
        "tuner_sha256": sha256_file(tuner_path),
        "training_environment": load_json(adapter_dir / "weak4_training_provenance.json"),
        "screen_gate": tuner["gate"],
    }

    temp_root = Path(tempfile.mkdtemp(prefix="weak4_pack_", dir=output_dir.parent))
    try:
        shutil.copytree(base_dir / "hf_model", temp_root / "hf_model")
        packaged_lora = temp_root / "lora_weak"
        packaged_lora.mkdir()
        for name in (
            "adapter_config.json",
            "adapter_model.safetensors",
            "weak4_training_provenance.json",
        ):
            shutil.copy2(adapter_dir / name, packaged_lora / name)
        (temp_root / "hf_meta.json").write_text(
            json.dumps(pack_meta, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        assert_empty_main_stack(temp_root, base_meta)
        if output_dir.exists():
            shutil.rmtree(output_dir)
        temp_root.replace(output_dir)
    except Exception:
        shutil.rmtree(temp_root, ignore_errors=True)
        raise
    print(
        f"built Weak4 pack: {output_dir} alpha={specialist['alpha']:.2f} "
        f"route_fraction={specialist['route_fraction']:.3f} serializer={specialist['serializer_name']}"
    )
    return output_dir


def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--base-dir",
        default="experiments/incoming/models/kd_m8_refit_int8",
    )
    parser.add_argument("--lora-dir", required=True)
    parser.add_argument("--tuner-json", required=True)
    parser.add_argument(
        "--output-dir",
        default="experiments/incoming/models/kd_m8_weak4",
    )
    parser.add_argument("--overwrite", action="store_true")
    return parser.parse_args()


def main():
    args = parse_args()
    build_pack(
        args.base_dir,
        args.lora_dir,
        args.tuner_json,
        args.output_dir,
        overwrite=args.overwrite,
    )


if __name__ == "__main__":
    main()
