import argparse
import csv
import hashlib
import json
import math
import tempfile
from pathlib import Path

import torch

from script import (
    ALL_CLASSES,
    load_jsonl,
    run_hf_inference,
    select_weak4_routes,
    serialize_transformer_sample,
    validate_weak4_specialist_config,
    weak4_family_locked_predictions,
)
from train import f1_metrics, session_id


WEAK4_CLASSES = ALL_CLASSES[:4]
WEAK4_IDS = list(range(4))
ALPHA_GRID = [step / 20.0 for step in range(21)]
PLATEAU_TOLERANCE = 0.0005
EXPECTED_ANCHOR_ROWS = 14001
EXPECTED_ANCHOR_SPLIT = "session"
EXPECTED_ANCHOR_SEED = 42
EXPECTED_ANCHOR_RAW_MACRO_F1 = 0.7838517354251339
EXPECTED_ANCHOR_IDS_SHA256 = "6f86a30da2845c1452df7030ee73718be856423248e4f2e65442a60373904985"
EXPECTED_RELABEL_ROWS = 248


def torch_load(path):
    try:
        return torch.load(path, map_location="cpu", weights_only=False)
    except TypeError:
        return torch.load(path, map_location="cpu")


def load_json(path):
    with Path(path).open(encoding="utf-8") as f:
        return json.load(f)


def payload_rows(payload, path):
    required = {"logits", "y_true", "ids", "classes", "split", "seed"}
    missing = sorted(required - set(payload))
    if missing:
        raise ValueError(f"{path} is missing payload keys: {missing}")
    logits = torch.as_tensor(payload["logits"]).float().cpu()
    ids = [str(value) for value in payload["ids"]]
    y_true = [int(value) for value in payload["y_true"]]
    if logits.ndim != 2 or logits.shape[0] != len(ids) or len(ids) != len(y_true):
        raise ValueError(
            f"{path} payload row mismatch: logits={tuple(logits.shape)} ids={len(ids)} y={len(y_true)}"
        )
    if logits.shape[1] != len(payload["classes"]):
        raise ValueError(
            f"{path} logits width {logits.shape[1]} != classes {len(payload['classes'])}"
        )
    if len(ids) != len(set(ids)):
        raise ValueError(f"{path} contains duplicate ids")
    return logits, ids, y_true


def join_payloads(main_payload, specialist_payload, main_path, specialist_path):
    main_logits, ids, y_true = payload_rows(main_payload, main_path)
    spec_logits, spec_ids, spec_y = payload_rows(specialist_payload, specialist_path)
    for key in ("classes", "split", "seed"):
        if specialist_payload[key] != main_payload[key]:
            raise ValueError(
                f"payload {key} mismatch: main={main_payload[key]!r} specialist={specialist_payload[key]!r}"
            )
    if list(main_payload["classes"]) != ALL_CLASSES:
        raise ValueError("main payload classes do not match canonical ALL_CLASSES")
    if set(ids) != set(spec_ids):
        missing = sorted(set(ids) - set(spec_ids))[:5]
        extra = sorted(set(spec_ids) - set(ids))[:5]
        raise ValueError(f"payload id sets differ: missing={missing} extra={extra}")

    spec_pos = {sample_id: idx for idx, sample_id in enumerate(spec_ids)}
    order = torch.tensor([spec_pos[sample_id] for sample_id in ids], dtype=torch.long)
    spec_logits = spec_logits[order]
    spec_y = [spec_y[idx] for idx in order.tolist()]
    if spec_y != y_true:
        mismatch = next(idx for idx, (left, right) in enumerate(zip(y_true, spec_y)) if left != right)
        raise ValueError(f"payload labels differ after id join at id={ids[mismatch]}")
    return main_logits, spec_logits, ids, y_true


def session_hash_partition(ids):
    tune = []
    confirm = []
    assignment = {}
    for idx, sample_id in enumerate(ids):
        group = session_id(sample_id)
        if group not in assignment:
            digest = hashlib.sha256(group.encode("utf-8")).digest()
            assignment[group] = int.from_bytes(digest[:8], "big") % 100 < 60
        (tune if assignment[group] else confirm).append(idx)
    if not tune or not confirm:
        raise ValueError("session-hash partition produced an empty tune or confirm set")
    tune_sessions = {session_id(ids[idx]) for idx in tune}
    confirm_sessions = {session_id(ids[idx]) for idx in confirm}
    if tune_sessions & confirm_sessions:
        raise AssertionError("session-hash partition leaked a session across tune/confirm")
    return tune, confirm


def subset_metrics(y_true, pred, indices):
    y_subset = [y_true[idx] for idx in indices]
    pred_subset = [int(pred[idx]) for idx in indices]
    return f1_metrics(y_subset, pred_subset)


def rescue_harm(y_true, baseline_pred, candidate_pred, routed, subset_indices):
    subset = set(subset_indices)
    routed_subset = [idx for idx in routed.tolist() if idx in subset]
    rescue = sum(
        int(baseline_pred[idx]) != y_true[idx] and int(candidate_pred[idx]) == y_true[idx]
        for idx in routed_subset
    )
    harm = sum(
        int(baseline_pred[idx]) == y_true[idx] and int(candidate_pred[idx]) != y_true[idx]
        for idx in routed_subset
    )
    changed = sum(int(baseline_pred[idx]) != int(candidate_pred[idx]) for idx in routed_subset)
    return {
        "routed_rows": len(routed_subset),
        "changed": changed,
        "rescue": rescue,
        "harm": harm,
        "net": rescue - harm,
    }


def score_alpha(main_logits, specialist_logits, routed, y_true, alpha, partitions):
    pred = weak4_family_locked_predictions(
        main_logits,
        specialist_logits[routed],
        routed,
        WEAK4_IDS,
        alpha,
    )
    report = {"alpha": alpha}
    for name, indices in partitions.items():
        metrics = subset_metrics(y_true, pred, indices)
        report[name] = {
            "macro_f1": metrics["macro_f1"],
            "weak4_per_class_f1": {
                label: metrics["per_class_f1"][label] for label in WEAK4_CLASSES
            },
        }
    return pred, report


def select_plateau_edge(alpha_reports):
    scores = [row["tune"]["macro_f1"] for row in alpha_reports]
    best_idx = min(
        range(len(scores)),
        key=lambda idx: (-scores[idx], alpha_reports[idx]["alpha"]),
    )
    threshold = scores[best_idx] - PLATEAU_TOLERANCE
    low = best_idx
    high = best_idx
    while low > 0 and scores[low - 1] >= threshold:
        low -= 1
    while high + 1 < len(scores) and scores[high + 1] >= threshold:
        high += 1
    return low, {
        "tolerance": PLATEAU_TOLERANCE,
        "peak_alpha": alpha_reports[best_idx]["alpha"],
        "peak_tune_macro_f1": scores[best_idx],
        "lower_alpha": alpha_reports[low]["alpha"],
        "upper_alpha": alpha_reports[high]["alpha"],
        "grid_points": high - low + 1,
        "has_neighbor": high > low,
        "selection_rule": "lowest alpha in the contiguous near-peak plateau",
    }


def percentile(values, quantile):
    if not values:
        return None
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, math.ceil(quantile * len(ordered)) - 1))
    return ordered[index]


def length_summary(values, max_length):
    if not values:
        return {"rows": 0, "mean": None, "p95": None, "p99": None, "max": None}
    return {
        "rows": len(values),
        "mean": sum(values) / len(values),
        "p95": percentile(values, 0.95),
        "p99": percentile(values, 0.99),
        "max": max(values),
        "over_max_length": sum(value > max_length for value in values),
        "over_max_length_fraction": sum(value > max_length for value in values) / len(values),
    }


def load_samples_by_id(train_path):
    samples = load_jsonl(train_path)
    by_id = {}
    for sample in samples:
        sample_id = str(sample.get("id", ""))
        if sample_id in by_id:
            raise ValueError(f"duplicate sample id in {train_path}: {sample_id}")
        by_id[sample_id] = sample
    return by_id


def token_audit(samples, routed, tokenizer_dir, serializer_name, max_length):
    from transformers import AutoTokenizer

    tokenizer = AutoTokenizer.from_pretrained(tokenizer_dir, local_files_only=True)
    texts = [serialize_transformer_sample(sample, serializer_name) for sample in samples]
    lengths = []
    for start in range(0, len(texts), 1024):
        encoded = tokenizer(
            texts[start:start + 1024],
            padding=False,
            truncation=False,
            return_length=True,
        )
        batch_lengths = encoded.get("length")
        if batch_lengths is None:
            batch_lengths = [len(row) for row in encoded["input_ids"]]
        lengths.extend(int(value) for value in batch_lengths)
    routed_lengths = [lengths[idx] for idx in routed.tolist()]
    return {
        "scope": "fixed validation payload",
        "serializer_name": serializer_name,
        "tokenizer_dir": str(tokenizer_dir),
        "max_length": max_length,
        "all": length_summary(lengths, max_length),
        "routed": length_summary(routed_lengths, max_length),
    }


def load_relabels(path):
    rows = []
    with Path(path).open(encoding="utf-8") as f:
        for line_no, line in enumerate(f, 1):
            if line.strip():
                try:
                    rows.append(json.loads(line))
                except json.JSONDecodeError as exc:
                    raise ValueError(f"{path}:{line_no}: invalid JSON") from exc
    return rows


def relabel_diagnostics(path, ids, baseline_pred, candidate_pred):
    id_to_pos = {sample_id: idx for idx, sample_id in enumerate(ids)}
    source_rows = load_relabels(path)
    source_ids = [str(row.get("id", "")) for row in source_rows]
    if len(source_rows) != EXPECTED_RELABEL_ROWS or len(set(source_ids)) != EXPECTED_RELABEL_ROWS:
        raise ValueError(
            f"relabels must contain exactly {EXPECTED_RELABEL_ROWS} unique rows, "
            f"got rows={len(source_rows)} unique={len(set(source_ids))}"
        )
    missing = sorted(set(source_ids) - set(id_to_pos))
    if missing:
        raise ValueError(f"relabel ids missing from validation payload: {missing[:5]}")
    rows = source_rows

    def label_at(pred, row):
        position = id_to_pos[str(row["id"])]
        return ALL_CLASSES[int(pred[position])]

    dataset_base = dataset_final = 0
    human_base = human_final = 0
    human_domain_base = human_domain_final = human_domain_n = 0
    acceptable_base = acceptable_final = 0
    for row in rows:
        base_label = label_at(baseline_pred, row)
        final_label = label_at(candidate_pred, row)
        dataset_label = row.get("dataset_true")
        dataset_base += base_label == dataset_label
        dataset_final += final_label == dataset_label
        human_label = row.get("human_label")
        human_base += base_label == human_label
        human_final += final_label == human_label
        if human_label in ALL_CLASSES:
            human_domain_n += 1
            human_domain_base += base_label == human_label
            human_domain_final += final_label == human_label
        acceptable = set(row.get("acceptable_labels") or [])
        acceptable_base += base_label in acceptable
        acceptable_final += final_label in acceptable
    return {
        "rows_joined": len(rows),
        "dataset_exact": {
            "baseline": dataset_base / len(rows),
            "candidate": dataset_final / len(rows),
        },
        "human_exact": {
            "rows": len(rows),
            "baseline": human_base / len(rows),
            "candidate": human_final / len(rows),
        },
        "human_exact_in_domain_only": {
            "rows": human_domain_n,
            "baseline": human_domain_base / human_domain_n if human_domain_n else None,
            "candidate": human_domain_final / human_domain_n if human_domain_n else None,
        },
        "human_acceptable": {
            "rows": len(rows),
            "baseline": acceptable_base / len(rows),
            "candidate": acceptable_final / len(rows),
        },
    }


def verify_pack(pack_dir, samples, ids, expected_pred, device, expected_config, surface):
    pack_dir = Path(pack_dir)
    if device.type != "cuda":
        raise ValueError("pack parity verification requires CUDA to preserve fp16 inference dtype")
    fp16_weights = pack_dir / "hf_model" / "model.safetensors"
    int8_weights = pack_dir / "hf_model" / "model.int8.safetensors"
    if surface == "fp16_algorithm":
        if not fp16_weights.is_file() or int8_weights.exists():
            raise ValueError("fp16_algorithm verification requires a fp16-only model.safetensors pack")
    elif surface == "int8_implementation":
        if not int8_weights.is_file() or not Path(str(int8_weights) + ".meta.json").is_file():
            raise ValueError("int8_implementation verification requires the int8 codec and sidecar")
    else:
        raise ValueError(f"unknown verification surface: {surface}")
    with (pack_dir / "hf_meta.json").open(encoding="utf-8") as f:
        pack_meta = json.load(f)
    specialist = pack_meta.get("weak4_specialist") or {}
    validate_weak4_specialist_config(specialist, pack_meta.get("classes") or [])
    for key in ("classes", "serializer_name", "max_length", "batch_size"):
        if specialist.get(key) != expected_config[key]:
            raise ValueError(
                f"verify pack specialist {key} mismatch: "
                f"pack={specialist.get(key)!r} expected={expected_config[key]!r}"
            )
    for key in ("alpha", "route_fraction"):
        if abs(float(specialist.get(key)) - float(expected_config[key])) > 1e-12:
            raise ValueError(
                f"verify pack specialist {key} mismatch: "
                f"pack={specialist.get(key)!r} expected={expected_config[key]!r}"
            )
    with tempfile.TemporaryDirectory(prefix="weak4_verify_") as tmp:
        tmp = Path(tmp)
        data_dir = tmp / "data"
        data_dir.mkdir()
        with (data_dir / "test.jsonl").open("w", encoding="utf-8") as f:
            for sample in samples:
                f.write(json.dumps(sample, ensure_ascii=False) + "\n")
        with (data_dir / "sample_submission.csv").open("w", encoding="utf-8", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=["id", "action"])
            writer.writeheader()
            writer.writerows({"id": sample_id, "action": ALL_CLASSES[0]} for sample_id in ids)
        output = tmp / "submission.csv"
        run_hf_inference(str(pack_dir), str(data_dir), str(output), device)
        with output.open(encoding="utf-8", newline="") as f:
            actual = {row["id"]: row["action"] for row in csv.DictReader(f)}
    expected = {sample_id: ALL_CLASSES[int(expected_pred[idx])] for idx, sample_id in enumerate(ids)}
    mismatches = [sample_id for sample_id in ids if actual.get(sample_id) != expected[sample_id]]
    if mismatches:
        raise AssertionError(f"pack predictions differ from tuner for {len(mismatches)} ids: {mismatches[:5]}")
    return {
        "verified": True,
        "rows": len(ids),
        "pack_dir": str(pack_dir),
        "surface": surface,
    }


def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument("--main-logits", required=True)
    parser.add_argument("--specialist-logits", required=True)
    parser.add_argument("--data-dir", default="open/data")
    parser.add_argument(
        "--tokenizer-dir",
        default="experiments/incoming/models/kd_m8_refit/hf_model",
    )
    parser.add_argument("--serializer-name", choices=["current_v1", "weak_nav_v1", "weak_nav_paths_v1"], default="")
    parser.add_argument("--max-length", type=int, default=384)
    parser.add_argument("--batch-size", type=int, default=64)
    parser.add_argument("--route-fraction", type=float, default=0.30)
    parser.add_argument("--relabels", default="")
    parser.add_argument("--output", required=True)
    parser.add_argument("--verify", action="store_true")
    parser.add_argument(
        "--fixed-tuner-json",
        default="",
        help="screen tuner report whose alpha/config stay fixed during parity verification",
    )
    parser.add_argument(
        "--verify-surface",
        choices=["fp16_algorithm", "int8_implementation"],
        default="",
        help="declare which same-artifact parity surface the supplied payloads represent",
    )
    parser.add_argument("--pack-dir", default="")
    parser.add_argument("--device", choices=["auto", "cpu", "cuda"], default="auto")
    args = parser.parse_args()
    if not 0.0 <= args.route_fraction <= 1.0:
        parser.error("--route-fraction must be in [0, 1]")
    if args.verify and not args.pack_dir:
        parser.error("--verify requires --pack-dir")
    if args.verify and not args.verify_surface:
        parser.error("--verify requires --verify-surface")
    if args.verify and not args.fixed_tuner_json:
        parser.error("--verify requires --fixed-tuner-json; parity must not retune alpha")
    if not args.verify and args.verify_surface:
        parser.error("--verify-surface requires --verify")
    if not args.verify and args.fixed_tuner_json:
        parser.error("--fixed-tuner-json requires --verify")
    return args


def main():
    args = parse_args()
    main_payload = torch_load(args.main_logits)
    specialist_payload = torch_load(args.specialist_logits)
    main_logits, specialist_logits, ids, y_true = join_payloads(
        main_payload,
        specialist_payload,
        args.main_logits,
        args.specialist_logits,
    )
    if len(ids) != EXPECTED_ANCHOR_ROWS:
        raise ValueError(f"fixed anchor must contain {EXPECTED_ANCHOR_ROWS} rows, got {len(ids)}")
    ids_digest = hashlib.sha256("\n".join(sorted(ids)).encode("utf-8")).hexdigest()
    if ids_digest != EXPECTED_ANCHOR_IDS_SHA256:
        raise ValueError(
            f"fixed anchor id digest mismatch: {ids_digest} != {EXPECTED_ANCHOR_IDS_SHA256}"
        )
    if main_payload["split"] != EXPECTED_ANCHOR_SPLIT:
        raise ValueError(
            f"fixed anchor split must be {EXPECTED_ANCHOR_SPLIT}, got {main_payload['split']}"
        )
    if int(main_payload["seed"]) != EXPECTED_ANCHOR_SEED:
        raise ValueError(
            f"fixed anchor seed must be {EXPECTED_ANCHOR_SEED}, got {main_payload['seed']}"
        )
    serializer_name = args.serializer_name or specialist_payload.get("serializer_name")
    if serializer_name not in {"current_v1", "weak_nav_v1", "weak_nav_paths_v1"}:
        raise ValueError(f"specialist payload has unsupported serializer: {serializer_name!r}")
    if specialist_payload.get("serializer_name") != serializer_name:
        raise ValueError(
            "requested serializer differs from specialist payload: "
            f"{serializer_name!r} != {specialist_payload.get('serializer_name')!r}"
        )
    if int(specialist_payload.get("max_length", -1)) != args.max_length:
        raise ValueError(
            "requested max_length differs from specialist payload: "
            f"{args.max_length} != {specialist_payload.get('max_length')!r}"
        )

    all_indices = list(range(len(ids)))
    tune_indices, confirm_indices = session_hash_partition(ids)
    partitions = {"tune": tune_indices, "confirm": confirm_indices, "full": all_indices}
    routed = select_weak4_routes(main_logits, WEAK4_IDS, args.route_fraction)
    baseline_pred = torch.argmax(main_logits, dim=1)
    baseline_metrics = {
        name: subset_metrics(y_true, baseline_pred, indices)
        for name, indices in partitions.items()
    }
    expected_raw = float(main_payload["raw_metrics"]["macro_f1"])
    strict_anchor_score = not args.verify or args.verify_surface == "fp16_algorithm"
    if strict_anchor_score and abs(expected_raw - EXPECTED_ANCHOR_RAW_MACRO_F1) > 1e-12:
        raise AssertionError(
            f"unexpected fixed-anchor raw macro: {expected_raw:.15f} "
            f"!= {EXPECTED_ANCHOR_RAW_MACRO_F1:.15f}"
        )
    reproduced_raw = baseline_metrics["full"]["macro_f1"]
    if abs(expected_raw - reproduced_raw) > 1e-12:
        raise AssertionError(
            f"alpha=0 sanity failed: payload raw={expected_raw:.15f} reproduced={reproduced_raw:.15f}"
        )

    alpha_reports = []
    alpha_predictions = []
    for alpha in ALPHA_GRID:
        pred, report = score_alpha(
            main_logits,
            specialist_logits,
            routed,
            y_true,
            alpha,
            partitions,
        )
        if alpha == 0.0 and not torch.equal(pred, baseline_pred):
            raise AssertionError("alpha=0 predictions do not exactly reproduce the main argmax")
        for name in partitions:
            report[name]["delta"] = (
                report[name]["macro_f1"] - baseline_metrics[name]["macro_f1"]
            )
        alpha_reports.append(report)
        alpha_predictions.append(pred)

    selection_source = "tune plateau lower edge"
    if args.verify:
        fixed_tuner = load_json(args.fixed_tuner_json)
        if list(fixed_tuner.get("classes") or []) != ALL_CLASSES:
            raise ValueError("fixed tuner class order does not match ALL_CLASSES")
        fixed_gate = fixed_tuner.get("gate") or {}
        if fixed_gate.get("passed") is not True or fixed_gate.get("quality_gate_evaluated") is not True:
            raise ValueError("fixed tuner did not pass the screen quality gate")
        fixed_contract = {
            "route_fraction": args.route_fraction,
            "serializer_name": serializer_name,
            "max_length": args.max_length,
            "batch_size": args.batch_size,
        }
        for key, expected in fixed_contract.items():
            actual = fixed_tuner.get(key)
            if isinstance(expected, float):
                matches = abs(float(actual) - expected) <= 1e-12
            else:
                matches = actual == expected
            if not matches:
                raise ValueError(
                    f"fixed tuner {key} mismatch: tuner={actual!r} requested={expected!r}"
                )
        fixed_alpha = float(fixed_tuner["selected_alpha"])
        try:
            selected_idx = next(
                idx for idx, row in enumerate(alpha_reports)
                if abs(float(row["alpha"]) - fixed_alpha) <= 1e-12
            )
        except StopIteration as exc:
            raise ValueError(f"fixed tuner alpha is outside the registered grid: {fixed_alpha}") from exc
        plateau = fixed_tuner.get("plateau") or {
            "has_neighbor": True,
            "selection_rule": "fixed from screen tuner",
        }
        selection_source = str(args.fixed_tuner_json)
    else:
        selected_idx, plateau = select_plateau_edge(alpha_reports)
    selected = alpha_reports[selected_idx]
    selected_pred = alpha_predictions[selected_idx]
    routed_set = set(routed.tolist())
    if any(int(selected_pred[idx]) != int(baseline_pred[idx]) for idx in all_indices if idx not in routed_set):
        raise AssertionError("non-routed predictions changed")

    outcomes = {
        name: rescue_harm(y_true, baseline_pred, selected_pred, routed, indices)
        for name, indices in partitions.items()
    }
    weak_deltas = {
        label: (
            selected["full"]["weak4_per_class_f1"][label]
            - baseline_metrics["full"]["per_class_f1"][label]
        )
        for label in WEAK4_CLASSES
    }
    gate_checks = {
        "confirm_delta_positive": selected["confirm"]["delta"] > 0.0,
        "confirm_rescue_gt_harm": outcomes["confirm"]["rescue"] > outcomes["confirm"]["harm"],
        "full_delta_at_least_0.005": selected["full"]["delta"] >= 0.005,
        "no_weak4_drop_at_least_0.005": all(delta > -0.005 for delta in weak_deltas.values()),
        "list_directory_no_drop": weak_deltas["list_directory"] >= 0.0,
        "alpha_plateau_has_neighbor": bool(plateau.get("has_neighbor", False)),
        "nonweak_identity": True,
    }

    samples_by_id = load_samples_by_id(Path(args.data_dir) / "train.jsonl")
    missing_samples = [sample_id for sample_id in ids if sample_id not in samples_by_id]
    if missing_samples:
        raise ValueError(f"validation ids missing from train.jsonl: {missing_samples[:5]}")
    samples = [samples_by_id[sample_id] for sample_id in ids]
    token_stats = token_audit(
        samples,
        routed,
        args.tokenizer_dir,
        serializer_name,
        args.max_length,
    )

    report = {
        "schema_version": 1,
        "main_logits": str(args.main_logits),
        "specialist_logits": str(args.specialist_logits),
        "classes": ALL_CLASSES,
        "weak4_classes": WEAK4_CLASSES,
        "split": main_payload["split"],
        "seed": int(main_payload["seed"]),
        "rows": len(ids),
        "partition": {
            "method": "sha256(session_id) modulo 100; tune < 60",
            "tune_rows": len(tune_indices),
            "confirm_rows": len(confirm_indices),
        },
        "route_fraction": args.route_fraction,
        "routed_rows": len(routed),
        "serializer_name": serializer_name,
        "max_length": args.max_length,
        "batch_size": args.batch_size,
        "selected_alpha": selected["alpha"],
        "selection_source": selection_source,
        "plateau": plateau,
        "baseline": {
            name: {
                "macro_f1": metrics["macro_f1"],
                "weak4_per_class_f1": {
                    label: metrics["per_class_f1"][label] for label in WEAK4_CLASSES
                },
            }
            for name, metrics in baseline_metrics.items()
        },
        "selected": selected,
        "alpha_grid": alpha_reports,
        "routed_outcomes": outcomes,
        "weak4_full_f1_delta": weak_deltas,
        "token_stats": token_stats,
        "gate": {
            "checks": gate_checks,
            "passed": all(gate_checks.values()),
            "quality_gate_evaluated": not args.verify,
        },
    }
    if args.relabels:
        report["relabel_diagnostics"] = relabel_diagnostics(
            args.relabels, ids, baseline_pred, selected_pred
        )
    if args.verify:
        device = torch.device(
            "cuda" if args.device == "auto" and torch.cuda.is_available() else
            "cpu" if args.device == "auto" else args.device
        )
        report["pack_verification"] = verify_pack(
            args.pack_dir,
            samples,
            ids,
            selected_pred,
            device,
            {
                "classes": WEAK4_IDS,
                "alpha": selected["alpha"],
                "route_fraction": args.route_fraction,
                "serializer_name": serializer_name,
                "max_length": args.max_length,
                "batch_size": args.batch_size,
            },
            args.verify_surface,
        )

    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(
        f"selected alpha={selected['alpha']:.2f} "
        f"tune_delta={selected['tune']['delta']:+.6f} "
        f"confirm_delta={selected['confirm']['delta']:+.6f} "
        f"full_delta={selected['full']['delta']:+.6f}"
    )
    print(
        f"confirm rescue={outcomes['confirm']['rescue']} harm={outcomes['confirm']['harm']} "
        f"gate={'PASS' if report['gate']['passed'] else 'FAIL'}"
    )
    print(f"saved {output}")


if __name__ == "__main__":
    main()
