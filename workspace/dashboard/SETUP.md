# 시험장 PC 환경 점검표

시험장 PC에서 사진 색인 품질 대시보드를 띄우고, 이미 설치된 hindsight에 분석 결과 은행을 옮겨 오기 위한 점검표다. Docker는 쓰지 않는다.

| 띄우는 것 | 필수 여부 | 필요한 것 |
| --- | --- | --- |
| 결과 대시보드 | 필수 | git, Node 20 이상 |
| hindsight 은행 복제 | 선택 | 시험장 PC에 이미 돌고 있는 hindsight(1.2.x 이상) |

자동 점검: `workspace/dashboard`에서 `node scripts/check-env.mjs`를 돌리면 아래 항목 대부분을 확인하고 고칠 방법을 알려 준다.

## 1. 전날 준비(인터넷이 되는 곳에서)

- [ ] `git --version`, `node --version`이 나온다. Node는 **20 이상**이어야 한다.
- [ ] 저장소를 받는다. 폰 폴더 사진이 들어 있어 약 400MB다.
  ```
  git clone -b feat/dashboard https://github.com/tt2-woo/AI-Dictionary.git
  ```
- [ ] `node scripts/check-env.mjs`가 대시보드 항목을 모두 ✓로 보여 준다.
- [ ] hindsight 판을 확인한다. 브라우저로 `http://localhost:8888/version`을 열어 `api_version`이 **1.2 이상**인지 본다. 은행 옮기기(`transfer/import`)는 최근 판에만 있다.
- [ ] hindsight 임베딩 모델이 **다국어 모델**인지 확인한다. 설정 `HINDSIGHT_API_EMBEDDINGS_LOCAL_MODEL=BAAI/bge-m3`. 기본값(`BAAI/bge-small-en-v1.5`)은 영어 전용이라 한국어 의미 검색이 약해진다. 은행을 가져오면 벡터를 시험장 모델로 다시 계산한다.
- [ ] 임베딩 모델을 바꿨다면 hindsight를 **한 번 띄워 모델 파일(bge-m3 약 2.2GB)을 미리 받아 둔다.** 당일 회사망에서 받으면 오래 걸리거나 막힐 수 있다.
- [ ] hindsight 전문 검색 방식을 확인한다. 외부 PostgreSQL에 pgroonga가 있으면 `HINDSIGHT_API_TEXT_SEARCH_EXTENSION=pgroonga`. 내장 DB(pg0)는 pgroonga를 못 쓰므로 한국어 낱말 검색(BM25)이 약해진다(의미 검색은 정상).
- [ ] LLM 키: 은행 가져오기와 recall에는 **필요 없다**. reflect(추론)를 시연하려면 필요하다. Vertex AI 프로젝트는 정지 상태이므로 Claude(`HINDSIGHT_API_LLM_PROVIDER=anthropic`, `HINDSIGHT_API_LLM_MODEL=claude-opus-5-5`)로 둔다. 키는 PC에서 직접 넣고 저장소에 올리지 않는다.
- [ ] Gemini를 다시 쓴다면 동시 호출을 1~2개로, 재시도는 3회 이하로 둔다. 오늘 Vertex 프로젝트 정지는 짧은 시간에 몰린 재시도 요청이 원인으로 보인다.

## 2. 대시보드 띄우기

```
cd AI-Dictionary\workspace\dashboard
node server.mjs
```

- [ ] 첫 화면 출력에 `다른 PC: http://<시험장PC-IP>:8787`이 나온다.
- [ ] 처음 실행할 때 Windows 방화벽 창이 뜨면 **개인 네트워크 허용**을 누른다. 회사 정책으로 막혀 있으면 관리자에게 TCP 8787 인바운드 허용을 요청하거나, 시험장 PC 브라우저에서만 시연한다.
- [ ] 시험장 PC 브라우저에서 `http://localhost:8787`이 열린다.
- [ ] 다른 PC나 휴대폰(같은 망)에서 `http://<시험장PC-IP>:8787`이 열린다.
- [ ] 오른쪽 위 "결과 내보낸 시각"이 보이고, 위쪽 노란 경고(없는 결과 파일)가 없다.
- [ ] 사진별 설명 비교의 썸네일 24장이 모두 보인다.
- [ ] 아래 기대값과 화면 숫자가 같다.

| 확인할 곳 | 기대값 |
| --- | --- |
| 한눈에 · Claude Opus 5.5 medium 심사 종합 | 4.38 |
| 한눈에 · Gemini 4 Argon ULTRA_HIGH 심사 종합 | 4.25 |
| 한눈에 · Gemini 4 Argon 기본 심사 종합 | 4.23 |
| 검색 3위 안 적중(Claude medium / Argon ULTRA / Argon 기본) | 100% / 96% / 96% |
| 블라인드 심사 표 · 최고로 뽑힌 사진 수(Claude medium) | 36 |

포트를 바꾸려면 PowerShell에서 `$env:AIDIC_DASHBOARD_PORT=8790; node server.mjs`. 끄려면 Ctrl+C.

## 3. hindsight 은행 가져오기(선택)

은행 `aidic-scenario`를 통째로 옮긴다. 분석 결과(사실·문서·엔티티)는 그대로 들어가고 **LLM 호출은 없다.** 시험장 hindsight가 벡터와 색인만 자기 임베딩 모델로 다시 만든다.

- [ ] 시험장 hindsight에 `aidic-scenario` 은행이 **아직 없다**(이 방식은 새 은행으로만 만든다). 이미 있으면 `target_bank_id`를 다른 이름으로 바꾼다.
- [ ] 저장소 위치(`AI-Dictionary` 폴더)에서 PowerShell로 가져온다.
  ```
  curl.exe -X POST "http://localhost:8888/v1/default/banks/aidic-scenario/transfer/import?mode=restore&target_bank_id=aidic-scenario" -F "file=@workspace/dashboard/hindsight/aidic-scenario.zip"
  ```
- [ ] 진행 상황을 본다. `status`가 `completed`가 될 때까지 기다린다.
  ```
  curl.exe "http://localhost:8888/v1/default/banks/aidic-scenario/operations?limit=5"
  ```
- [ ] 문서 수가 맞는지 본다. 사진 261개 + 하루 타임라인 16개 = **문서 277개**.
  ```
  curl.exe "http://localhost:8888/v1/default/banks/aidic-scenario/documents?limit=1"
  ```
- [ ] recall이 된다(LLM 없이 동작).
  ```
  curl.exe -X POST "http://localhost:8888/v1/default/banks/aidic-scenario/memories/recall" -H "Content-Type: application/json" --data-binary "@workspace/dashboard/hindsight/recall-test.json"
  ```

이 은행은 시나리오 시험이 끝나기 전 상태 그대로다. 사진 261장은 모두 있고, 하루 타임라인은 69개 가운데 16개만 들어 있다.

## 4. 당일 5분 점검

1. `node scripts/check-env.mjs` → 모두 ✓
2. `node server.mjs` → 다른 PC에서 접속
3. 화면 숫자가 2절의 기대값과 같다
4. (hindsight를 쓴다면) recall 시험 질의가 결과를 돌려준다

## 5. 문제가 생기면

| 증상 | 확인할 것 |
| --- | --- |
| `node`가 없다고 나온다 | Node 20 이상 설치. 회사 PC라 설치가 막히면 Node 공식 사이트의 zip(설치 없이 압축만 푸는 판)을 받아 그 폴더의 `node.exe`로 실행한다 |
| 사진이 안 나온다 | `AI-Dictionary\Mobile Storage` 폴더가 있는지. 다른 곳에 두었다면 `$env:AIDIC_PHONE_DIR="경로"` |
| 노란 "없는 결과 파일" 경고 | `workspace\dashboard\data\`의 JSON 파일이 다 받아졌는지 |
| 다른 PC에서 안 열린다 | 같은 망인지, 방화벽 허용, 서버 출력의 IP가 맞는지 |
| `포트 8787을 이미 쓰고 있습니다` | `AIDIC_DASHBOARD_PORT`로 다른 포트 |
| 은행 가져오기가 400·409 | 같은 이름의 은행이 이미 있다 → `target_bank_id`를 바꾼다 |
| 가져오기 뒤 검색 품질이 이 PC보다 낮다 | 시험장 임베딩 모델·전문 검색 방식이 다르다(1절) |
| 모두 안 될 때 | 인터넷이 되면 claude.ai 대시보드 링크로 같은 내용을 볼 수 있다(사진 포함 공개 링크) |
