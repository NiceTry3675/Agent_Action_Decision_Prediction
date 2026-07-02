# 2026-07-02 팀 공유 요약

`submit.zip` 제출 결과 Public Macro-F1 `0.743`을 기록했습니다. 목표였던 Public 0.74를 넘었습니다.

- 구성: XLM-R 5ep replay_last1 cap10000 + OOF rule boosts + sparse SVC ensemble
- OOF 기준: raw 0.739852, old bias 0.741570, 2-stage bias 0.741881
- fixed cross-check: combined 0.751733
- 패키지: `submit.zip` 529MB, `model/` 611MB
- zip 루트 구조: `script.py`, `requirements.txt`, `model/`
- 오프라인/CPU smoke 모두 통과했고, 출력 컬럼은 `id,action`으로 확인됨

결론:

1. 현재 `submit.zip`은 목표 달성 제출물로 고정
2. OOF 0.741881 -> Public 0.743으로 잘 맞았고, fixed 0.751733은 낙관적이었음
3. 다음 작업은 목표 회복이 아니라 리더보드 추가 개선으로 분리해서 진행
4. 개선이 필요하면 mBERT diversity 또는 추가 XLM-R 계열 보강 여부 검토

추가 실험 요약:

- `microsoft/xlm-align-base`: quick 0.133759, 폐기
- `microsoft/infoxlm-base`: quick 0.009553, `read_file`로 붕괴, 폐기
- `microsoft/mdeberta-v3-base`: quick 0.664922, 느리고 XLM-R 경로보다 약해서 보류
- `bert-base-multilingual-cased`: quick 0.697580, Public 미달 시 ensemble diversity 후보로만 유지

현재 결론:

대체 encoder 중 XLM-R finalist를 대체할 모델은 없었습니다. 현재는 XLM-R replay + rule + sparse 조합을 새 기준선으로 두고, 이후 실험은 0.743 이상 개선 목적일 때만 진행하면 됩니다.
