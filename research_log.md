## 20260701_081648_baseline_prompt_lr_random

- Date/time: 2026-07-01 08:16:48 UTC
- Hypothesis: Baseline notebook reproduction: current_prompt TF-IDF + LogisticRegression.
- Code/config changes: `tfidf_logreg` with current_prompt word 1-2grams.
- Validation setup: random
- Overall Macro-F1: 0.438757
- Per-class observations:
  - Weakest: list_directory=0.190, glob_pattern=0.208, web_search=0.268, lint_or_typecheck=0.270, grep_search=0.294
  - Strongest: respond_only=0.998, write_file=0.985, edit_file=0.645, run_bash=0.441, plan_task=0.392
- Top confusions: [(873, 'edit_file', 'apply_patch'), (541, 'apply_patch', 'edit_file'), (520, 'grep_search', 'read_file'), (478, 'grep_search', 'glob_pattern'), (474, 'read_file', 'grep_search'), (412, 'grep_search', 'list_directory'), (409, 'read_file', 'list_directory'), (371, 'read_file', 'glob_pattern')]
- Prediction distribution: {'apply_patch': 1321, 'ask_user': 502, 'edit_file': 1915, 'glob_pattern': 1304, 'grep_search': 1483, 'lint_or_typecheck': 624, 'list_directory': 1247, 'plan_task': 509, 'read_file': 1611, 'respond_only': 1032, 'run_bash': 946, 'run_tests': 840, 'web_search': 365, 'write_file': 301}
- Runtime or package-size concerns: sparse sklearn model; expected offline CPU inference within limits.
- Decision: discard or revisit
- Next suggested experiment: compare random vs session split and inspect weak classes before widening features.
## 20260701_081748_baseline_prompt_lr_session

- Date/time: 2026-07-01 08:17:48 UTC
- Hypothesis: Baseline notebook reproduction under session-aware validation.
- Code/config changes: `tfidf_logreg` with current_prompt word 1-2grams.
- Validation setup: session
- Overall Macro-F1: 0.432977
- Per-class observations:
  - Weakest: list_directory=0.171, glob_pattern=0.203, web_search=0.273, lint_or_typecheck=0.291, grep_search=0.292
  - Strongest: respond_only=0.997, write_file=0.977, edit_file=0.630, ask_user=0.417, run_bash=0.398
- Top confusions: [(914, 'edit_file', 'apply_patch'), (604, 'apply_patch', 'edit_file'), (534, 'grep_search', 'read_file'), (461, 'grep_search', 'list_directory'), (459, 'read_file', 'grep_search'), (430, 'grep_search', 'glob_pattern'), (414, 'read_file', 'glob_pattern'), (396, 'read_file', 'list_directory')]
- Prediction distribution: {'apply_patch': 1337, 'ask_user': 505, 'edit_file': 2015, 'glob_pattern': 1290, 'grep_search': 1502, 'lint_or_typecheck': 646, 'list_directory': 1311, 'plan_task': 489, 'read_file': 1568, 'respond_only': 996, 'run_bash': 882, 'run_tests': 888, 'web_search': 386, 'write_file': 291}
- Runtime or package-size concerns: sparse sklearn model; expected offline CPU inference within limits.
- Decision: discard or revisit
- Next suggested experiment: compare random vs session split and inspect weak classes before widening features.
## 20260701_081829_prompt_svc_random

- Date/time: 2026-07-01 08:19:21 UTC
- Hypothesis: Current prompt word/char TF-IDF plus intent/meta tokens; tuned class bias for Macro-F1.; class bias tuned on validation scores
- Code/config changes: `tfidf_linearsvc` with current_prompt word+char ngrams.
- Validation setup: random
- Overall Macro-F1: 0.651178
- Per-class observations:
  - Weakest: list_directory=0.362, read_file=0.384, glob_pattern=0.436, grep_search=0.495, ask_user=0.519
  - Strongest: respond_only=1.000, write_file=0.997, edit_file=0.949, apply_patch=0.884, run_bash=0.749
- Top confusions: [(564, 'read_file', 'grep_search'), (451, 'read_file', 'list_directory'), (396, 'grep_search', 'list_directory'), (336, 'grep_search', 'read_file'), (248, 'glob_pattern', 'grep_search'), (210, 'read_file', 'glob_pattern'), (206, 'grep_search', 'glob_pattern'), (200, 'list_directory', 'grep_search')]
- Prediction distribution: {'apply_patch': 1003, 'ask_user': 408, 'edit_file': 2209, 'glob_pattern': 974, 'grep_search': 2095, 'lint_or_typecheck': 410, 'list_directory': 1539, 'plan_task': 538, 'read_file': 1255, 'respond_only': 1036, 'run_bash': 966, 'run_tests': 976, 'web_search': 295, 'write_file': 296}
- Runtime or package-size concerns: sparse sklearn model; expected offline CPU inference within limits.
- Decision: discard or revisit
- Next suggested experiment: compare random vs session split and inspect weak classes before widening features.
## 20260701_081958_prompt_svc_session

- Date/time: 2026-07-01 08:20:48 UTC
- Hypothesis: Current prompt word/char TF-IDF plus intent/meta tokens under session-aware validation; tuned class bias.; class bias tuned on validation scores
- Code/config changes: `tfidf_linearsvc` with current_prompt word+char ngrams.
- Validation setup: session
- Overall Macro-F1: 0.640894
- Per-class observations:
  - Weakest: list_directory=0.317, glob_pattern=0.428, read_file=0.431, grep_search=0.458, web_search=0.516
  - Strongest: respond_only=1.000, write_file=0.998, edit_file=0.942, apply_patch=0.873, run_bash=0.720
- Top confusions: [(552, 'grep_search', 'read_file'), (488, 'read_file', 'grep_search'), (299, 'read_file', 'list_directory'), (270, 'grep_search', 'list_directory'), (263, 'list_directory', 'read_file'), (245, 'grep_search', 'glob_pattern'), (233, 'glob_pattern', 'grep_search'), (223, 'read_file', 'glob_pattern')]
- Prediction distribution: {'apply_patch': 1098, 'ask_user': 491, 'edit_file': 2210, 'glob_pattern': 1069, 'grep_search': 1859, 'lint_or_typecheck': 482, 'list_directory': 1036, 'plan_task': 462, 'read_file': 1850, 'respond_only': 1003, 'run_bash': 992, 'run_tests': 918, 'web_search': 359, 'write_file': 277}
- Runtime or package-size concerns: sparse sklearn model; expected offline CPU inference within limits.
- Decision: discard or revisit
- Next suggested experiment: compare random vs session split and inspect weak classes before widening features.
## 20260701_082146_full_svc_session

- Date/time: 2026-07-01 08:22:37 UTC
- Hypothesis: Full sparse channels: current prompt, history user/action text, args/result summaries, action sequence, workspace metadata; tuned class bias.; class bias tuned on validation scores
- Code/config changes: `tfidf_linearsvc` with prompt, history actions, args/results, workspace metadata.
- Validation setup: session
- Overall Macro-F1: 0.639448
- Per-class observations:
  - Weakest: list_directory=0.342, glob_pattern=0.412, read_file=0.433, grep_search=0.483, web_search=0.493
  - Strongest: respond_only=1.000, write_file=0.998, edit_file=0.936, apply_patch=0.852, run_bash=0.712
- Top confusions: [(545, 'grep_search', 'read_file'), (512, 'read_file', 'grep_search'), (346, 'read_file', 'list_directory'), (300, 'grep_search', 'list_directory'), (255, 'glob_pattern', 'read_file'), (249, 'list_directory', 'read_file'), (235, 'glob_pattern', 'grep_search'), (208, 'list_directory', 'grep_search')]
- Prediction distribution: {'apply_patch': 1011, 'ask_user': 476, 'edit_file': 2304, 'glob_pattern': 714, 'grep_search': 2000, 'lint_or_typecheck': 465, 'list_directory': 1214, 'plan_task': 503, 'read_file': 1901, 'respond_only': 1001, 'run_bash': 881, 'run_tests': 1036, 'web_search': 323, 'write_file': 277}
- Runtime or package-size concerns: sparse sklearn model; expected offline CPU inference within limits.
- Decision: discard or revisit
- Next suggested experiment: compare random vs session split and inspect weak classes before widening features.
## 20260701_082357_full_svc_light_session

- Date/time: 2026-07-01 08:24:48 UTC
- Hypothesis: Compact full-context variant to test whether smaller history channels help without diluting prompt/action-meta signal.; class bias tuned on validation scores
- Code/config changes: `tfidf_linearsvc` with compact prompt/history/meta sparse ensemble.
- Validation setup: session
- Overall Macro-F1: 0.640600
- Per-class observations:
  - Weakest: list_directory=0.346, read_file=0.396, glob_pattern=0.433, web_search=0.498, grep_search=0.499
  - Strongest: respond_only=1.000, write_file=0.998, edit_file=0.936, apply_patch=0.856, run_bash=0.713
- Top confusions: [(563, 'read_file', 'grep_search'), (397, 'grep_search', 'read_file'), (393, 'read_file', 'list_directory'), (322, 'grep_search', 'list_directory'), (244, 'glob_pattern', 'grep_search'), (223, 'list_directory', 'grep_search'), (192, 'ask_user', 'plan_task'), (188, 'list_directory', 'read_file')]
- Prediction distribution: {'apply_patch': 1016, 'ask_user': 415, 'edit_file': 2316, 'glob_pattern': 944, 'grep_search': 2175, 'lint_or_typecheck': 571, 'list_directory': 1309, 'plan_task': 567, 'read_file': 1448, 'respond_only': 1001, 'run_bash': 913, 'run_tests': 824, 'web_search': 330, 'write_file': 277}
- Runtime or package-size concerns: sparse sklearn model; expected offline CPU inference within limits.
- Decision: discard or revisit
- Next suggested experiment: compare random vs session split and inspect weak classes before widening features.
## 20260701_082637_prompt_svc_session

- Date/time: 2026-07-01 08:27:38 UTC
- Hypothesis: Prompt/action-meta SVC regularization sweep C=0.4.; class bias tuned on validation scores; C override=0.4
- Code/config changes: `tfidf_linearsvc` with current_prompt word+char ngrams.
- Validation setup: session
- Overall Macro-F1: 0.644404
- Per-class observations:
  - Weakest: list_directory=0.346, glob_pattern=0.427, read_file=0.431, grep_search=0.467, web_search=0.496
  - Strongest: respond_only=1.000, write_file=0.996, edit_file=0.942, apply_patch=0.865, run_bash=0.736
- Top confusions: [(517, 'grep_search', 'read_file'), (460, 'read_file', 'grep_search'), (403, 'read_file', 'list_directory'), (349, 'grep_search', 'list_directory'), (233, 'list_directory', 'read_file'), (231, 'glob_pattern', 'grep_search'), (202, 'glob_pattern', 'read_file'), (197, 'grep_search', 'glob_pattern')]
- Prediction distribution: {'apply_patch': 1003, 'ask_user': 439, 'edit_file': 2316, 'glob_pattern': 889, 'grep_search': 1801, 'lint_or_typecheck': 409, 'list_directory': 1365, 'plan_task': 555, 'read_file': 1755, 'respond_only': 1003, 'run_bash': 993, 'run_tests': 990, 'web_search': 308, 'write_file': 280}
- Runtime or package-size concerns: sparse sklearn model; expected offline CPU inference within limits.
- Decision: discard or revisit
- Next suggested experiment: compare random vs session split and inspect weak classes before widening features.
## 20260701_082646_prompt_svc_session

- Date/time: 2026-07-01 08:27:45 UTC
- Hypothesis: Prompt/action-meta SVC regularization sweep C=1.2.; class bias tuned on validation scores; C override=1.2
- Code/config changes: `tfidf_linearsvc` with current_prompt word+char ngrams.
- Validation setup: session
- Overall Macro-F1: 0.636689
- Per-class observations:
  - Weakest: list_directory=0.318, glob_pattern=0.411, read_file=0.414, grep_search=0.461, web_search=0.519
  - Strongest: respond_only=0.999, write_file=0.998, edit_file=0.942, apply_patch=0.874, run_bash=0.716
- Top confusions: [(516, 'read_file', 'grep_search'), (497, 'grep_search', 'read_file'), (370, 'read_file', 'list_directory'), (326, 'grep_search', 'list_directory'), (252, 'glob_pattern', 'grep_search'), (223, 'list_directory', 'read_file'), (220, 'grep_search', 'glob_pattern'), (217, 'list_directory', 'grep_search')]
- Prediction distribution: {'apply_patch': 1048, 'ask_user': 489, 'edit_file': 2246, 'glob_pattern': 959, 'grep_search': 1958, 'lint_or_typecheck': 443, 'list_directory': 1240, 'plan_task': 469, 'read_file': 1665, 'respond_only': 1005, 'run_bash': 1022, 'run_tests': 932, 'web_search': 351, 'write_file': 279}
- Runtime or package-size concerns: sparse sklearn model; expected offline CPU inference within limits.
- Decision: discard or revisit
- Next suggested experiment: compare random vs session split and inspect weak classes before widening features.
## 20260701_082823_prompt_svc_session

- Date/time: 2026-07-01 08:29:25 UTC
- Hypothesis: Prompt/action-meta SVC regularization sweep C=0.2.; class bias tuned on validation scores; C override=0.2
- Code/config changes: `tfidf_linearsvc` with current_prompt word+char ngrams.
- Validation setup: session
- Overall Macro-F1: 0.650287
- Per-class observations:
  - Weakest: list_directory=0.348, glob_pattern=0.444, read_file=0.447, grep_search=0.486, web_search=0.493
  - Strongest: respond_only=1.000, write_file=0.996, edit_file=0.936, apply_patch=0.858, run_bash=0.742
- Top confusions: [(555, 'grep_search', 'read_file'), (513, 'read_file', 'grep_search'), (283, 'read_file', 'list_directory'), (281, 'list_directory', 'read_file'), (246, 'glob_pattern', 'grep_search'), (233, 'grep_search', 'list_directory'), (225, 'glob_pattern', 'read_file'), (197, 'grep_search', 'glob_pattern')]
- Prediction distribution: {'apply_patch': 1063, 'ask_user': 539, 'edit_file': 2258, 'glob_pattern': 901, 'grep_search': 1980, 'lint_or_typecheck': 408, 'list_directory': 994, 'plan_task': 462, 'read_file': 1947, 'respond_only': 1002, 'run_bash': 914, 'run_tests': 1026, 'web_search': 332, 'write_file': 280}
- Runtime or package-size concerns: sparse sklearn model; expected offline CPU inference within limits.
- Decision: discard or revisit
- Next suggested experiment: compare random vs session split and inspect weak classes before widening features.
## 20260701_082828_prompt_svc_session

- Date/time: 2026-07-01 08:29:29 UTC
- Hypothesis: Prompt/action-meta SVC regularization sweep C=0.6.; class bias tuned on validation scores; C override=0.6
- Code/config changes: `tfidf_linearsvc` with current_prompt word+char ngrams.
- Validation setup: session
- Overall Macro-F1: 0.642515
- Per-class observations:
  - Weakest: list_directory=0.324, glob_pattern=0.421, read_file=0.433, grep_search=0.464, web_search=0.501
  - Strongest: respond_only=0.999, write_file=0.998, edit_file=0.940, apply_patch=0.871, run_bash=0.727
- Top confusions: [(561, 'grep_search', 'read_file'), (501, 'read_file', 'grep_search'), (312, 'read_file', 'list_directory'), (276, 'grep_search', 'list_directory'), (263, 'list_directory', 'read_file'), (255, 'glob_pattern', 'grep_search'), (213, 'glob_pattern', 'read_file'), (205, 'grep_search', 'glob_pattern')]
- Prediction distribution: {'apply_patch': 1097, 'ask_user': 479, 'edit_file': 2186, 'glob_pattern': 938, 'grep_search': 1921, 'lint_or_typecheck': 451, 'list_directory': 1083, 'plan_task': 517, 'read_file': 1878, 'respond_only': 1004, 'run_bash': 1000, 'run_tests': 949, 'web_search': 326, 'write_file': 277}
- Runtime or package-size concerns: sparse sklearn model; expected offline CPU inference within limits.
- Decision: discard or revisit
- Next suggested experiment: compare random vs session split and inspect weak classes before widening features.
## 20260701_083007_prompt_svc_session

- Date/time: 2026-07-01 08:30:57 UTC
- Hypothesis: Prompt/action-meta SVC regularization sweep C=0.1.; class bias tuned on validation scores; C override=0.1
- Code/config changes: `tfidf_linearsvc` with current_prompt word+char ngrams.
- Validation setup: session
- Overall Macro-F1: 0.654681
- Per-class observations:
  - Weakest: list_directory=0.388, read_file=0.440, glob_pattern=0.456, web_search=0.487, grep_search=0.495
  - Strongest: respond_only=1.000, write_file=0.995, edit_file=0.932, apply_patch=0.850, run_bash=0.753
- Top confusions: [(498, 'grep_search', 'read_file'), (475, 'read_file', 'grep_search'), (400, 'read_file', 'list_directory'), (304, 'grep_search', 'list_directory'), (234, 'glob_pattern', 'grep_search'), (223, 'list_directory', 'read_file'), (209, 'glob_pattern', 'read_file'), (177, 'grep_search', 'glob_pattern')]
- Prediction distribution: {'apply_patch': 1012, 'ask_user': 531, 'edit_file': 2269, 'glob_pattern': 833, 'grep_search': 1910, 'lint_or_typecheck': 383, 'list_directory': 1337, 'plan_task': 432, 'read_file': 1754, 'respond_only': 1002, 'run_bash': 969, 'run_tests': 1027, 'web_search': 368, 'write_file': 279}
- Runtime or package-size concerns: sparse sklearn model; expected offline CPU inference within limits.
- Decision: discard or revisit
- Next suggested experiment: compare random vs session split and inspect weak classes before widening features.
## 20260701_083007_prompt_svc_session

- Date/time: 2026-07-01 08:30:58 UTC
- Hypothesis: Prompt/action-meta SVC C=0.2 without class_weight to test minority tradeoff.; class bias tuned on validation scores; C override=0.2; class_weight=none
- Code/config changes: `tfidf_linearsvc` with current_prompt word+char ngrams.
- Validation setup: session
- Overall Macro-F1: 0.650566
- Per-class observations:
  - Weakest: list_directory=0.350, read_file=0.441, glob_pattern=0.448, web_search=0.471, grep_search=0.481
  - Strongest: respond_only=1.000, write_file=0.996, edit_file=0.940, apply_patch=0.858, run_bash=0.750
- Top confusions: [(533, 'grep_search', 'read_file'), (511, 'read_file', 'grep_search'), (308, 'read_file', 'list_directory'), (265, 'grep_search', 'list_directory'), (254, 'list_directory', 'read_file'), (247, 'glob_pattern', 'grep_search'), (205, 'glob_pattern', 'read_file'), (202, 'list_directory', 'grep_search')]
- Prediction distribution: {'apply_patch': 1002, 'ask_user': 493, 'edit_file': 2363, 'glob_pattern': 909, 'grep_search': 1948, 'lint_or_typecheck': 405, 'list_directory': 1087, 'plan_task': 537, 'read_file': 1816, 'respond_only': 1002, 'run_bash': 990, 'run_tests': 974, 'web_search': 304, 'write_file': 276}
- Runtime or package-size concerns: sparse sklearn model; expected offline CPU inference within limits.
- Decision: discard or revisit
- Next suggested experiment: compare random vs session split and inspect weak classes before widening features.
## 20260701_083134_prompt_svc_session

- Date/time: 2026-07-01 08:32:26 UTC
- Hypothesis: Prompt/action-meta SVC regularization sweep C=0.05.; class bias tuned on validation scores; C override=0.05
- Code/config changes: `tfidf_linearsvc` with current_prompt word+char ngrams.
- Validation setup: session
- Overall Macro-F1: 0.656725
- Per-class observations:
  - Weakest: list_directory=0.414, read_file=0.429, glob_pattern=0.474, web_search=0.481, grep_search=0.491
  - Strongest: respond_only=1.000, write_file=0.993, edit_file=0.921, apply_patch=0.839, run_bash=0.750
- Top confusions: [(459, 'grep_search', 'read_file'), (443, 'read_file', 'grep_search'), (411, 'read_file', 'list_directory'), (307, 'grep_search', 'list_directory'), (238, 'grep_search', 'glob_pattern'), (195, 'read_file', 'glob_pattern'), (195, 'glob_pattern', 'grep_search'), (190, 'list_directory', 'read_file')]
- Prediction distribution: {'apply_patch': 1005, 'ask_user': 555, 'edit_file': 2223, 'glob_pattern': 1115, 'grep_search': 1786, 'lint_or_typecheck': 458, 'list_directory': 1407, 'plan_task': 436, 'read_file': 1599, 'respond_only': 1003, 'run_bash': 930, 'run_tests': 996, 'web_search': 313, 'write_file': 280}
- Runtime or package-size concerns: sparse sklearn model; expected offline CPU inference within limits.
- Decision: discard or revisit
- Next suggested experiment: compare random vs session split and inspect weak classes before widening features.
## 20260701_083135_prompt_svc_random

- Date/time: 2026-07-01 08:32:28 UTC
- Hypothesis: Chosen prompt/action-meta SVC C=0.1 evaluated on baseline-compatible random split.; class bias tuned on validation scores; C override=0.1
- Code/config changes: `tfidf_linearsvc` with current_prompt word+char ngrams.
- Validation setup: random
- Overall Macro-F1: 0.662099
- Per-class observations:
  - Weakest: list_directory=0.412, read_file=0.440, glob_pattern=0.463, grep_search=0.505, web_search=0.524
  - Strongest: respond_only=1.000, write_file=0.990, edit_file=0.935, apply_patch=0.863, run_bash=0.752
- Top confusions: [(486, 'read_file', 'grep_search'), (464, 'grep_search', 'read_file'), (381, 'read_file', 'list_directory'), (306, 'grep_search', 'list_directory'), (221, 'glob_pattern', 'grep_search'), (216, 'list_directory', 'read_file'), (210, 'glob_pattern', 'read_file'), (177, 'grep_search', 'glob_pattern')]
- Prediction distribution: {'apply_patch': 949, 'ask_user': 465, 'edit_file': 2267, 'glob_pattern': 858, 'grep_search': 1904, 'lint_or_typecheck': 445, 'list_directory': 1338, 'plan_task': 489, 'read_file': 1712, 'respond_only': 1035, 'run_bash': 886, 'run_tests': 1006, 'web_search': 356, 'write_file': 290}
- Runtime or package-size concerns: sparse sklearn model; expected offline CPU inference within limits.
- Decision: discard or revisit
- Next suggested experiment: compare random vs session split and inspect weak classes before widening features.
## 20260701_083300_prompt_svc_session

- Date/time: 2026-07-01 08:33:50 UTC
- Hypothesis: Prompt/action-meta SVC regularization sweep C=0.025.; class bias tuned on validation scores; C override=0.025
- Code/config changes: `tfidf_linearsvc` with current_prompt word+char ngrams.
- Validation setup: session
- Overall Macro-F1: 0.654798
- Per-class observations:
  - Weakest: list_directory=0.413, grep_search=0.455, glob_pattern=0.469, web_search=0.478, read_file=0.489
  - Strongest: respond_only=1.000, write_file=0.987, edit_file=0.907, apply_patch=0.816, run_tests=0.733
- Top confusions: [(728, 'grep_search', 'read_file'), (328, 'read_file', 'list_directory'), (299, 'list_directory', 'read_file'), (290, 'glob_pattern', 'read_file'), (254, 'grep_search', 'list_directory'), (253, 'read_file', 'grep_search'), (195, 'grep_search', 'glob_pattern'), (163, 'plan_task', 'ask_user')]
- Prediction distribution: {'apply_patch': 1023, 'ask_user': 594, 'edit_file': 2229, 'glob_pattern': 971, 'grep_search': 1271, 'lint_or_typecheck': 466, 'list_directory': 1237, 'plan_task': 471, 'read_file': 2473, 'respond_only': 1003, 'run_bash': 866, 'run_tests': 975, 'web_search': 250, 'write_file': 277}
- Runtime or package-size concerns: sparse sklearn model; expected offline CPU inference within limits.
- Decision: discard or revisit
- Next suggested experiment: compare random vs session split and inspect weak classes before widening features.
## 20260701_083420_prompt_svc_random

- Date/time: 2026-07-01 08:35:07 UTC
- Hypothesis: Chosen prompt/action-meta SVC C=0.05 evaluated on baseline-compatible random split.; class bias tuned on validation scores; C override=0.05
- Code/config changes: `tfidf_linearsvc` with current_prompt word+char ngrams.
- Validation setup: random
- Overall Macro-F1: 0.662137
- Per-class observations:
  - Weakest: list_directory=0.426, glob_pattern=0.463, read_file=0.479, grep_search=0.485, web_search=0.487
  - Strongest: respond_only=1.000, write_file=0.990, edit_file=0.923, apply_patch=0.858, run_bash=0.755
- Top confusions: [(671, 'grep_search', 'read_file'), (350, 'read_file', 'grep_search'), (324, 'read_file', 'list_directory'), (306, 'glob_pattern', 'read_file'), (276, 'list_directory', 'read_file'), (263, 'grep_search', 'list_directory'), (167, 'glob_pattern', 'grep_search'), (161, 'ask_user', 'plan_task')]
- Prediction distribution: {'apply_patch': 970, 'ask_user': 529, 'edit_file': 2132, 'glob_pattern': 736, 'grep_search': 1526, 'lint_or_typecheck': 440, 'list_directory': 1222, 'plan_task': 564, 'read_file': 2356, 'respond_only': 1035, 'run_bash': 932, 'run_tests': 971, 'web_search': 295, 'write_file': 292}
- Runtime or package-size concerns: sparse sklearn model; expected offline CPU inference within limits.
- Decision: discard or revisit
- Next suggested experiment: compare random vs session split and inspect weak classes before widening features.
## 20260701_083541_prompt_svc_session

- Date/time: 2026-07-01 08:36:30 UTC
- Hypothesis: Final candidate: prompt/action-meta LinearSVC, balanced class weights, C=0.05, session-tuned class bias, refit on all training data.; class bias tuned on validation scores; C override=0.05
- Code/config changes: `tfidf_linearsvc` with current_prompt word+char ngrams.
- Validation setup: session
- Overall Macro-F1: 0.656725
- Per-class observations:
  - Weakest: list_directory=0.414, read_file=0.429, glob_pattern=0.474, web_search=0.481, grep_search=0.491
  - Strongest: respond_only=1.000, write_file=0.993, edit_file=0.921, apply_patch=0.839, run_bash=0.750
- Top confusions: [(459, 'grep_search', 'read_file'), (443, 'read_file', 'grep_search'), (411, 'read_file', 'list_directory'), (307, 'grep_search', 'list_directory'), (238, 'grep_search', 'glob_pattern'), (195, 'read_file', 'glob_pattern'), (195, 'glob_pattern', 'grep_search'), (190, 'list_directory', 'read_file')]
- Prediction distribution: {'apply_patch': 1005, 'ask_user': 555, 'edit_file': 2223, 'glob_pattern': 1115, 'grep_search': 1786, 'lint_or_typecheck': 458, 'list_directory': 1407, 'plan_task': 436, 'read_file': 1599, 'respond_only': 1003, 'run_bash': 930, 'run_tests': 996, 'web_search': 313, 'write_file': 280}
- Runtime or package-size concerns: sparse sklearn model; expected offline CPU inference within limits.
- Decision: discard or revisit
- Next suggested experiment: compare random vs session split and inspect weak classes before widening features.
