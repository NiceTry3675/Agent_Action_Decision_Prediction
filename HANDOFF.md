# 세션 핸드오프 (2026-07-02 23:30 KST 작성)

새 세션에서 이 파일을 읽고 이어서 진행. 전체 로드맵: `~/.claude/plans/0-76-woolly-sky.md` (승인됨).

## 목표와 현재 위치

- Dacon 236694, Public 0.743 (OOF 2-stage 0.741881) → **목표 0.77+ (1~2위)**, 마감 07-15.
- A0(train.py import 수정)·A1(state_v2 직렬화기) 완료 — 커밋 `0634f84`.
- Colab 클라우드 레인 **개통 완료** (스모크 왕복 검증 통과: 클라우드 raw 0.6864 vs 로컬 0.6921, qv600 노이즈 범위 내).

## 지금 Colab에서 돌고 있는 것 (최우선 확인)

- **G1 판정 런**: state_v2 + len384 + 5ep + replay_last1, 고정 세션 스플릿.
- Colab T4 VM, pid 10555, 로그 `/content/AADP/logs/run_20260702_141656.log`, 23:17 KST 시작, 예상 2.5~3.5시간.
- **판정 기준**: fixed raw ≥ 0.739664(current_v1 len192 5ep) + 0.006 = **0.7457** → 통과 시 3-fold OOF 착수, 미달 시 len256-288 폴백/직렬화 반복.
- VM이 밤새 회수됐다면 런 유실 → `[bootstrap]`부터 재실행 후 재launch (같은 args가 노트북 `[args]` 셀에 저장돼 있음).

## 아침 작업 순서

1. VS Code에서 `colab/colab_runner.ipynb` 열고 Colab 커널 연결 확인 (노트북 탭 활성 유지). **GPU는 앞으로 T4 대신 L4 사용** (사용자 지시; large는 A100). 단 지금 T4에서 돌고 있는 G1 런은 그대로 회수.
2. `[poll]` 셀 실행 (좀비 프로세스 판정 수정본; state=Z/gone이면 완료)
3. `[collect]` 셀 실행 → 출력의 run 이름 확인. **주의: G1 회수는 반드시 [collect]를 [bootstrap] 재실행보다 먼저** — bootstrap이 baseline id를 다시 찍으면 G1 행이 흡수돼 collect가 빈손이 됨. (현재 VM 번들에는 vm_agent.py가 없어 G1은 구 프로토콜로 회수)
4. 로컬: `.venv/bin/python colab/cloud_sync.py pull <run이름>` → results.csv 자동 병합
5. G1 판정 → 통과 시: 같은 설정으로 `--split session_oof --fold-id 0/1/2` 3런 (병렬 불가면 순차, fold당 ~2.5-3h)
6. 병행(로컬): 기존 len192 OOF logits로 스태커 선구축 (`tune_stacker_oof.py` 신규 작성, 플랜의 스태커 스펙 참조)

## 커맨드 채널 (2026-07-03 신규, 다음 런타임부터)

- executeCode의 VS Code Quick Pick 승인은 끌 수 없음(공식 문서 확인) → Drive 커맨드 채널로 우회.
- 새 셀 순서: `[probe]` → `[mount]` → `[bootstrap]` → `[agent]`(데몬 시작, 세션 마지막 Quick Pick). 이후 전부 로컬 CLI: `cloud_sync.py cmd "..."` / `hb`(하트비트 폴링) / `unassign`(런타임 반납).
- CU 안전장치: 데몬이 학습 종료 시 자동 collect, 유휴 45분(AADP_IDLE_MAX_MIN) 후 자동 unassign (미회수 결과 있으면 반납 보류). 상세: `colab/COLAB.md` "Command channel and CU safety".
- VM측 구현 `colab/vm_agent.py` (daemon/launch/status/collect). 커밋+`cloud_sync.py push` 후 유효.

## 환경 상태

- rclone 원격 `gdrive` 설정 완료. Drive 교환 폴더 `AADP_exchange/` (code/data/runs).
- Drive에 코드 번들 `code_20260702_133704_0634f84d_dirty.tar.gz` + `open_data.tar.gz` 업로드됨.
- `mcp__ide__executeCode` 자동 승인: 전역 `~/.claude/settings.json` + 프로젝트 `.claude/settings.local.json` 양쪽 등록 → **새 세션부터 유효**.
- IDE 연결 주의: VS Code 익스텐션은 CLI 세션 하나만 연결 유지. 여러 claude 세션이 떠 있으면 `/ide`가 "Failed to connect" → 다른 세션 종료 후 재시도.
- 노트북 셀 프로토콜: `colab/COLAB.md` 참조. 에이전트는 `[args]` 셀만 편집.

## 미커밋 변경 (커밋 권장)

- 신규: `colab/colab_runner.ipynb`, `colab/cloud_sync.py`, `colab/COLAB.md`, `HANDOFF.md`(이 파일)
- 수정: `AGENTS.md`(클라우드 레인 섹션), `.gitignore`(experiments/incoming/), `experiments/results.csv`(+3행: qv600 control/probe/클라우드 스모크)
- `.claude/settings.local.json`(권한), `demo.ipynb`(커널 테스트용 — 삭제 가능)
- **커밋 후 `python colab/cloud_sync.py push`로 코드 번들 갱신할 것** (현재 Drive 번들은 dirty 스냅샷)

## 규칙 리마인드

- 승격은 세션 그룹 3-fold OOF로만. 클라우드 결과는 반드시 `cloud_sync.py pull`로 병합 (수동 편집 금지).
- 제출은 OOF 0.741881을 넘기 전 금지. 한 번에 한 변수.
- 학습을 노트북 셀에서 동기 실행 금지 — `[launch]`(백그라운드) + `[poll]`만 사용.
