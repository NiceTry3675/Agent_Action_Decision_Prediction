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
## 20260701_085147_gpu_linear_session

- Date/time: 2026-07-01 08:51:47 UTC
- Hypothesis: Move the submission pipeline to a CUDA-first Torch classifier while keeping compact hashed prompt/action/meta features.
- Code/config changes: Torch `linear` hashed n-gram model, epochs=12, lr=0.03, batch=512.
- Validation setup: session
- Overall Macro-F1: 0.567094
- Per-class observations:
  - Weakest: list_directory=0.295, glob_pattern=0.303, web_search=0.345, read_file=0.355, grep_search=0.413
  - Strongest: respond_only=0.999, write_file=0.995, edit_file=0.846, run_bash=0.679, run_tests=0.626
- Top confusions: [(582, 'read_file', 'grep_search'), (504, 'grep_search', 'read_file'), (340, 'read_file', 'glob_pattern'), (337, 'apply_patch', 'edit_file'), (319, 'glob_pattern', 'grep_search'), (308, 'grep_search', 'glob_pattern'), (285, 'edit_file', 'apply_patch'), (276, 'grep_search', 'list_directory')]
- Prediction distribution: {'apply_patch': 924, 'ask_user': 476, 'edit_file': 2283, 'glob_pattern': 1168, 'grep_search': 2007, 'lint_or_typecheck': 379, 'list_directory': 976, 'plan_task': 591, 'read_file': 1629, 'respond_only': 1053, 'run_bash': 1028, 'run_tests': 903, 'web_search': 280, 'write_file': 304}
- Runtime or package-size concerns: CUDA model inference uses Torch only; JSON/token hashing remains CPU-side preprocessing.
- Decision: discard or revisit
- Next suggested experiment: compare linear vs MLP hashed models and tune epochs/lr if GPU validation lags sparse SVC.
## 20260701_085320_gpu_mlp_session

- Date/time: 2026-07-01 08:53:20 UTC
- Hypothesis: Move the submission pipeline to a CUDA-first Torch classifier while keeping compact hashed prompt/action/meta features.
- Code/config changes: Torch `mlp` hashed n-gram model, epochs=10, lr=0.01, batch=512.
- Validation setup: session
- Overall Macro-F1: 0.574049
- Per-class observations:
  - Weakest: list_directory=0.296, glob_pattern=0.303, web_search=0.369, read_file=0.375, grep_search=0.405
  - Strongest: respond_only=0.999, write_file=0.992, edit_file=0.856, run_bash=0.690, apply_patch=0.638
- Top confusions: [(626, 'grep_search', 'read_file'), (547, 'read_file', 'grep_search'), (326, 'apply_patch', 'edit_file'), (318, 'glob_pattern', 'read_file'), (297, 'list_directory', 'read_file'), (285, 'glob_pattern', 'grep_search'), (279, 'read_file', 'list_directory'), (266, 'grep_search', 'list_directory')]
- Prediction distribution: {'apply_patch': 885, 'ask_user': 594, 'edit_file': 2304, 'glob_pattern': 869, 'grep_search': 1873, 'lint_or_typecheck': 401, 'list_directory': 975, 'plan_task': 433, 'read_file': 2016, 'respond_only': 1050, 'run_bash': 1014, 'run_tests': 964, 'web_search': 321, 'write_file': 302}
- Runtime or package-size concerns: CUDA model inference uses Torch only; JSON/token hashing remains CPU-side preprocessing.
- Decision: discard or revisit
- Next suggested experiment: compare linear vs MLP hashed models and tune epochs/lr if GPU validation lags sparse SVC.
## 20260701_085817_gpu_linear_session

- Date/time: 2026-07-01 08:58:17 UTC
- Hypothesis: Move the submission pipeline to a CUDA-first Torch classifier while keeping compact hashed prompt/action/meta features.
- Code/config changes: Torch `linear` hashed n-gram model, epochs=14, lr=0.05, batch=512.
- Validation setup: session
- Overall Macro-F1: 0.556751
- Per-class observations:
  - Weakest: glob_pattern=0.282, list_directory=0.299, web_search=0.353, read_file=0.364, grep_search=0.406
  - Strongest: respond_only=0.998, write_file=0.990, edit_file=0.826, run_bash=0.677, run_tests=0.591
- Top confusions: [(551, 'read_file', 'grep_search'), (544, 'grep_search', 'read_file'), (334, 'apply_patch', 'edit_file'), (332, 'edit_file', 'apply_patch'), (313, 'read_file', 'list_directory'), (291, 'glob_pattern', 'grep_search'), (287, 'grep_search', 'list_directory'), (279, 'glob_pattern', 'read_file')]
- Prediction distribution: {'apply_patch': 986, 'ask_user': 456, 'edit_file': 2199, 'glob_pattern': 956, 'grep_search': 1929, 'lint_or_typecheck': 407, 'list_directory': 1097, 'plan_task': 616, 'read_file': 1769, 'respond_only': 1046, 'run_bash': 1095, 'run_tests': 812, 'web_search': 330, 'write_file': 303}
- Runtime or package-size concerns: CUDA model inference uses Torch only; JSON/token hashing remains CPU-side preprocessing.
- Decision: discard or revisit
- Next suggested experiment: compare linear vs MLP hashed models and tune epochs/lr if GPU validation lags sparse SVC.
## 20260701_085942_gpu_linear_session

- Date/time: 2026-07-01 08:59:42 UTC
- Hypothesis: Move the submission pipeline to a CUDA-first Torch classifier while keeping compact hashed prompt/action/meta features.
- Code/config changes: Torch `linear` hashed n-gram model, epochs=4, lr=0.05, batch=512.
- Validation setup: session
- Overall Macro-F1: 0.574929
- Per-class observations:
  - Weakest: list_directory=0.289, glob_pattern=0.311, read_file=0.363, web_search=0.407, grep_search=0.424
  - Strongest: respond_only=0.999, write_file=0.997, edit_file=0.816, run_bash=0.704, run_tests=0.630
- Top confusions: [(605, 'read_file', 'grep_search'), (526, 'grep_search', 'read_file'), (396, 'edit_file', 'apply_patch'), (347, 'apply_patch', 'edit_file'), (325, 'glob_pattern', 'grep_search'), (290, 'read_file', 'list_directory'), (288, 'read_file', 'glob_pattern'), (263, 'grep_search', 'glob_pattern')]
- Prediction distribution: {'apply_patch': 1011, 'ask_user': 506, 'edit_file': 2175, 'glob_pattern': 993, 'grep_search': 2101, 'lint_or_typecheck': 466, 'list_directory': 957, 'plan_task': 562, 'read_file': 1707, 'respond_only': 1054, 'run_bash': 1022, 'run_tests': 866, 'web_search': 276, 'write_file': 305}
- Runtime or package-size concerns: CUDA model inference uses Torch only; JSON/token hashing remains CPU-side preprocessing.
- Decision: discard or revisit
- Next suggested experiment: compare linear vs MLP hashed models and tune epochs/lr if GPU validation lags sparse SVC.
## 20260701_090149_gpu_linear_session

- Date/time: 2026-07-01 09:01:49 UTC
- Hypothesis: Move the submission pipeline to a CUDA-first Torch classifier while keeping compact hashed prompt/action/meta features.
- Code/config changes: Torch `linear` vocab n-gram model, loss=ovr_squared_hinge, epochs=8, lr=0.03, batch=512.
- Validation setup: session
- Overall Macro-F1: 0.559482
- Per-class observations:
  - Weakest: list_directory=0.286, glob_pattern=0.291, read_file=0.377, grep_search=0.378, web_search=0.421
  - Strongest: respond_only=0.997, write_file=0.993, edit_file=0.797, run_bash=0.681, run_tests=0.597
- Top confusions: [(602, 'grep_search', 'read_file'), (487, 'read_file', 'grep_search'), (417, 'edit_file', 'apply_patch'), (358, 'apply_patch', 'edit_file'), (305, 'glob_pattern', 'read_file'), (300, 'grep_search', 'glob_pattern'), (295, 'read_file', 'glob_pattern'), (284, 'read_file', 'list_directory')]
- Prediction distribution: {'apply_patch': 1035, 'ask_user': 571, 'edit_file': 2194, 'glob_pattern': 1085, 'grep_search': 1697, 'lint_or_typecheck': 489, 'list_directory': 1001, 'plan_task': 401, 'read_file': 1991, 'respond_only': 1054, 'run_bash': 1051, 'run_tests': 828, 'web_search': 297, 'write_file': 307}
- Runtime or package-size concerns: CUDA model inference uses Torch only; JSON/token hashing remains CPU-side preprocessing.
- Decision: discard or revisit
- Next suggested experiment: compare linear vs MLP hashed models and tune epochs/lr if GPU validation lags sparse SVC.
## 20260701_090903_gpu_transformer_session

- Date/time: 2026-07-01 09:09:03 UTC
- Hypothesis: A multilingual transformer fine-tuned on GPU should recover semantic prompt/action cues that sparse GPU models missed.
- Code/config changes: `distilbert-base-multilingual-cased`, max_length=192, epochs=1, lr=2e-05, batch=24.
- Validation setup: session
- Overall Macro-F1: 0.654018
- Per-class observations:
  - Weakest: list_directory=0.451, web_search=0.462, read_file=0.483, lint_or_typecheck=0.512, ask_user=0.519
  - Strongest: respond_only=0.997, write_file=0.911, edit_file=0.907, apply_patch=0.839, run_bash=0.730
- Top confusions: [(468, 'grep_search', 'read_file'), (408, 'read_file', 'list_directory'), (385, 'read_file', 'grep_search'), (303, 'grep_search', 'list_directory'), (222, 'run_bash', 'run_tests'), (215, 'glob_pattern', 'read_file'), (193, 'list_directory', 'read_file'), (175, 'edit_file', 'apply_patch')]
- Prediction distribution: {'apply_patch': 1041, 'ask_user': 536, 'edit_file': 2238, 'glob_pattern': 772, 'grep_search': 1631, 'lint_or_typecheck': 448, 'list_directory': 1430, 'plan_task': 480, 'read_file': 1794, 'respond_only': 1046, 'run_bash': 874, 'run_tests': 1081, 'web_search': 338, 'write_file': 292}
- Runtime or package-size concerns: GPU inference uses packaged HuggingFace weights; package remains under the 1 GB limit.
- Decision: keep as GPU candidate
- Next suggested experiment: tune max_length/epochs or ensemble with sparse GPU logits if transformer under-recognizes file-operation classes.
## 20260701_091700_gpu_transformer_session

- Date/time: 2026-07-01 09:17:00 UTC
- Hypothesis: A multilingual transformer fine-tuned on GPU should recover semantic prompt/action cues that sparse GPU models missed.
- Code/config changes: `distilbert-base-multilingual-cased`, max_length=192, epochs=2, lr=2e-05, batch=24.
- Validation setup: session
- Overall Macro-F1: 0.692934
- Per-class observations:
  - Weakest: list_directory=0.471, web_search=0.506, lint_or_typecheck=0.527, read_file=0.548, plan_task=0.562
  - Strongest: respond_only=0.998, write_file=0.982, edit_file=0.953, apply_patch=0.913, run_bash=0.761
- Top confusions: [(576, 'grep_search', 'read_file'), (419, 'read_file', 'list_directory'), (312, 'grep_search', 'list_directory'), (240, 'glob_pattern', 'read_file'), (223, 'list_directory', 'read_file'), (209, 'read_file', 'grep_search'), (208, 'run_bash', 'run_tests'), (163, 'glob_pattern', 'list_directory')]
- Prediction distribution: {'apply_patch': 947, 'ask_user': 550, 'edit_file': 2281, 'glob_pattern': 782, 'grep_search': 1303, 'lint_or_typecheck': 407, 'list_directory': 1477, 'plan_task': 423, 'read_file': 2172, 'respond_only': 1049, 'run_bash': 877, 'run_tests': 1057, 'web_search': 374, 'write_file': 302}
- Runtime or package-size concerns: GPU inference uses packaged HuggingFace weights; package remains under the 1 GB limit.
- Decision: keep as GPU candidate
- Next suggested experiment: tune max_length/epochs or ensemble with sparse GPU logits if transformer under-recognizes file-operation classes.
## 20260701_092751_gpu_transformer_session

- Date/time: 2026-07-01 09:27:51 UTC
- Hypothesis: A multilingual transformer fine-tuned on GPU should recover semantic prompt/action cues that sparse GPU models missed.
- Code/config changes: `distilbert-base-multilingual-cased`, max_length=192, epochs=3, lr=2e-05, batch=24.
- Validation setup: session
- Overall Macro-F1: 0.709668
- Per-class observations:
  - Weakest: list_directory=0.476, read_file=0.549, web_search=0.549, lint_or_typecheck=0.571, grep_search=0.590
  - Strongest: respond_only=0.998, write_file=0.979, edit_file=0.959, apply_patch=0.925, run_bash=0.774
- Top confusions: [(561, 'grep_search', 'read_file'), (419, 'read_file', 'list_directory'), (309, 'grep_search', 'list_directory'), (237, 'read_file', 'grep_search'), (235, 'glob_pattern', 'read_file'), (209, 'list_directory', 'read_file'), (179, 'run_bash', 'run_tests'), (169, 'glob_pattern', 'list_directory')]
- Prediction distribution: {'apply_patch': 954, 'ask_user': 499, 'edit_file': 2267, 'glob_pattern': 720, 'grep_search': 1398, 'lint_or_typecheck': 503, 'list_directory': 1469, 'plan_task': 477, 'read_file': 2110, 'respond_only': 1054, 'run_bash': 880, 'run_tests': 978, 'web_search': 382, 'write_file': 310}
- Runtime or package-size concerns: GPU inference uses packaged HuggingFace weights; package remains under the 1 GB limit.
- Decision: keep as GPU candidate
- Next suggested experiment: tune max_length/epochs or ensemble with sparse GPU logits if transformer under-recognizes file-operation classes.
## 20260701_093837_gpu_transformer_session

- Date/time: 2026-07-01 09:38:37 UTC
- Hypothesis: A multilingual transformer fine-tuned on GPU should recover semantic prompt/action cues that sparse GPU models missed.
- Code/config changes: `distilbert-base-multilingual-cased`, max_length=192, epochs=3, lr=2e-05, batch=24.
- Validation setup: session
- Overall Macro-F1: 0.710721
- Per-class observations:
  - Weakest: list_directory=0.475, web_search=0.548, read_file=0.553, lint_or_typecheck=0.580, grep_search=0.585
  - Strongest: respond_only=0.999, write_file=0.982, edit_file=0.958, apply_patch=0.919, run_bash=0.770
- Top confusions: [(559, 'grep_search', 'read_file'), (414, 'read_file', 'list_directory'), (315, 'grep_search', 'list_directory'), (225, 'glob_pattern', 'read_file'), (218, 'read_file', 'grep_search'), (211, 'list_directory', 'read_file'), (171, 'run_bash', 'run_tests'), (164, 'glob_pattern', 'list_directory')]
- Prediction distribution: {'apply_patch': 928, 'ask_user': 413, 'edit_file': 2310, 'glob_pattern': 791, 'grep_search': 1335, 'lint_or_typecheck': 516, 'list_directory': 1465, 'plan_task': 551, 'read_file': 2115, 'respond_only': 1049, 'run_bash': 895, 'run_tests': 929, 'web_search': 398, 'write_file': 306}
- Runtime or package-size concerns: GPU inference uses packaged HuggingFace weights; package remains under the 1 GB limit.
- Decision: keep as GPU candidate
- Next suggested experiment: tune max_length/epochs or ensemble with sparse GPU logits if transformer under-recognizes file-operation classes.
## 20260701_100225_gpu_transformer_random

- Date/time: 2026-07-01 10:02:25 UTC
- Hypothesis: A multilingual transformer fine-tuned on GPU should recover semantic prompt/action cues that sparse GPU models missed.
- Code/config changes: `distilbert-base-multilingual-cased`, max_length=192, epochs=3, lr=2e-05, batch=24.
- Validation setup: random
- Overall Macro-F1: 0.722073
- Per-class observations:
  - Weakest: list_directory=0.498, web_search=0.564, read_file=0.568, ask_user=0.572, grep_search=0.598
  - Strongest: respond_only=0.998, write_file=0.987, edit_file=0.966, apply_patch=0.938, run_bash=0.791
- Top confusions: [(586, 'grep_search', 'read_file'), (303, 'read_file', 'list_directory'), (263, 'grep_search', 'list_directory'), (261, 'read_file', 'grep_search'), (237, 'glob_pattern', 'read_file'), (217, 'list_directory', 'read_file'), (162, 'ask_user', 'plan_task'), (142, 'glob_pattern', 'list_directory')]
- Prediction distribution: {'apply_patch': 939, 'ask_user': 491, 'edit_file': 2272, 'glob_pattern': 776, 'grep_search': 1477, 'lint_or_typecheck': 459, 'list_directory': 1251, 'plan_task': 552, 'read_file': 2223, 'respond_only': 1036, 'run_bash': 1003, 'run_tests': 942, 'web_search': 280, 'write_file': 300}
- Runtime or package-size concerns: GPU inference uses packaged HuggingFace weights; package remains under the 1 GB limit.
- Decision: keep as GPU candidate
- Next suggested experiment: tune max_length/epochs or ensemble with sparse GPU logits if transformer under-recognizes file-operation classes.
## 20260701_113611_gpu_transformer_session_current_v1_len160_qv600_stage1_cache_current_smoke

- Date/time: 2026-07-01 11:36:11 UTC
- Hypothesis: A cached multilingual transformer pipeline should make fixed-session screening faster without changing the model family.
- Code/config changes: `distilbert-base-multilingual-cased`, serializer=current_v1, max_length=160, epochs=1, lr=2e-05, batch=24, bucket_multiplier=8.
- Validation setup: session, quick_val_size=600
- Raw Macro-F1: 0.665491
- Overall Macro-F1: 0.665491
- Per-class observations:
  - Weakest: ask_user=0.368, read_file=0.413, run_tests=0.565, plan_task=0.571, web_search=0.575
  - Strongest: respond_only=1.000, write_file=0.927, apply_patch=0.891, edit_file=0.889, run_bash=0.659
- Top confusions: [(18, 'ask_user', 'plan_task'), (14, 'read_file', 'grep_search'), (11, 'list_directory', 'read_file'), (10, 'glob_pattern', 'read_file'), (10, 'run_tests', 'lint_or_typecheck'), (10, 'web_search', 'ask_user'), (10, 'run_bash', 'run_tests'), (8, 'ask_user', 'web_search')]
- Prediction distribution: {'apply_patch': 49, 'ask_user': 33, 'edit_file': 47, 'glob_pattern': 27, 'grep_search': 54, 'lint_or_typecheck': 41, 'list_directory': 43, 'plan_task': 55, 'read_file': 49, 'respond_only': 43, 'run_bash': 39, 'run_tests': 42, 'web_search': 38, 'write_file': 40}
- Runtime or package-size concerns: runtime=208.2s, tokenize=16.0s, train=188.2s, eval=0.3s, artifact_size_mb=520.0.
- Validation logits: experiments/logits/20260701_113611_gpu_transformer_session_current_v1_len160_qv600_stage1_cache_current_smoke_val_logits.pt
- Decision: screening only; require full fixed-session validation
- Next suggested experiment: quick-screen serializer/replay variants, then promote only broad fixed-session improvements to OOF.
## 20260701_114116_gpu_transformer_session_recent_pairs_v1_len160_qv600_stage1_recent_pairs_smoke

- Date/time: 2026-07-01 11:41:16 UTC
- Hypothesis: A cached multilingual transformer pipeline should make fixed-session screening faster without changing the model family.
- Code/config changes: `distilbert-base-multilingual-cased`, serializer=recent_pairs_v1, max_length=160, epochs=1, lr=2e-05, batch=24, bucket_multiplier=8.
- Validation setup: session, quick_val_size=600
- Raw Macro-F1: 0.542845
- Overall Macro-F1: 0.542845
- Per-class observations:
  - Weakest: web_search=0.185, lint_or_typecheck=0.373, ask_user=0.418, read_file=0.432, run_tests=0.455
  - Strongest: respond_only=0.988, write_file=0.964, edit_file=0.635, list_directory=0.625, run_bash=0.529
- Top confusions: [(29, 'web_search', 'ask_user'), (25, 'lint_or_typecheck', 'run_tests'), (23, 'apply_patch', 'edit_file'), (17, 'run_bash', 'run_tests'), (15, 'ask_user', 'plan_task'), (14, 'run_tests', 'run_bash'), (14, 'plan_task', 'ask_user'), (12, 'glob_pattern', 'grep_search')]
- Prediction distribution: {'apply_patch': 32, 'ask_user': 67, 'edit_file': 61, 'glob_pattern': 24, 'grep_search': 54, 'lint_or_typecheck': 16, 'list_directory': 53, 'plan_task': 42, 'read_file': 45, 'respond_only': 42, 'run_bash': 44, 'run_tests': 67, 'web_search': 12, 'write_file': 41}
- Runtime or package-size concerns: runtime=196.4s, tokenize=12.4s, train=179.9s, eval=0.3s, artifact_size_mb=520.0.
- Validation logits: experiments/logits/20260701_114116_gpu_transformer_session_recent_pairs_v1_len160_qv600_stage1_recent_pairs_smoke_val_logits.pt
- Decision: screening only; require full fixed-session validation
- Next suggested experiment: quick-screen serializer/replay variants, then promote only broad fixed-session improvements to OOF.
## 20260701_115211_gpu_transformer_session_current_v1_len160_qv600_replay-last1_stage1_replay_last1_cap10000

- Date/time: 2026-07-01 11:52:11 UTC
- Hypothesis: A cached multilingual transformer pipeline should make fixed-session screening faster without changing the model family.
- Code/config changes: `distilbert-base-multilingual-cased`, serializer=current_v1, replay=last1, max_length=160, epochs=1, lr=2e-05, batch=24, bucket_multiplier=8.
- Validation setup: session, quick_val_size=600
- Raw Macro-F1: 0.692097
- Overall Macro-F1: 0.692097
- Per-class observations:
  - Weakest: ask_user=0.518, plan_task=0.524, read_file=0.566, run_tests=0.578, grep_search=0.602
  - Strongest: respond_only=0.988, write_file=0.964, edit_file=0.870, apply_patch=0.854, glob_pattern=0.667
- Top confusions: [(12, 'run_tests', 'lint_or_typecheck'), (12, 'plan_task', 'ask_user'), (12, 'ask_user', 'plan_task'), (10, 'glob_pattern', 'read_file'), (9, 'grep_search', 'read_file'), (9, 'run_bash', 'run_tests'), (8, 'read_file', 'grep_search'), (8, 'list_directory', 'read_file')]
- Prediction distribution: {'apply_patch': 46, 'ask_user': 42, 'edit_file': 49, 'glob_pattern': 23, 'grep_search': 50, 'lint_or_typecheck': 44, 'list_directory': 45, 'plan_task': 41, 'read_file': 56, 'respond_only': 42, 'run_bash': 41, 'run_tests': 40, 'web_search': 40, 'write_file': 41}
- Runtime or package-size concerns: runtime=229.5s, tokenize=17.9s, train=207.2s, eval=0.4s, artifact_size_mb=520.0.
- Validation logits: experiments/logits/20260701_115211_gpu_transformer_session_current_v1_len160_qv600_replay-last1_stage1_replay_last1_cap10000_val_logits.pt
- Decision: screening only; require full fixed-session validation
- Next suggested experiment: quick-screen serializer/replay variants, then promote only broad fixed-session improvements to OOF.
## 20260701_115640_gpu_transformer_session_current_v1_len160_qv600_replay-last2_stage1_replay_last2_cap10000

- Date/time: 2026-07-01 11:56:40 UTC
- Hypothesis: A cached multilingual transformer pipeline should make fixed-session screening faster without changing the model family.
- Code/config changes: `distilbert-base-multilingual-cased`, serializer=current_v1, replay=last2, max_length=160, epochs=1, lr=2e-05, batch=24, bucket_multiplier=8.
- Validation setup: session, quick_val_size=600
- Raw Macro-F1: 0.691595
- Overall Macro-F1: 0.691595
- Per-class observations:
  - Weakest: ask_user=0.405, read_file=0.515, plan_task=0.578, grep_search=0.591, web_search=0.605
  - Strongest: respond_only=0.977, write_file=0.951, apply_patch=0.903, edit_file=0.899, glob_pattern=0.696
- Top confusions: [(15, 'ask_user', 'plan_task'), (11, 'run_bash', 'run_tests'), (10, 'glob_pattern', 'read_file'), (10, 'grep_search', 'read_file'), (9, 'read_file', 'grep_search'), (9, 'ask_user', 'web_search'), (9, 'web_search', 'ask_user'), (8, 'run_tests', 'lint_or_typecheck')]
- Prediction distribution: {'apply_patch': 50, 'ask_user': 31, 'edit_file': 46, 'glob_pattern': 26, 'grep_search': 45, 'lint_or_typecheck': 37, 'list_directory': 49, 'plan_task': 47, 'read_file': 54, 'respond_only': 45, 'run_bash': 40, 'run_tests': 46, 'web_search': 44, 'write_file': 40}
- Runtime or package-size concerns: runtime=218.5s, tokenize=16.9s, train=196.2s, eval=0.3s, artifact_size_mb=520.0.
- Validation logits: experiments/logits/20260701_115640_gpu_transformer_session_current_v1_len160_qv600_replay-last2_stage1_replay_last2_cap10000_val_logits.pt
- Decision: screening only; require full fixed-session validation
- Next suggested experiment: quick-screen serializer/replay variants, then promote only broad fixed-session improvements to OOF.
## 20260701_120904_gpu_transformer_session_current_v1_len192_replay-last1_stage2_replay_last1_cap10000_len192_ep3

- Date/time: 2026-07-01 12:09:05 UTC
- Hypothesis: A cached multilingual transformer pipeline should make fixed-session screening faster without changing the model family.
- Code/config changes: `distilbert-base-multilingual-cased`, serializer=current_v1, replay=last1, max_length=192, epochs=3, lr=2e-05, batch=24, bucket_multiplier=8.
- Validation setup: session
- Raw Macro-F1: 0.712909
- Overall Macro-F1: 0.718145
- Per-class observations:
  - Weakest: list_directory=0.470, web_search=0.557, read_file=0.562, grep_search=0.592, lint_or_typecheck=0.601
  - Strongest: respond_only=0.999, write_file=0.990, edit_file=0.959, apply_patch=0.921, run_bash=0.771
- Top confusions: [(580, 'grep_search', 'read_file'), (360, 'read_file', 'list_directory'), (283, 'grep_search', 'list_directory'), (247, 'glob_pattern', 'read_file'), (244, 'list_directory', 'read_file'), (233, 'read_file', 'grep_search'), (187, 'run_bash', 'run_tests'), (161, 'ask_user', 'plan_task')]
- Prediction distribution: {'apply_patch': 961, 'ask_user': 378, 'edit_file': 2259, 'glob_pattern': 720, 'grep_search': 1412, 'lint_or_typecheck': 433, 'list_directory': 1320, 'plan_task': 632, 'read_file': 2247, 'respond_only': 1048, 'run_bash': 926, 'run_tests': 1008, 'web_search': 348, 'write_file': 309}
- Runtime or package-size concerns: runtime=650.1s, tokenize=15.3s, train=614.6s, eval=8.4s, artifact_size_mb=520.0.
- Validation logits: experiments/logits/20260701_120904_gpu_transformer_session_current_v1_len192_replay-last1_stage2_replay_last1_cap10000_len192_ep3_val_logits.pt
- Comparison against current best: improves fixed-session tuned Macro-F1 from 0.710721 to 0.718145 (+0.007424), but weak exploration classes remain the limiting errors.
- Decision: keep as GPU candidate
- Next suggested experiment: run max_length=256, epochs=4, lr=2e-5 with the same replay_last1 cap before deciding whether this direction deserves OOF.
## 20260701_122727_gpu_transformer_session_current_v1_len256_replay-last1_stage2_replay_last1_cap10000_len256_ep4

- Date/time: 2026-07-01 12:27:28 UTC
- Hypothesis: A cached multilingual transformer pipeline should make fixed-session screening faster without changing the model family.
- Code/config changes: `distilbert-base-multilingual-cased`, serializer=current_v1, replay=last1, max_length=256, epochs=4, lr=2e-05, batch=24, bucket_multiplier=8.
- Validation setup: session
- Raw Macro-F1: 0.726448
- Overall Macro-F1: 0.731768
- Per-class observations:
  - Weakest: list_directory=0.489, read_file=0.553, web_search=0.576, grep_search=0.602, glob_pattern=0.614
  - Strongest: respond_only=1.000, write_file=0.992, edit_file=0.963, apply_patch=0.927, run_bash=0.804
- Top confusions: [(480, 'grep_search', 'read_file'), (368, 'read_file', 'list_directory'), (325, 'read_file', 'grep_search'), (286, 'grep_search', 'list_directory'), (183, 'list_directory', 'read_file'), (183, 'glob_pattern', 'read_file'), (163, 'glob_pattern', 'list_directory'), (135, 'ask_user', 'plan_task')]
- Prediction distribution: {'apply_patch': 940, 'ask_user': 479, 'edit_file': 2268, 'glob_pattern': 762, 'grep_search': 1654, 'lint_or_typecheck': 459, 'list_directory': 1382, 'plan_task': 564, 'read_file': 1900, 'respond_only': 1051, 'run_bash': 1013, 'run_tests': 918, 'web_search': 307, 'write_file': 304}
- Runtime or package-size concerns: runtime=1003.5s, tokenize=16.7s, train=965.0s, eval=10.2s, artifact_size_mb=520.0.
- Validation logits: experiments/logits/20260701_122727_gpu_transformer_session_current_v1_len256_replay-last1_stage2_replay_last1_cap10000_len256_ep4_val_logits.pt
- Comparison against current best: improves fixed-session tuned Macro-F1 from 0.718145 to 0.731768 (+0.013623), and from the submitted local fixed score 0.710721 to 0.731768 (+0.021047).
- Decision: keep as GPU candidate
- Next suggested experiment: check lr=1e-5 with 5 epochs at max_length=256 before OOF, because public calibration still suggests this local score may not be enough for 0.74 Public.
## 20260701_124846_gpu_transformer_session_current_v1_len256_replay-last1_stage2_replay_last1_cap10000_len256_ep5_lr1e-5

- Date/time: 2026-07-01 12:48:48 UTC
- Hypothesis: A cached multilingual transformer pipeline should make fixed-session screening faster without changing the model family.
- Code/config changes: `distilbert-base-multilingual-cased`, serializer=current_v1, replay=last1, max_length=256, epochs=5, lr=1e-05, batch=24, bucket_multiplier=8.
- Validation setup: session
- Raw Macro-F1: 0.722781
- Overall Macro-F1: 0.729483
- Per-class observations:
  - Weakest: list_directory=0.498, web_search=0.540, read_file=0.580, grep_search=0.596, lint_or_typecheck=0.612
  - Strongest: respond_only=0.999, write_file=0.990, edit_file=0.960, apply_patch=0.929, run_bash=0.808
- Top confusions: [(571, 'grep_search', 'read_file'), (306, 'read_file', 'list_directory'), (263, 'grep_search', 'list_directory'), (248, 'read_file', 'grep_search'), (237, 'glob_pattern', 'read_file'), (228, 'list_directory', 'read_file'), (156, 'ask_user', 'plan_task'), (149, 'glob_pattern', 'list_directory')]
- Prediction distribution: {'apply_patch': 908, 'ask_user': 427, 'edit_file': 2317, 'glob_pattern': 733, 'grep_search': 1448, 'lint_or_typecheck': 349, 'list_directory': 1264, 'plan_task': 623, 'read_file': 2253, 'respond_only': 1050, 'run_bash': 1047, 'run_tests': 977, 'web_search': 304, 'write_file': 301}
- Runtime or package-size concerns: runtime=1184.0s, tokenize=3.4s, train=1156.8s, eval=11.8s, artifact_size_mb=520.0.
- Validation logits: experiments/logits/20260701_124846_gpu_transformer_session_current_v1_len256_replay-last1_stage2_replay_last1_cap10000_len256_ep5_lr1e-5_val_logits.pt
- Comparison against current best: trails the lr=2e-5, 4-epoch candidate 0.731768 by 0.002285 despite one extra epoch, so lr=1e-5 is not the current promotion path.
- Decision: discard or revisit
- Next suggested experiment: prepare 3-fold session-aware OOF for the max_length=256, epochs=4, lr=2e-5 replay_last1 candidate.
## 20260701_131408_gpu_transformer_session_oof_current_v1_len256_fold0-of3_replay-last1_finalist_oof_len256_ep4_lr2e-5_replay_last1_fold

- Date/time: 2026-07-01 13:14:09 UTC
- Hypothesis: A cached multilingual transformer pipeline should make fixed-session screening faster without changing the model family.
- Code/config changes: `distilbert-base-multilingual-cased`, serializer=current_v1, replay=last1, max_length=256, epochs=4, lr=2e-05, batch=24, bucket_multiplier=8.
- Validation setup: session_oof, fold=0/3
- Raw Macro-F1: 0.721392
- Overall Macro-F1: 0.721392
- Per-class observations:
  - Weakest: list_directory=0.487, read_file=0.563, web_search=0.582, grep_search=0.606, lint_or_typecheck=0.610
  - Strongest: respond_only=0.997, write_file=0.983, edit_file=0.959, apply_patch=0.919, run_bash=0.778
- Top confusions: [(774, 'grep_search', 'read_file'), (578, 'read_file', 'grep_search'), (539, 'read_file', 'list_directory'), (458, 'grep_search', 'list_directory'), (311, 'list_directory', 'read_file'), (288, 'glob_pattern', 'read_file'), (253, 'run_bash', 'run_tests'), (240, 'glob_pattern', 'list_directory')]
- Prediction distribution: {'apply_patch': 1695, 'ask_user': 828, 'edit_file': 3672, 'glob_pattern': 1409, 'grep_search': 2861, 'lint_or_typecheck': 879, 'list_directory': 2116, 'plan_task': 811, 'read_file': 3156, 'respond_only': 1723, 'run_bash': 1542, 'run_tests': 1556, 'web_search': 580, 'write_file': 506}
- Runtime or package-size concerns: runtime=859.5s, tokenize=26.5s, train=810.9s, eval=16.7s, artifact_size_mb=520.0.
- Validation logits: experiments/logits/20260701_131408_gpu_transformer_session_oof_current_v1_len256_fold0-of3_replay-last1_finalist_oof_len256_ep4_lr2e-5_replay_last1_fold_val_logits.pt
- Decision: oof fold complete; aggregate before decision
- Next suggested experiment: quick-screen serializer/replay variants, then promote only broad fixed-session improvements to OOF.
## 20260701_132925_gpu_transformer_session_oof_current_v1_len256_fold1-of3_replay-last1_finalist_oof_len256_ep4_lr2e-5_replay_last1

- Date/time: 2026-07-01 13:29:26 UTC
- Hypothesis: A cached multilingual transformer pipeline should make fixed-session screening faster without changing the model family.
- Code/config changes: `distilbert-base-multilingual-cased`, serializer=current_v1, replay=last1, max_length=256, epochs=4, lr=2e-05, batch=24, bucket_multiplier=8.
- Validation setup: session_oof, fold=1/3
- Raw Macro-F1: 0.721838
- Overall Macro-F1: 0.721838
- Per-class observations:
  - Weakest: list_directory=0.487, read_file=0.556, web_search=0.590, lint_or_typecheck=0.596, grep_search=0.606
  - Strongest: respond_only=0.999, write_file=0.978, edit_file=0.956, apply_patch=0.920, run_bash=0.791
- Top confusions: [(765, 'grep_search', 'read_file'), (621, 'read_file', 'grep_search'), (562, 'read_file', 'list_directory'), (476, 'grep_search', 'list_directory'), (296, 'list_directory', 'read_file'), (269, 'glob_pattern', 'list_directory'), (246, 'glob_pattern', 'read_file'), (234, 'run_bash', 'run_tests')]
- Prediction distribution: {'apply_patch': 1751, 'ask_user': 835, 'edit_file': 3612, 'glob_pattern': 1410, 'grep_search': 2912, 'lint_or_typecheck': 854, 'list_directory': 2215, 'plan_task': 796, 'read_file': 3029, 'respond_only': 1731, 'run_bash': 1604, 'run_tests': 1511, 'web_search': 563, 'write_file': 510}
- Runtime or package-size concerns: runtime=854.7s, tokenize=28.6s, train=802.1s, eval=17.9s, artifact_size_mb=520.0.
- Validation logits: experiments/logits/20260701_132925_gpu_transformer_session_oof_current_v1_len256_fold1-of3_replay-last1_finalist_oof_len256_ep4_lr2e-5_replay_last1_val_logits.pt
- Decision: oof fold complete; aggregate before decision
- Next suggested experiment: quick-screen serializer/replay variants, then promote only broad fixed-session improvements to OOF.
## 20260701_150857_gpu_transformer_session_oof_current_v1_len256_fold2-of3_replay-last1_finalist_oof_len256_ep4_lr2e-5_replay_last1

- Date/time: 2026-07-01 15:08:57 UTC
- Hypothesis: A cached multilingual transformer pipeline should make fixed-session screening faster without changing the model family.
- Code/config changes: `distilbert-base-multilingual-cased`, serializer=current_v1, replay=last1, max_length=256, epochs=4, lr=2e-05, batch=24, bucket_multiplier=8.
- Validation setup: session_oof, fold=2/3
- Raw Macro-F1: 0.720619
- Overall Macro-F1: 0.720619
- Per-class observations:
  - Weakest: list_directory=0.494, read_file=0.543, web_search=0.572, lint_or_typecheck=0.594, ask_user=0.603
  - Strongest: respond_only=0.999, write_file=0.980, edit_file=0.955, apply_patch=0.919, run_bash=0.782
- Top confusions: [(699, 'read_file', 'grep_search'), (674, 'grep_search', 'read_file'), (573, 'read_file', 'list_directory'), (439, 'grep_search', 'list_directory'), (277, 'list_directory', 'read_file'), (256, 'glob_pattern', 'list_directory'), (244, 'glob_pattern', 'read_file'), (231, 'glob_pattern', 'grep_search')]
- Prediction distribution: {'apply_patch': 1724, 'ask_user': 685, 'edit_file': 3651, 'glob_pattern': 1428, 'grep_search': 3112, 'lint_or_typecheck': 876, 'list_directory': 2185, 'plan_task': 890, 'read_file': 2842, 'respond_only': 1727, 'run_bash': 1550, 'run_tests': 1525, 'web_search': 644, 'write_file': 494}
- Runtime or package-size concerns: runtime=915.2s, tokenize=3.6s, train=890.3s, eval=17.0s, artifact_size_mb=520.0.
- Validation logits: experiments/logits/20260701_150857_gpu_transformer_session_oof_current_v1_len256_fold2-of3_replay-last1_finalist_oof_len256_ep4_lr2e-5_replay_last1_val_logits.pt
- Decision: oof fold complete; aggregate before decision
- Next suggested experiment: quick-screen serializer/replay variants, then promote only broad fixed-session improvements to OOF.
## 20260701_oof_current_v1_len256_ep4_lr2e-5_replay_last1_cap10000

- Date/time: 2026-07-01 15:10:07 UTC
- Validation setup: 3-fold session-aware OOF aggregate
- Fold logits: ['experiments/logits/20260701_131408_gpu_transformer_session_oof_current_v1_len256_fold0-of3_replay-last1_finalist_oof_len256_ep4_lr2e-5_replay_last1_fold_val_logits.pt', 'experiments/logits/20260701_132925_gpu_transformer_session_oof_current_v1_len256_fold1-of3_replay-last1_finalist_oof_len256_ep4_lr2e-5_replay_last1_val_logits.pt', 'experiments/logits/20260701_150857_gpu_transformer_session_oof_current_v1_len256_fold2-of3_replay-last1_finalist_oof_len256_ep4_lr2e-5_replay_last1_val_logits.pt']
- Raw OOF Macro-F1: 0.721322
- Tuned OOF Macro-F1: 0.725204
- Weakest classes: list_directory=0.489, web_search=0.577, read_file=0.578, grep_search=0.596, lint_or_typecheck=0.598
- Top confusions: [(2867, 'grep_search', 'read_file'), (1729, 'read_file', 'list_directory'), (1432, 'grep_search', 'list_directory'), (1156, 'read_file', 'grep_search'), (1056, 'list_directory', 'read_file'), (980, 'glob_pattern', 'read_file'), (818, 'run_bash', 'run_tests'), (791, 'glob_pattern', 'list_directory')]
- Prediction distribution: {'apply_patch': 4790, 'ask_user': 2161, 'edit_file': 11339, 'glob_pattern': 4115, 'grep_search': 7048, 'lint_or_typecheck': 2128, 'list_directory': 6694, 'plan_task': 2887, 'read_file': 10809, 'respond_only': 5177, 'run_bash': 4695, 'run_tests': 5080, 'web_search': 1577, 'write_file': 1500}
- Decision: OOF aggregate for current_v1 + replay_last1 cap10000 weight0.5, len256 epochs4 lr2e-5; compare to fixed-session 0.731768 before final refit
- Interpretation: tuned OOF trails the fixed-session tuned score by 0.006564 and remains well below the 0.74 public target, so this is not strong enough for final refit or leaderboard submission yet.
- Next suggested experiment: quick-screen targeted improvements for the persistent `list_directory` / `read_file` / `grep_search` / `web_search` / `lint_or_typecheck` errors, then promote only candidates that beat this OOF baseline.
## 20260701_154129_gpu_transformer_session_hybrid_v1_len160_qv600_replay-last1_stage1_hybrid_replay_last1_cap10000

- Date/time: 2026-07-01 15:41:29 UTC
- Hypothesis: A cached multilingual transformer pipeline should make fixed-session screening faster without changing the model family.
- Code/config changes: `distilbert-base-multilingual-cased`, serializer=hybrid_v1, replay=last1, max_length=160, epochs=1, lr=2e-05, batch=24, bucket_multiplier=8.
- Validation setup: session, quick_val_size=600
- Raw Macro-F1: 0.395243
- Overall Macro-F1: 0.395243
- Per-class observations:
  - Weakest: glob_pattern=0.000, apply_patch=0.000, lint_or_typecheck=0.000, web_search=0.044, read_file=0.290
  - Strongest: respond_only=0.989, write_file=0.953, edit_file=0.642, run_bash=0.500, list_directory=0.493
- Top confusions: [(42, 'apply_patch', 'edit_file'), (35, 'glob_pattern', 'grep_search'), (32, 'web_search', 'ask_user'), (31, 'read_file', 'grep_search'), (30, 'lint_or_typecheck', 'run_tests'), (28, 'plan_task', 'ask_user'), (20, 'list_directory', 'grep_search'), (19, 'run_bash', 'run_tests')]
- Prediction distribution: {'ask_user': 87, 'edit_file': 91, 'grep_search': 127, 'lint_or_typecheck': 2, 'list_directory': 30, 'plan_task': 33, 'read_file': 19, 'respond_only': 44, 'run_bash': 45, 'run_tests': 75, 'web_search': 3, 'write_file': 44}
- Runtime or package-size concerns: runtime=226.7s, tokenize=3.1s, train=218.8s, eval=0.4s, artifact_size_mb=520.0.
- Validation logits: experiments/logits/20260701_154129_gpu_transformer_session_hybrid_v1_len160_qv600_replay-last1_stage1_hybrid_replay_last1_cap10000_val_logits.pt
- Decision: screening only; require full fixed-session validation
- Next suggested experiment: quick-screen serializer/replay variants, then promote only broad fixed-session improvements to OOF.
## 20260701_154605_gpu_transformer_session_compact_events_v1_len160_qv600_replay-last1_stage1_compact_events_replay_last1_cap10000

- Date/time: 2026-07-01 15:46:05 UTC
- Hypothesis: A cached multilingual transformer pipeline should make fixed-session screening faster without changing the model family.
- Code/config changes: `distilbert-base-multilingual-cased`, serializer=compact_events_v1, replay=last1, max_length=160, epochs=1, lr=2e-05, batch=24, bucket_multiplier=8.
- Validation setup: session, quick_val_size=600
- Raw Macro-F1: 0.420960
- Overall Macro-F1: 0.420960
- Per-class observations:
  - Weakest: glob_pattern=0.000, apply_patch=0.000, lint_or_typecheck=0.000, web_search=0.255, read_file=0.290
  - Strongest: respond_only=0.965, write_file=0.952, edit_file=0.614, plan_task=0.533, list_directory=0.519
- Top confusions: [(43, 'apply_patch', 'edit_file'), (35, 'lint_or_typecheck', 'run_tests'), (31, 'glob_pattern', 'grep_search'), (29, 'web_search', 'ask_user'), (27, 'read_file', 'grep_search'), (26, 'run_bash', 'run_tests'), (20, 'plan_task', 'ask_user'), (18, 'list_directory', 'grep_search')]
- Prediction distribution: {'ask_user': 78, 'edit_file': 97, 'glob_pattern': 1, 'grep_search': 113, 'lint_or_typecheck': 1, 'list_directory': 38, 'plan_task': 32, 'read_file': 26, 'respond_only': 42, 'run_bash': 22, 'run_tests': 95, 'web_search': 13, 'write_file': 42}
- Runtime or package-size concerns: runtime=246.2s, tokenize=24.7s, train=216.9s, eval=0.4s, artifact_size_mb=520.0.
- Validation logits: experiments/logits/20260701_154605_gpu_transformer_session_compact_events_v1_len160_qv600_replay-last1_stage1_compact_events_replay_last1_cap10000_val_logits.pt
- Decision: screening only; require full fixed-session validation
- Next suggested experiment: quick-screen serializer/replay variants, then promote only broad fixed-session improvements to OOF.

## Serializer screen conclusion

- Date/time: 2026-07-01 15:46:05 UTC
- Compared against `current_v1 + replay_last1 cap10000`, max_length 160, 1 epoch, quick_val600 baseline: 0.692097.
- `hybrid_v1 + replay_last1 cap10000` scored 0.395243 and missed `glob_pattern`, `apply_patch`, and `lint_or_typecheck` on the quick set.
- `compact_events_v1 + replay_last1 cap10000` scored 0.420960 and also missed `glob_pattern`, `apply_patch`, and `lint_or_typecheck`.
- Decision: do not promote structured serializer variants at max_length 160. Continue with `current_v1 + replay_last1` as the active transformer path, and shift cheap screens to weighting, smoothing, or targeted reranking.

## Class-weight screen conclusion

- Date/time: 2026-07-01 15:55:01 UTC
- Compared against `current_v1 + replay_last1 cap10000`, max_length 160, 1 epoch, quick_val600, `class_weight_power=0.5`: 0.692097.
- `class_weight_power=0.75` scored 0.684071 and hurt `read_file` relative to the baseline.
- `class_weight_power=0.25` scored 0.669840 and weakened `ask_user`, `plan_task`, `read_file`, and `web_search`.
- Decision: keep `class_weight_power=0.5` for the current transformer path. Do not promote 0.25 or 0.75 without a new serializer/model reason.

## Label-smoothing screen conclusion

- Date/time: 2026-07-01 16:03:34 UTC
- Compared against `current_v1 + replay_last1 cap10000`, max_length 160, 1 epoch, quick_val600, `label_smoothing=0.02`: 0.692097.
- `label_smoothing=0.0` scored 0.679076 and hurt `read_file`.
- `label_smoothing=0.05` scored 0.680512 and hurt `ask_user`.
- Decision: keep `label_smoothing=0.02` for the current transformer path. The next cheap axis should be targeted weak-class diagnostics/reranking or a carefully scoped length/lr screen, not smoothing.

## Rule-boost diagnostic conclusion

- Date/time: 2026-07-01 16:17:20 UTC
- Validation setup: deterministic sample/logit rule boosts tuned on 3-fold session-aware OOF logits for the `current_v1 + replay_last1 cap10000`, length-256, 4-epoch finalist.
- OOF result: 0.725204 baseline to 0.728511 with 6 selected rules.
- Fixed-session cross-check: 0.731768 baseline to 0.732621 using the same rule artifact with the fixed-run class bias.
- Rule artifact: `experiments/artifacts/20260702_oof_rule_boosts_current_v1_len256_ep4_replay_last1_rule_boosts.json`
- Code changes: `script.py` now applies `rule_boosts` from transformer `hf_meta.json` when present; `train_transformer.py` can include a rule artifact during final model export via `--rule-boosts-path`.
- Decision: keep rule boosts as a small finalist add-on, but do not final refit or submit from this alone. The gain is consistent but still below the 0.74 public target.

## Markov-prior ensemble conclusion

- Date/time: 2026-07-01 16:28:33 UTC
- Validation setup: fold-aware Markov/action prior fitted without validation sessions and evaluated on the current finalist OOF logits.
- Transformer-only OOF baseline: 0.725204.
- Transformer + Markov prior OOF: 0.725495 at prior weight 0.200; too small to matter.
- Rule-boost baseline OOF: 0.728511.
- Rule boosts + Markov prior + OOF bias retune: 0.729425 at prior weight 0.050.
- Fixed-session cross-check: rule-only fixed validation was 0.732621, while adding the Markov prior dropped to 0.731527; adding the OOF extra bias dropped further to 0.729958.
- Artifacts: `experiments/artifacts/20260702_oof_markov_prior_current_v1_len256_ep4_replay_last1_markov_prior.json`, `experiments/artifacts/20260702_oof_markov_prior_plus_rules_current_v1_len256_ep4_replay_last1_markov_prior.json`, `experiments/artifacts/20260702_oof_markov_prior_plus_rules_bias_current_v1_len256_ep4_replay_last1_markov_prior.json`.
- Decision: reject Markov/action prior for promotion. It is not robust across fixed-session validation and should remain a diagnostic only.

## Sparse SVC ensemble conclusion

- Date/time: 2026-07-01 16:41:36 UTC
- Validation setup: fold-aware TF-IDF LinearSVC OOF scores, trained without validation sessions, ensembled with current finalist transformer logits.
- Sparse-only OOF: 0.518132.
- Transformer-only OOF: 0.725204; transformer + sparse SVC OOF: 0.728315 at sparse weight 0.5.
- Rule-boosted transformer OOF: 0.728511; rule boosts + sparse SVC OOF: 0.730254 at sparse weight 0.5.
- Fixed-session cross-check: transformer baseline 0.731768, rule boosts 0.732621, rule boosts + sparse SVC 0.735248.
- Artifacts: `experiments/artifacts/20260702_oof_sparse_svc_current_v1_len256_ep4_replay_last1_sparse_svc.json`, `experiments/artifacts/20260702_oof_sparse_svc_plus_rules_current_v1_len256_ep4_replay_last1_sparse_svc.json`.
- Sparse OOF logits: `experiments/logits/20260702_oof_sparse_svc_current_v1_len256_ep4_replay_last1_sparse_oof_logits.pt`, `experiments/logits/20260702_oof_sparse_svc_plus_rules_current_v1_len256_ep4_replay_last1_sparse_oof_logits.pt`.
- Decision: keep sparse SVC as the strongest complementary finalist component so far. It improves both OOF and fixed validation, but the OOF score is still below the 0.74 target, so do not final refit or submit yet.
## 20260701_155056_gpu_transformer_session_current_v1_len160_qv600_replay-last1_stage1_current_replay_last1_cwp075

- Date/time: 2026-07-01 15:50:56 UTC
- Hypothesis: A cached multilingual transformer pipeline should make fixed-session screening faster without changing the model family.
- Code/config changes: `distilbert-base-multilingual-cased`, serializer=current_v1, replay=last1, max_length=160, epochs=1, lr=2e-05, batch=24, bucket_multiplier=8.
- Validation setup: session, quick_val_size=600
- Raw Macro-F1: 0.684071
- Overall Macro-F1: 0.684071
- Per-class observations:
  - Weakest: read_file=0.455, ask_user=0.474, grep_search=0.587, web_search=0.588, list_directory=0.593
  - Strongest: respond_only=0.988, write_file=0.965, edit_file=0.899, apply_patch=0.864, run_bash=0.667
- Top confusions: [(14, 'ask_user', 'plan_task'), (11, 'read_file', 'grep_search'), (10, 'ask_user', 'web_search'), (10, 'run_tests', 'lint_or_typecheck'), (9, 'list_directory', 'read_file'), (9, 'run_bash', 'run_tests'), (8, 'web_search', 'ask_user'), (8, 'grep_search', 'read_file')]
- Prediction distribution: {'apply_patch': 45, 'ask_user': 33, 'edit_file': 46, 'glob_pattern': 31, 'grep_search': 49, 'lint_or_typecheck': 42, 'list_directory': 48, 'plan_task': 48, 'read_file': 45, 'respond_only': 42, 'run_bash': 41, 'run_tests': 44, 'web_search': 43, 'write_file': 43}
- Runtime or package-size concerns: runtime=222.3s, tokenize=2.4s, train=215.3s, eval=0.3s, artifact_size_mb=520.0.
- Validation logits: experiments/logits/20260701_155056_gpu_transformer_session_current_v1_len160_qv600_replay-last1_stage1_current_replay_last1_cwp075_val_logits.pt
- Decision: screening only; require full fixed-session validation
- Next suggested experiment: quick-screen serializer/replay variants, then promote only broad fixed-session improvements to OOF.
## 20260701_155501_gpu_transformer_session_current_v1_len160_qv600_replay-last1_stage1_current_replay_last1_cwp025

- Date/time: 2026-07-01 15:55:01 UTC
- Hypothesis: A cached multilingual transformer pipeline should make fixed-session screening faster without changing the model family.
- Code/config changes: `distilbert-base-multilingual-cased`, serializer=current_v1, replay=last1, max_length=160, epochs=1, lr=2e-05, batch=24, bucket_multiplier=8.
- Validation setup: session, quick_val_size=600
- Raw Macro-F1: 0.669840
- Overall Macro-F1: 0.669840
- Per-class observations:
  - Weakest: ask_user=0.433, plan_task=0.482, read_file=0.509, web_search=0.528, list_directory=0.578
  - Strongest: respond_only=0.976, write_file=0.927, apply_patch=0.882, edit_file=0.879, lint_or_typecheck=0.667
- Top confusions: [(16, 'plan_task', 'ask_user'), (15, 'web_search', 'ask_user'), (13, 'glob_pattern', 'read_file'), (13, 'ask_user', 'plan_task'), (11, 'list_directory', 'read_file'), (10, 'grep_search', 'read_file'), (10, 'run_bash', 'run_tests'), (8, 'read_file', 'grep_search')]
- Prediction distribution: {'apply_patch': 50, 'ask_user': 54, 'edit_file': 48, 'glob_pattern': 25, 'grep_search': 43, 'lint_or_typecheck': 38, 'list_directory': 40, 'plan_task': 40, 'read_file': 63, 'respond_only': 41, 'run_bash': 41, 'run_tests': 47, 'web_search': 30, 'write_file': 40}
- Runtime or package-size concerns: runtime=202.0s, tokenize=2.3s, train=195.5s, eval=0.3s, artifact_size_mb=520.0.
- Validation logits: experiments/logits/20260701_155501_gpu_transformer_session_current_v1_len160_qv600_replay-last1_stage1_current_replay_last1_cwp025_val_logits.pt
- Decision: screening only; require full fixed-session validation
- Next suggested experiment: quick-screen serializer/replay variants, then promote only broad fixed-session improvements to OOF.
## 20260701_155929_gpu_transformer_session_current_v1_len160_qv600_replay-last1_stage1_current_replay_last1_ls000

- Date/time: 2026-07-01 15:59:29 UTC
- Hypothesis: A cached multilingual transformer pipeline should make fixed-session screening faster without changing the model family.
- Code/config changes: `distilbert-base-multilingual-cased`, serializer=current_v1, replay=last1, max_length=160, epochs=1, lr=2e-05, batch=24, bucket_multiplier=8.
- Validation setup: session, quick_val_size=600
- Raw Macro-F1: 0.679076
- Overall Macro-F1: 0.679076
- Per-class observations:
  - Weakest: read_file=0.451, ask_user=0.500, plan_task=0.518, grep_search=0.571, list_directory=0.598
  - Strongest: respond_only=0.976, write_file=0.952, edit_file=0.870, apply_patch=0.864, run_bash=0.659
- Top confusions: [(13, 'glob_pattern', 'read_file'), (13, 'plan_task', 'ask_user'), (13, 'ask_user', 'plan_task'), (11, 'read_file', 'grep_search'), (11, 'list_directory', 'read_file'), (10, 'web_search', 'ask_user'), (10, 'grep_search', 'read_file'), (9, 'run_tests', 'lint_or_typecheck')]
- Prediction distribution: {'apply_patch': 45, 'ask_user': 45, 'edit_file': 49, 'glob_pattern': 24, 'grep_search': 48, 'lint_or_typecheck': 42, 'list_directory': 44, 'plan_task': 42, 'read_file': 59, 'respond_only': 41, 'run_bash': 42, 'run_tests': 43, 'web_search': 34, 'write_file': 42}
- Runtime or package-size concerns: runtime=189.6s, tokenize=2.1s, train=183.4s, eval=0.3s, artifact_size_mb=520.0.
- Validation logits: experiments/logits/20260701_155929_gpu_transformer_session_current_v1_len160_qv600_replay-last1_stage1_current_replay_last1_ls000_val_logits.pt
- Decision: screening only; require full fixed-session validation
- Next suggested experiment: quick-screen serializer/replay variants, then promote only broad fixed-session improvements to OOF.
## 20260701_160334_gpu_transformer_session_current_v1_len160_qv600_replay-last1_stage1_current_replay_last1_ls005

- Date/time: 2026-07-01 16:03:34 UTC
- Hypothesis: A cached multilingual transformer pipeline should make fixed-session screening faster without changing the model family.
- Code/config changes: `distilbert-base-multilingual-cased`, serializer=current_v1, replay=last1, max_length=160, epochs=1, lr=2e-05, batch=24, bucket_multiplier=8.
- Validation setup: session, quick_val_size=600
- Raw Macro-F1: 0.680512
- Overall Macro-F1: 0.680512
- Per-class observations:
  - Weakest: ask_user=0.425, read_file=0.514, plan_task=0.556, web_search=0.561, list_directory=0.591
  - Strongest: respond_only=0.976, write_file=0.952, apply_patch=0.864, edit_file=0.860, lint_or_typecheck=0.667
- Top confusions: [(15, 'ask_user', 'plan_task'), (13, 'glob_pattern', 'read_file'), (11, 'grep_search', 'read_file'), (10, 'list_directory', 'read_file'), (10, 'ask_user', 'web_search'), (10, 'web_search', 'ask_user'), (9, 'plan_task', 'ask_user'), (9, 'run_bash', 'run_tests')]
- Prediction distribution: {'apply_patch': 45, 'ask_user': 37, 'edit_file': 50, 'glob_pattern': 24, 'grep_search': 38, 'lint_or_typecheck': 41, 'list_directory': 45, 'plan_task': 47, 'read_file': 62, 'respond_only': 41, 'run_bash': 44, 'run_tests': 44, 'web_search': 40, 'write_file': 42}
- Runtime or package-size concerns: runtime=189.5s, tokenize=2.2s, train=183.3s, eval=0.3s, artifact_size_mb=520.0.
- Validation logits: experiments/logits/20260701_160334_gpu_transformer_session_current_v1_len160_qv600_replay-last1_stage1_current_replay_last1_ls005_val_logits.pt
- Decision: screening only; require full fixed-session validation
- Next suggested experiment: quick-screen serializer/replay variants, then promote only broad fixed-session improvements to OOF.
## 20260702_oof_rule_boosts_current_v1_len256_ep4_replay_last1

- Date/time: 2026-07-01 16:14:31 UTC
- Validation setup: 3-fold session-aware OOF aggregate with deterministic sample/logit rule boosts.
- Baseline OOF Macro-F1: 0.725204
- Boosted OOF Macro-F1: 0.728511
- Selected rules: 6
- Weakest classes: list_directory:0.4939, read_file:0.5809, web_search:0.5933, grep_search:0.5963, lint_or_typecheck:0.6003
- Top confusions: [(2937, 'grep_search', 'read_file'), (1639, 'read_file', 'list_directory'), (1362, 'grep_search', 'list_directory'), (1156, 'read_file', 'grep_search'), (1081, 'list_directory', 'read_file'), (1014, 'glob_pattern', 'read_file'), (786, 'run_bash', 'run_tests'), (757, 'glob_pattern', 'list_directory')]
- Rule artifact: experiments/artifacts/20260702_oof_rule_boosts_current_v1_len256_ep4_replay_last1_rule_boosts.json
- Decision: OOF diagnostic: greedy deterministic sample/logit rule boosts on current_v1 + replay_last1 finalist OOF logits; inspect gain before inference integration
## 20260702_oof_markov_prior_current_v1_len256_ep4_replay_last1

- Date/time: 2026-07-01 16:20:18 UTC
- Validation setup: fold-aware Markov/action prior evaluated on 3-fold session-aware OOF logits.
- Base Macro-F1: 0.725204
- Prior-only Macro-F1: 0.373755
- Best prior weight: 0.200
- Best Macro-F1: 0.725495
- Weakest classes: list_directory:0.4915, read_file:0.5726, web_search:0.5853, lint_or_typecheck:0.5966, grep_search:0.5979
- Top confusions: [(2844, 'grep_search', 'read_file'), (1638, 'read_file', 'list_directory'), (1327, 'grep_search', 'list_directory'), (1307, 'read_file', 'grep_search'), (1079, 'list_directory', 'read_file'), (982, 'glob_pattern', 'read_file'), (846, 'run_bash', 'run_tests'), (749, 'glob_pattern', 'list_directory')]
- Artifact: experiments/artifacts/20260702_oof_markov_prior_current_v1_len256_ep4_replay_last1_markov_prior.json
- Decision: OOF diagnostic: fold-aware Markov/action prior added to current_v1 + replay_last1 finalist logits; no extra bias retune
## 20260702_oof_markov_prior_plus_rules_current_v1_len256_ep4_replay_last1

- Date/time: 2026-07-01 16:23:17 UTC
- Validation setup: fold-aware Markov/action prior evaluated on 3-fold session-aware OOF logits.
- Base Macro-F1: 0.728511
- Prior-only Macro-F1: 0.373755
- Best prior weight: 0.000
- Best Macro-F1: 0.728511
- Weakest classes: list_directory:0.4939, read_file:0.5809, web_search:0.5933, grep_search:0.5963, lint_or_typecheck:0.6003
- Top confusions: [(2937, 'grep_search', 'read_file'), (1639, 'read_file', 'list_directory'), (1362, 'grep_search', 'list_directory'), (1156, 'read_file', 'grep_search'), (1081, 'list_directory', 'read_file'), (1014, 'glob_pattern', 'read_file'), (786, 'run_bash', 'run_tests'), (757, 'glob_pattern', 'list_directory')]
- Artifact: experiments/artifacts/20260702_oof_markov_prior_plus_rules_current_v1_len256_ep4_replay_last1_markov_prior.json
- Decision: OOF diagnostic: fold-aware Markov/action prior added on top of 6 OOF rule boosts for current_v1 + replay_last1 finalist logits; no extra bias retune
## 20260702_oof_markov_prior_plus_rules_bias_current_v1_len256_ep4_replay_last1

- Date/time: 2026-07-01 16:27:29 UTC
- Validation setup: fold-aware Markov/action prior evaluated on 3-fold session-aware OOF logits.
- Base Macro-F1: 0.728511
- Prior-only Macro-F1: 0.373755
- Best prior weight: 0.050
- Best Macro-F1: 0.729425
- Weakest classes: list_directory:0.4943, read_file:0.5749, web_search:0.5945, lint_or_typecheck:0.6008, grep_search:0.6021
- Top confusions: [(2732, 'grep_search', 'read_file'), (1616, 'read_file', 'list_directory'), (1383, 'read_file', 'grep_search'), (1333, 'grep_search', 'list_directory'), (1043, 'list_directory', 'read_file'), (946, 'glob_pattern', 'read_file'), (750, 'glob_pattern', 'list_directory'), (744, 'run_bash', 'run_tests')]
- Artifact: experiments/artifacts/20260702_oof_markov_prior_plus_rules_bias_current_v1_len256_ep4_replay_last1_markov_prior.json
- Decision: OOF diagnostic: fold-aware Markov/action prior plus 6 OOF rule boosts with final class-bias retune; inspect before integration
## 20260702_oof_sparse_svc_plus_rules_current_v1_len256_ep4_replay_last1

- Date/time: 2026-07-01 16:35:46 UTC
- Validation setup: fold-aware TF-IDF LinearSVC OOF scores ensembled with current finalist transformer logits.
- Base Macro-F1: 0.728511
- Sparse-only Macro-F1: 0.518132
- Best sparse weight: 0.500
- Best Macro-F1: 0.730254
- Weakest classes: list_directory:0.4943, read_file:0.5697, grep_search:0.5979, lint_or_typecheck:0.6080, web_search:0.6082
- Top confusions: [(2658, 'grep_search', 'read_file'), (1730, 'read_file', 'list_directory'), (1423, 'grep_search', 'list_directory'), (1348, 'read_file', 'grep_search'), (958, 'list_directory', 'read_file'), (919, 'glob_pattern', 'read_file'), (785, 'glob_pattern', 'list_directory'), (730, 'run_bash', 'run_tests')]
- Artifact: experiments/artifacts/20260702_oof_sparse_svc_plus_rules_current_v1_len256_ep4_replay_last1_sparse_svc.json
- Sparse logits: experiments/logits/20260702_oof_sparse_svc_plus_rules_current_v1_len256_ep4_replay_last1_sparse_oof_logits.pt
- Decision: OOF diagnostic: fold-aware TF-IDF LinearSVC scores ensembled with 6-rule boosted current_v1 + replay_last1 finalist logits; no extra bias retune
## 20260702_oof_sparse_svc_current_v1_len256_ep4_replay_last1

- Date/time: 2026-07-01 16:39:27 UTC
- Validation setup: fold-aware TF-IDF LinearSVC OOF scores ensembled with current finalist transformer logits.
- Base Macro-F1: 0.725204
- Sparse-only Macro-F1: 0.518132
- Best sparse weight: 0.500
- Best Macro-F1: 0.728315
- Weakest classes: list_directory:0.4908, read_file:0.5683, grep_search:0.5980, web_search:0.6003, lint_or_typecheck:0.6054
- Top confusions: [(2609, 'grep_search', 'read_file'), (1777, 'read_file', 'list_directory'), (1466, 'grep_search', 'list_directory'), (1353, 'read_file', 'grep_search'), (943, 'list_directory', 'read_file'), (899, 'glob_pattern', 'read_file'), (802, 'glob_pattern', 'list_directory'), (755, 'run_bash', 'run_tests')]
- Artifact: experiments/artifacts/20260702_oof_sparse_svc_current_v1_len256_ep4_replay_last1_sparse_svc.json
- Sparse logits: experiments/logits/20260702_oof_sparse_svc_current_v1_len256_ep4_replay_last1_sparse_oof_logits.pt
- Decision: OOF diagnostic: fold-aware TF-IDF LinearSVC scores ensembled with current_v1 + replay_last1 finalist logits; no rules, no extra bias retune
## 20260701_slack_xlm_roberta_base_public_07081

- Date/time: 2026-07-01 23:47:20 KST
- Source: Slack handoff by `jinsanroh02` in `#2026-aisw중심대학-디지털-경진대회-ai부문-전체`; message `https://2026aiswai.slack.com/archives/C0BE7C9R2UT/p1782917240220989`, Canvas `F0BEGTEFKUJ`.
- Hypothesis: Replacing `distilbert-base-multilingual-cased` with `xlm-roberta-base` is the next meaningful jump; sparse TF-IDF features appear capped around local 0.64 and DistilBERT public was 0.6983.
- Code/config changes: External teammate run, not yet reproduced in this repo. `xlm-roberta-base` fine-tuned with the teammate `serialize_transformer_sample` format: current prompt, tier/lang/turn/budget/elapsed meta, workspace meta, recent 8 actions, optional last_user/args/results.
- Training setup: epochs=3, lr=2e-5, max_length=192, batch_size=16, class-weighted CrossEntropy with label_smoothing=0.02, class_weight_power=0.5, AdamW weight_decay=0.01, linear warmup 6%, bf16 training, class-bias post tuning, full 70k final refit saved as fp16.
- Validation setup: session split.
- Local session Macro-F1: 0.718793.
- Public Macro-F1: 0.7080569737, reported as the current team best; previous DistilBERT public score was 0.6983.
- Local-public gap: -0.0107.
- Runtime or package-size concerns: 30k inference took 1m 7s; fp16 artifact 556MB and zipped submission 513MB, within the 10 minute and 1GB limits.
- Per-class observations:
  - Weakest shared classes: list_directory=~0.48, read_file=~0.54, web_search=~0.54, lint_or_typecheck=~0.58, grep_search=~0.60.
  - Main confusion region remains file/exploration actions: grep_search, read_file, and list_directory.
- Decision: keep this 3-epoch `xlm-roberta-base` result as the historical public calibration anchor; it is superseded locally by the 5-epoch XLM-R handoff below.
- Next suggested experiment: use the 5-epoch XLM-R baseline as the reference point, then test replay_last1 cap10000 with saved validation logits and 2-stage bias tuning.
## 20260702_slack_xlm_roberta_base_5ep_fixed_07389

- Date/time: 2026-07-02 KST
- Source: teammate handoff shared in the active research thread; no validation logits were saved for this teammate run.
- Hypothesis: `xlm-roberta-base` needs longer fine-tuning than the earlier 3-epoch public submission; 5 epochs may become the fixed-session reference before testing replay or OOF.
- Code/config changes: External teammate run using current_v1-style serialization and the current GitHub `script.py`; only the `model/` artifact changes to XLM-R fp16 weights.
- Training setup: model=xlm-roberta-base, max_length=192, epochs=5, lr=2e-5, batch_size=16, seed=42, class_weight_power=0.5, label_smoothing=0.02, replay=none.
- Validation setup: fixed session split, validation rows=14,001.
- Raw Macro-F1: 0.7354.
- Old bias-tuned Macro-F1: not shared.
- 2-stage bias-tuned Macro-F1: 0.7389.
- Per-class observations:
  - Weakest: list_directory=0.474, read_file=0.561, grep_search=0.596, web_search=0.610, glob_pattern=0.626.
  - Main confusions: grep_search -> read_file=564, read_file -> list_directory=360, grep_search -> list_directory=283.
- Runtime or package-size concerns: fp16 XLM-R artifact is about 556MB, within the 1GB submission limit. Expected Public from fixed 0.7389 is roughly 0.726-0.728, so this is better but still likely below the 0.74 target.
- Decision: promote XLM-R 5ep no-replay to the canonical fixed-session baseline; the older 3ep XLM-R result is now only a historical public anchor.
- Next suggested experiment: run XLM-R 5ep + replay_last1 cap10000 weight0.5 with 2-stage class-bias tuning and saved validation logits; promote to 3-fold session OOF only if replay improves fixed-session score or weak-class F1.
## 20260701_183208_gpu_transformer_session_current_v1_len192_replay-last1_stage2_xlmr_base_len192_ep5_replay_last1_cap1000

- Date/time: 2026-07-01 18:32:08 UTC
- Hypothesis: Replay_last1 may improve the new canonical XLM-R 5ep baseline by adding supervised prior user-action pairs without changing inference packaging.
- Code/config changes: `xlm-roberta-base`, serializer=current_v1, replay=last1, max_length=192, epochs=5, lr=2e-05, batch=16, bucket_multiplier=8.
- Validation setup: session
- Raw Macro-F1: 0.739664
- Old bias-tuned Macro-F1: 0.743909
- Overall Macro-F1: 0.744625
- Per-class observations:
  - Weakest: list_directory=0.505, web_search=0.588, read_file=0.604, grep_search=0.610, glob_pattern=0.627
  - Strongest: respond_only=0.999, write_file=0.993, edit_file=0.977, apply_patch=0.955, run_bash=0.812
- Top confusions: [(564, 'grep_search', 'read_file'), (320, 'read_file', 'list_directory'), (270, 'grep_search', 'list_directory'), (218, 'glob_pattern', 'read_file'), (209, 'list_directory', 'read_file'), (182, 'read_file', 'grep_search'), (154, 'glob_pattern', 'list_directory'), (148, 'ask_user', 'plan_task')]
- Prediction distribution: {'apply_patch': 927, 'ask_user': 466, 'edit_file': 2264, 'glob_pattern': 804, 'grep_search': 1368, 'lint_or_typecheck': 448, 'list_directory': 1300, 'plan_task': 584, 'read_file': 2254, 'respond_only': 1052, 'run_bash': 998, 'run_tests': 921, 'web_search': 306, 'write_file': 309}
- Runtime or package-size concerns: runtime=2411.1s, tokenize=13.4s, train=2368.5s, eval=14.8s, artifact_size_mb=520.0.
- Validation logits: experiments/logits/20260701_183208_gpu_transformer_session_current_v1_len192_replay-last1_stage2_xlmr_base_len192_ep5_replay_last1_cap1000_val_logits.pt
- Decision: promote to 3-fold session OOF. Replay improved fixed-session raw Macro-F1 from the no-replay handoff 0.7354 to 0.739664 and 2-stage tuned Macro-F1 from 0.7389 to 0.744625. `list_directory`, `read_file`, and `grep_search` improved, but `web_search` fell versus the handoff and needs OOF confirmation.
- Next suggested experiment: run 3-fold session-aware OOF for `xlm-roberta-base`, current_v1, max_length 192, epochs 5, replay_last1 cap10000 weight0.5, then tune 2-stage class bias, rule boosts, and sparse SVC ensemble on the aggregate OOF logits.
## 20260701_190742_gpu_transformer_session_oof_current_v1_len192_fold0-of3_replay-last1_oof_xlmr_len192_ep5_replay_last1_cap10000_fold0

- Date/time: 2026-07-01 19:07:42 UTC
- Hypothesis: A cached multilingual transformer pipeline should make fixed-session screening faster without changing the model family.
- Code/config changes: `xlm-roberta-base`, serializer=current_v1, replay=last1, max_length=192, epochs=5, lr=2e-05, batch=16, bucket_multiplier=8.
- Validation setup: session_oof, fold=0/3
- Raw Macro-F1: 0.731585
- Old bias-tuned Macro-F1: not run
- Overall Macro-F1: 0.731585
- Per-class observations:
  - Weakest: list_directory=0.447, read_file=0.539, ask_user=0.592, grep_search=0.601, web_search=0.619
  - Strongest: respond_only=0.999, write_file=0.988, edit_file=0.973, apply_patch=0.948, run_bash=0.801
- Top confusions: [(757, 'grep_search', 'read_file'), (667, 'read_file', 'grep_search'), (578, 'read_file', 'list_directory'), (448, 'grep_search', 'list_directory'), (346, 'list_directory', 'read_file'), (261, 'glob_pattern', 'read_file'), (243, 'list_directory', 'grep_search'), (235, 'ask_user', 'plan_task')]
- Prediction distribution: {'apply_patch': 1650, 'ask_user': 751, 'edit_file': 3684, 'glob_pattern': 1439, 'grep_search': 3070, 'lint_or_typecheck': 814, 'list_directory': 2055, 'plan_task': 839, 'read_file': 3031, 'respond_only': 1728, 'run_bash': 1566, 'run_tests': 1598, 'web_search': 606, 'write_file': 503}
- Runtime or package-size concerns: runtime=2038.4s, tokenize=13.2s, train=1996.0s, eval=24.7s, artifact_size_mb=520.0.
- Validation logits: experiments/logits/20260701_190742_gpu_transformer_session_oof_current_v1_len192_fold0-of3_replay-last1_oof_xlmr_len192_ep5_replay_last1_cap10000_fold0_val_logits.pt
- Decision: oof fold complete; aggregate before decision
- Next suggested experiment: quick-screen serializer/replay variants, then promote only broad fixed-session improvements to OOF.
## 20260701_194151_gpu_transformer_session_oof_current_v1_len192_fold1-of3_replay-last1_oof_xlmr_len192_ep5_replay_last1_cap10000_fold1

- Date/time: 2026-07-01 19:41:51 UTC
- Hypothesis: A cached multilingual transformer pipeline should make fixed-session screening faster without changing the model family.
- Code/config changes: `xlm-roberta-base`, serializer=current_v1, replay=last1, max_length=192, epochs=5, lr=2e-05, batch=16, bucket_multiplier=8.
- Validation setup: session_oof, fold=1/3
- Raw Macro-F1: 0.720607
- Old bias-tuned Macro-F1: not run
- Overall Macro-F1: 0.720607
- Per-class observations:
  - Weakest: list_directory=0.445, read_file=0.548, web_search=0.578, lint_or_typecheck=0.598, grep_search=0.599
  - Strongest: respond_only=1.000, write_file=0.979, edit_file=0.971, apply_patch=0.946, run_bash=0.788
- Top confusions: [(864, 'grep_search', 'read_file'), (618, 'read_file', 'grep_search'), (493, 'read_file', 'list_directory'), (415, 'list_directory', 'read_file'), (399, 'grep_search', 'list_directory'), (298, 'glob_pattern', 'read_file'), (253, 'run_bash', 'run_tests'), (226, 'plan_task', 'ask_user')]
- Prediction distribution: {'apply_patch': 1684, 'ask_user': 850, 'edit_file': 3663, 'glob_pattern': 1490, 'grep_search': 2885, 'lint_or_typecheck': 771, 'list_directory': 1841, 'plan_task': 776, 'read_file': 3358, 'respond_only': 1726, 'run_bash': 1575, 'run_tests': 1614, 'web_search': 589, 'write_file': 511}
- Runtime or package-size concerns: runtime=2039.1s, tokenize=12.5s, train=1997.4s, eval=24.7s, artifact_size_mb=520.0.
- Validation logits: experiments/logits/20260701_194151_gpu_transformer_session_oof_current_v1_len192_fold1-of3_replay-last1_oof_xlmr_len192_ep5_replay_last1_cap10000_fold1_val_logits.pt
- Decision: oof fold complete; aggregate before decision
- Next suggested experiment: quick-screen serializer/replay variants, then promote only broad fixed-session improvements to OOF.
## 20260701_201604_gpu_transformer_session_oof_current_v1_len192_fold2-of3_replay-last1_oof_xlmr_len192_ep5_replay_last1_cap10000_fold2

- Date/time: 2026-07-01 20:16:04 UTC
- Hypothesis: A cached multilingual transformer pipeline should make fixed-session screening faster without changing the model family.
- Code/config changes: `xlm-roberta-base`, serializer=current_v1, replay=last1, max_length=192, epochs=5, lr=2e-05, batch=16, bucket_multiplier=8.
- Validation setup: session_oof, fold=2/3
- Raw Macro-F1: 0.718740
- Old bias-tuned Macro-F1: not run
- Overall Macro-F1: 0.718740
- Per-class observations:
  - Weakest: list_directory=0.423, read_file=0.546, web_search=0.588, lint_or_typecheck=0.592, ask_user=0.605
  - Strongest: respond_only=0.997, write_file=0.992, edit_file=0.969, apply_patch=0.941, run_bash=0.791
- Top confusions: [(892, 'grep_search', 'read_file'), (614, 'read_file', 'grep_search'), (543, 'list_directory', 'read_file'), (442, 'read_file', 'list_directory'), (358, 'glob_pattern', 'read_file'), (336, 'grep_search', 'list_directory'), (261, 'run_bash', 'run_tests'), (227, 'ask_user', 'plan_task')]
- Prediction distribution: {'apply_patch': 1695, 'ask_user': 759, 'edit_file': 3663, 'glob_pattern': 1405, 'grep_search': 2907, 'lint_or_typecheck': 804, 'list_directory': 1609, 'plan_task': 869, 'read_file': 3653, 'respond_only': 1736, 'run_bash': 1523, 'run_tests': 1619, 'web_search': 599, 'write_file': 492}
- Runtime or package-size concerns: runtime=2039.2s, tokenize=12.6s, train=1997.4s, eval=24.7s, artifact_size_mb=520.0.
- Validation logits: experiments/logits/20260701_201604_gpu_transformer_session_oof_current_v1_len192_fold2-of3_replay-last1_oof_xlmr_len192_ep5_replay_last1_cap10000_fold2_val_logits.pt
- Decision: oof fold complete; aggregate before decision
- Next suggested experiment: quick-screen serializer/replay variants, then promote only broad fixed-session improvements to OOF.
## 20260702_oof_xlmr_base_len192_ep5_replay_last1_cap10000

- Date/time: 2026-07-01 20:17:07 UTC
- Validation setup: 3-fold session-aware OOF aggregate
- Fold logits: ['experiments/logits/20260701_190742_gpu_transformer_session_oof_current_v1_len192_fold0-of3_replay-last1_oof_xlmr_len192_ep5_replay_last1_cap10000_fold0_val_logits.pt', 'experiments/logits/20260701_194151_gpu_transformer_session_oof_current_v1_len192_fold1-of3_replay-last1_oof_xlmr_len192_ep5_replay_last1_cap10000_fold1_val_logits.pt', 'experiments/logits/20260701_201604_gpu_transformer_session_oof_current_v1_len192_fold2-of3_replay-last1_oof_xlmr_len192_ep5_replay_last1_cap10000_fold2_val_logits.pt']
- Raw OOF Macro-F1: 0.723739
- Old bias-tuned OOF Macro-F1: 0.728371
- 2-stage tuned OOF Macro-F1: 0.728569
- Weakest classes: list_directory=0.454, read_file=0.566, grep_search=0.589, web_search=0.600, lint_or_typecheck=0.610
- Top confusions: [(3139, 'grep_search', 'read_file'), (1858, 'read_file', 'list_directory'), (1440, 'grep_search', 'list_directory'), (1340, 'list_directory', 'read_file'), (1158, 'glob_pattern', 'read_file'), (997, 'read_file', 'grep_search'), (724, 'ask_user', 'plan_task'), (709, 'glob_pattern', 'list_directory')]
- Prediction distribution: {'apply_patch': 4898, 'ask_user': 2390, 'edit_file': 11152, 'glob_pattern': 4072, 'grep_search': 6554, 'lint_or_typecheck': 1978, 'list_directory': 6508, 'plan_task': 2719, 'read_file': 11597, 'respond_only': 5186, 'run_bash': 5003, 'run_tests': 4915, 'web_search': 1543, 'write_file': 1485}
- Decision: Aggregate 3-fold session OOF for promoted XLM-R replay finalist; compare against fixed-session 0.744625 before rule/sparse ensemble or final refit
## 20260702_oof_rule_boosts_xlmr_base_len192_ep5_replay_last1_cap10000

- Date/time: 2026-07-01 20:22:51 UTC
- Validation setup: 3-fold session-aware OOF aggregate with deterministic sample/logit rule boosts.
- Baseline OOF Macro-F1: 0.728569
- Boosted OOF Macro-F1: 0.735673
- Selected rules: 12
- Weakest classes: list_directory:0.4678, read_file:0.5659, grep_search:0.5893, lint_or_typecheck:0.6173, web_search:0.6263
- Top confusions: [(3110, 'grep_search', 'read_file'), (1954, 'read_file', 'list_directory'), (1476, 'grep_search', 'list_directory'), (1195, 'list_directory', 'read_file'), (1115, 'glob_pattern', 'read_file'), (995, 'read_file', 'grep_search'), (756, 'glob_pattern', 'list_directory'), (726, 'ask_user', 'plan_task')]
- Rule artifact: experiments/artifacts/20260702_oof_rule_boosts_xlmr_base_len192_ep5_replay_last1_cap10000_rule_boosts.json
- Decision: OOF rule boost diagnostic on XLM-R replay finalist; only promote if gain is meaningful over 0.728569 2-stage OOF baseline
## 20260702_oof_sparse_svc_plus_rules_xlmr_base_len192_ep5_replay_last1_cap10000

- Date/time: 2026-07-01 20:35:21 UTC
- Validation setup: fold-aware TF-IDF LinearSVC OOF scores ensembled with current finalist transformer logits.
- Base Macro-F1: 0.735673
- Sparse-only Macro-F1: 0.536014
- Best sparse weight: 1.000
- Old bias-tuned Macro-F1: 0.738590
- Best Macro-F1: 0.739142
- Weakest classes: list_directory:0.4674, read_file:0.5684, grep_search:0.5973, lint_or_typecheck:0.6215, ask_user:0.6308
- Top confusions: [(2999, 'grep_search', 'read_file'), (1653, 'read_file', 'list_directory'), (1310, 'grep_search', 'list_directory'), (1289, 'list_directory', 'read_file'), (1266, 'read_file', 'grep_search'), (1086, 'glob_pattern', 'read_file'), (765, 'ask_user', 'plan_task'), (697, 'glob_pattern', 'list_directory')]
- Artifact: experiments/artifacts/20260702_oof_sparse_svc_plus_rules_xlmr_base_len192_ep5_replay_last1_cap10000_sparse_svc.json
- Sparse logits: experiments/logits/20260702_oof_sparse_svc_plus_rules_xlmr_base_len192_ep5_replay_last1_cap10000_sparse_oof_logits.pt
- Decision: OOF sparse SVC ensemble on top of XLM-R replay plus OOF rule boosts; fold-aware sparse training, tune sparse weight and 2-stage bias on OOF only
## 20260702_oof_sparse_svc_plus_rules_xlmr_base_len192_ep5_replay_last1_cap10000_w4_retune

- Date/time: 2026-07-01 20:56:23 UTC
- Validation setup: 3-fold session-aware OOF, XLM-R replay logits plus OOF rule boosts plus saved fold-aware sparse SVC scores.
- Base rule-boosted Macro-F1: 0.735673
- Sparse-only Macro-F1: 0.536014
- Sparse weight: 4.000
- Combined raw Macro-F1: 0.739852
- Old bias-tuned Macro-F1: 0.741570
- 2-stage tuned Macro-F1: 0.741881
- Weakest classes: list_directory:0.4824, read_file:0.5682, grep_search:0.6035, lint_or_typecheck:0.6298, ask_user:0.6349
- Top confusions: [(2736, 'grep_search', 'read_file'), (1701, 'read_file', 'list_directory'), (1451, 'read_file', 'grep_search'), (1374, 'grep_search', 'list_directory'), (1089, 'list_directory', 'read_file'), (1037, 'glob_pattern', 'read_file'), (756, 'ask_user', 'plan_task'), (728, 'glob_pattern', 'list_directory')]
- Artifact: experiments/artifacts/20260702_oof_sparse_svc_plus_rules_xlmr_base_len192_ep5_replay_last1_cap10000_w4_retune_sparse_weight_retune.json
- Decision: best OOF candidate so far and above 0.74, but final inference would need packaged sparse SVC support plus rule boosts; do not submit until packaging and smoke tests are done.
## 20260702_fixed_sparse_svc_plus_rules_xlmr_base_len192_ep5_replay_last1_cap10000_w4_check

- Date/time: 2026-07-01 20:53:00 UTC
- Validation setup: fixed session split cross-check using saved XLM-R replay fixed logits, OOF rule artifact, and fold-safe sparse SVC trained without validation sessions.
- Base fixed Macro-F1: 0.744625
- Rule-boosted fixed Macro-F1: 0.747755
- Sparse-only fixed Macro-F1: 0.550580
- Combined fixed Macro-F1 at sparse weight 4.0: 0.751733
- Weakest combined classes: list_directory=0.5084, read_file=0.5947, grep_search=0.6097, glob_pattern=0.6301, ask_user=0.6390, web_search=0.6396.
- Decision: fixed split agrees that rules+sparse help, but OOF 0.741881 remains the primary promotion signal. Continue only through packaging/final-refit checks, not additional fixed-only tuning.
## 20260702_final_package_xlmr_replay_rules_sparse_w4

- Date/time: 2026-07-02 KST
- Package: `submit.zip`
- Validation basis: best OOF stack `20260702_oof_sparse_svc_plus_rules_xlmr_base_len192_ep5_replay_last1_cap10000_w4_retune`, 2-stage tuned OOF Macro-F1 0.741881.
- Final refit: `xlm-roberta-base`, current_v1, max_length=192, epochs=5, lr=2e-5, batch=16, replay_last1 cap10000 weight0.5, trained on all 70k labels plus replay examples.
- Inference stack: final fp16 XLM-R transformer, OOF transformer class bias, 12 OOF-derived rule boosts, final sparse SVC artifact, sparse weight=4.0, final ensemble class bias from OOF retune.
- Requirements: `transformers==4.46.3`, `safetensors==0.8.0`, `scikit-learn==1.8.0`, `joblib==1.5.3`.
- Package-size checks: `model/` is 611MB; `submit.zip` is 529MB; archive contains only `script.py`, `requirements.txt`, and `model/`.
- Offline smoke: zip extracted to a clean temp directory, `TRANSFORMERS_OFFLINE=1 HF_DATASETS_OFFLINE=1`, sample data copied as `data/`; `script.py` produced `output/submission.csv`.
- Submission checks: output columns exactly `id,action`; row count matches sample submission; id order matches sample submission; predicted labels are valid; missing `model/` fails clearly.
- Decision: submitted later with reported Public Macro-F1 0.743; see `20260702_final_package_public_0743`.
## 20260701_215145_gpu_transformer_session_current_v1_len192_replay-last1_final_xlmr_len192_ep5_replay_last1_oofbias_rules

- Date/time: 2026-07-01 21:51:45 UTC
- Validation setup: final refit on all labeled rows; no validation rows used.
- Code/config changes: `xlm-roberta-base`, serializer=current_v1, replay=last1, max_length=192, epochs=5, lr=2e-05, batch=16.
- OOF class-bias source: experiments/artifacts/20260702_oof_xlmr_base_len192_ep5_replay_last1_cap10000_oof_metrics.json
- Rule boosts source: experiments/artifacts/20260702_oof_rule_boosts_xlmr_base_len192_ep5_replay_last1_cap10000_rule_boosts.json
- Runtime or package-size concerns: runtime=2840.1s, tokenize=12.4s, train=2818.8s. Initial fp32 artifact was 1082.8MB, then converted to fp16 for the final package; packaged `model/` is 611MB and `submit.zip` is 529MB.
- Decision: final transformer refit complete; next train final sparse SVC artifact and smoke-test offline submission package.
## 20260702_dacon_submission_url_and_public_snapshot

- Date/time: 2026-07-02 KST
- Competition page: https://dacon.io/competitions/official/236694/overview/description
- Submission page: https://dacon.io/competitions/official/236694/mysubmission
- Public leaderboard: https://dacon.io/competitions/official/236694/leaderboard
- Live leaderboard snapshot: rank 1 public Macro-F1 0.77975; rank 4 0.74359; a 0.70805 row matching the prior XLM-R 3ep public anchor is visible.
- Submission state: public web view is logged out. Upload requires a logged-in Dacon account with team access; no repo-local submission CLI is documented.
- Decision: superseded by submitted result `20260702_final_package_public_0743`; reported Public Macro-F1 0.743 meets the 0.74 target.
## 20260701_221707_gpu_transformer_session_current_v1_len192_qv600_stage1_xlm_align_base_len192_ep1_qv600

- Date/time: 2026-07-01 22:17:07 UTC
- Hypothesis: A cached multilingual transformer pipeline should make fixed-session screening faster without changing the model family.
- Code/config changes: `microsoft/xlm-align-base`, serializer=current_v1, replay=none, max_length=192, epochs=1, lr=2e-05, batch=16, bucket_multiplier=8.
- Validation setup: session, quick_val_size=600
- Raw Macro-F1: 0.133759
- Old bias-tuned Macro-F1: not run
- Overall Macro-F1: 0.133759
- Per-class observations:
  - Weakest: write_file=0.000, ask_user=0.000, grep_search=0.030, glob_pattern=0.043, lint_or_typecheck=0.043
  - Strongest: list_directory=0.335, run_tests=0.317, web_search=0.250, apply_patch=0.244, edit_file=0.208
- Top confusions: [(32, 'lint_or_typecheck', 'run_tests'), (26, 'respond_only', 'run_tests'), (26, 'write_file', 'list_directory'), (19, 'plan_task', 'list_directory'), (15, 'apply_patch', 'run_tests'), (13, 'run_bash', 'run_tests'), (11, 'grep_search', 'apply_patch'), (11, 'web_search', 'grep_search')]
- Prediction distribution: {'apply_patch': 88, 'edit_file': 63, 'glob_pattern': 4, 'grep_search': 24, 'lint_or_typecheck': 4, 'list_directory': 124, 'plan_task': 3, 'read_file': 39, 'respond_only': 46, 'run_bash': 39, 'run_tests': 140, 'web_search': 22, 'write_file': 4}
- Runtime or package-size concerns: runtime=443.4s, tokenize=11.3s, train=423.1s, eval=0.6s, artifact_size_mb=610.3.
- Validation logits: experiments/logits/20260701_221707_gpu_transformer_session_current_v1_len192_qv600_stage1_xlm_align_base_len192_ep1_qv600_val_logits.pt
- Decision: discard. Quick-val Macro-F1 0.133759 is far below the XLM-R/DistilBERT screens and misses multiple classes entirely, so do not promote `microsoft/xlm-align-base` to full fixed-session or replay.
- Next suggested experiment: if continuing local model search before a Dacon upload result, quick-screen `microsoft/infoxlm-base` at the same 1-epoch quick-val budget; otherwise prioritize submitting the packaged XLM-R replay + rules + sparse candidate.
## 20260701_222632_gpu_transformer_session_current_v1_len192_qv600_stage1_infoxlm_base_len192_ep1_qv600

- Date/time: 2026-07-01 22:26:32 UTC
- Hypothesis: A cached multilingual transformer pipeline should make fixed-session screening faster without changing the model family.
- Code/config changes: `microsoft/infoxlm-base`, serializer=current_v1, replay=none, max_length=192, epochs=1, lr=2e-05, batch=16, bucket_multiplier=8.
- Validation setup: session, quick_val_size=600
- Raw Macro-F1: 0.009553
- Old bias-tuned Macro-F1: not run
- Overall Macro-F1: 0.009553
- Per-class observations:
  - Weakest: grep_search=0.000, list_directory=0.000, glob_pattern=0.000, edit_file=0.000, write_file=0.000
  - Strongest: read_file=0.134, grep_search=0.000, list_directory=0.000, glob_pattern=0.000, edit_file=0.000
- Top confusions: [(43, 'apply_patch', 'read_file'), (43, 'run_tests', 'read_file'), (43, 'glob_pattern', 'read_file'), (43, 'respond_only', 'read_file'), (43, 'grep_search', 'read_file'), (43, 'ask_user', 'read_file'), (43, 'run_bash', 'read_file'), (43, 'plan_task', 'read_file')]
- Prediction distribution: {'read_file': 600}
- Runtime or package-size concerns: runtime=441.9s, tokenize=10.9s, train=423.0s, eval=0.6s, artifact_size_mb=610.3.
- Validation logits: experiments/logits/20260701_222632_gpu_transformer_session_current_v1_len192_qv600_stage1_infoxlm_base_len192_ep1_qv600_val_logits.pt
- Decision: discard. Quick-val Macro-F1 0.009553 collapsed to `read_file` for all 600 validation rows, so do not promote `microsoft/infoxlm-base` to full fixed-session or replay.
- Next suggested experiment: do not spend more time on XLM-Align or InfoXLM. If continuing local model search before a Dacon upload result, the remaining cheap encoder screen is `microsoft/mdeberta-v3-base` with reduced batch size; otherwise prioritize submitting the packaged XLM-R replay + rules + sparse candidate.
## 20260701_224444_gpu_transformer_session_current_v1_len192_qv600_stage1_mdeberta_v3_base_len192_ep1_qv600

- Date/time: 2026-07-01 22:44:44 UTC
- Hypothesis: A cached multilingual transformer pipeline should make fixed-session screening faster without changing the model family.
- Code/config changes: `microsoft/mdeberta-v3-base`, serializer=current_v1, replay=none, max_length=192, epochs=1, lr=2e-05, batch=8, bucket_multiplier=8. The fast tokenizer path required `protobuf`, so `train_transformer.py` fell back to the slow tokenizer without adding a dependency.
- Validation setup: session, quick_val_size=600
- Raw Macro-F1: 0.664922
- Old bias-tuned Macro-F1: not run
- Overall Macro-F1: 0.664922
- Per-class observations:
  - Weakest: read_file=0.378, ask_user=0.452, web_search=0.493, grep_search=0.536, plan_task=0.583
  - Strongest: respond_only=1.000, write_file=0.988, edit_file=0.889, apply_patch=0.881, run_bash=0.651
- Top confusions: [(19, 'read_file', 'grep_search'), (17, 'ask_user', 'plan_task'), (14, 'web_search', 'ask_user'), (12, 'run_bash', 'run_tests'), (12, 'glob_pattern', 'grep_search'), (11, 'run_tests', 'lint_or_typecheck'), (8, 'plan_task', 'ask_user'), (7, 'web_search', 'plan_task')]
- Prediction distribution: {'apply_patch': 41, 'ask_user': 41, 'edit_file': 47, 'glob_pattern': 22, 'grep_search': 69, 'lint_or_typecheck': 41, 'list_directory': 51, 'plan_task': 53, 'read_file': 31, 'respond_only': 43, 'run_bash': 43, 'run_tests': 46, 'web_search': 31, 'write_file': 41}
- Runtime or package-size concerns: runtime=965.5s, tokenize=34.3s, train=925.6s, eval=1.2s, artifact_size_mb=610.3.
- Validation logits: experiments/logits/20260701_224444_gpu_transformer_session_current_v1_len192_qv600_stage1_mdeberta_v3_base_len192_ep1_qv600_val_logits.pt
- Decision: do not promote. Quick-val Macro-F1 0.664922 only matches the older DistilBERT no-replay screen, trails the active quick baselines, and is slower with the slow-tokenizer fallback.
- Next suggested experiment: no alternative encoder has beaten the XLM-R reference path. Prioritize submitting the packaged XLM-R replay + rules + sparse candidate; only revisit mDeBERTa for ensemble diversity if a public result shows the current package misses the target.
## 20260701_225233_gpu_transformer_session_current_v1_len192_qv600_stage1_mbert_base_len192_ep1_qv600

- Date/time: 2026-07-01 22:52:33 UTC
- Hypothesis: A cached multilingual transformer pipeline should make fixed-session screening faster without changing the model family.
- Code/config changes: `bert-base-multilingual-cased`, serializer=current_v1, replay=none, max_length=192, epochs=1, lr=2e-05, batch=16, bucket_multiplier=8.
- Validation setup: session, quick_val_size=600
- Raw Macro-F1: 0.697580
- Old bias-tuned Macro-F1: not run
- Overall Macro-F1: 0.697580
- Per-class observations:
  - Weakest: read_file=0.455, ask_user=0.500, run_tests=0.591, grep_search=0.609, lint_or_typecheck=0.619
  - Strongest: respond_only=0.989, write_file=0.976, edit_file=0.920, apply_patch=0.911, list_directory=0.667
- Top confusions: [(13, 'ask_user', 'plan_task'), (11, 'run_tests', 'lint_or_typecheck'), (11, 'ask_user', 'web_search'), (11, 'run_bash', 'run_tests'), (10, 'glob_pattern', 'read_file'), (10, 'read_file', 'grep_search'), (9, 'plan_task', 'web_search'), (9, 'grep_search', 'read_file')]
- Prediction distribution: {'apply_patch': 47, 'ask_user': 29, 'edit_file': 44, 'glob_pattern': 28, 'grep_search': 49, 'lint_or_typecheck': 41, 'list_directory': 50, 'plan_task': 47, 'read_file': 45, 'respond_only': 44, 'run_bash': 39, 'run_tests': 45, 'web_search': 50, 'write_file': 42}
- Runtime or package-size concerns: runtime=370.0s, tokenize=13.4s, train=349.9s, eval=0.7s, artifact_size_mb=610.3.
- Validation logits: experiments/logits/20260701_225233_gpu_transformer_session_current_v1_len192_qv600_stage1_mbert_base_len192_ep1_qv600_val_logits.pt
- Decision: keep only as a possible ensemble-diversity candidate. Quick-val Macro-F1 0.697580 is slightly above the DistilBERT replay quick screen but far below the XLM-R finalist path, so do not spend full fixed-session validation time before seeing the Dacon result for the packaged XLM-R candidate.
- Next suggested experiment: submit the packaged XLM-R replay + rules + sparse candidate. If the public score misses 0.74, consider whether mBERT adds complementary OOF errors before any full mBERT training.
## 20260702_final_package_public_0743

- Date/time: 2026-07-02 KST
- Submission: `submit.zip`, final XLM-R replay + OOF rule boosts + sparse SVC weight 4.0 package.
- Reported Public Macro-F1: 0.743.
- Local basis: OOF raw 0.739852, old bias-tuned OOF 0.741570, 2-stage tuned OOF 0.741881. Fixed cross-check was 0.751733.
- Calibration: OOF 2-stage was close to Public (+0.001119), while fixed cross-check was optimistic by about 0.008733.
- Decision: target met. Use this package as the achieved submission baseline; further experiments are leaderboard-improvement work rather than recovery work.
