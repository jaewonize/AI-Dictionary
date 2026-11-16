# 다른 PC 환경 세팅 점검표

다른 PC에서 사진 색인 품질 대시보드를 띄우고, 그 PC에 이미 설치된 hindsight에 분석 결과 은행을 옮겨 오기 위한 점검표다. Docker는 쓰지 않는다.

| 띄우는 것 | 필수 여부 | 필요한 것 |
| --- | --- | --- |
| 결과 대시보드 | 필수 | git, Node 20 이상 |
| hindsight 은행 복제 | 선택 | 그 PC에 이미 돌고 있는 hindsight(1.2.x 이상) |

자동 점검: `workspace/dashboard`에서 `node scripts/check-env.mjs`를 돌리면 아래 항목 대부분을 확인하고 고칠 방법을 알려 준다.

## 1. 미리 준비(인터넷이 되는 곳에서)

- [ ] `git --version`, `node --version`이 나온다. Node는 **20 이상**이어야 한다.
- [ ] 저장소를 받는다. 폰 폴더 사진이 들어 있어 약 400MB다.
  ```
  git clone -b hackathon https://github.com/tt2-woo/AI-Dictionary.git
  ```
- [ ] `node scripts/check-env.mjs`가 대시보드 항목을 모두 ✓로 보여 준다.
- [ ] hindsight 판을 확인한다. 브라우저로 `http://localhost:8888/version`을 열어 `api_version`이 **1.2 이상**인지 본다. 은행 옮기기(`transfer/import`)는 최근 판에만 있다.
- [ ] hindsight 임베딩 모델이 **다국어 모델**인지 확인한다. 설정 `HINDSIGHT_API_EMBEDDINGS_LOCAL_MODEL=BAAI/bge-m3`. 기본값(`BAAI/bge-small-en-v1.5`)은 영어 전용이라 한국어 의미 검색이 약해진다. 은행을 가져오면 벡터를 그 PC의 모델로 다시 계산한다.
- [ ] 임베딩 모델을 바꿨다면 hindsight를 **한 번 띄워 모델 파일(bge-m3 약 2.2GB)을 미리 받아 둔다.** 당일 회사망에서 받으면 오래 걸리거나 막힐 수 있다.
- [ ] hindsight 전문 검색 방식을 확인한다. 외부 PostgreSQL에 pgroonga가 있으면 `HINDSIGHT_API_TEXT_SEARCH_EXTENSION=pgroonga`. 내장 DB(pg0)는 pgroonga를 못 쓰므로 한국어 낱말 검색(BM25)이 약해진다(의미 검색은 정상).
- [ ] hindsight LLM은 **Claude Opus 5.5로 고정**한다(`HINDSIGHT_API_LLM_PROVIDER=anthropic`, `HINDSIGHT_API_LLM_MODEL=claude-opus-5-5`). 키는 `HINDSIGHT_API_LLM_API_KEY`에 그 PC에서 직접 넣고 저장소에 올리지 않는다. 은행 가져오기와 recall에는 LLM이 **필요 없고**, reflect(추론)를 시연할 때만 쓴다.
- [ ] Gemini 4 Argon은 hindsight에 붙이지 않는다. Argon은 사진을 은행에 넣기 전에 사진을 분석해 설명을 쓰는 단계에서만 쓰는 모델이다(Opus 5.5와 비교한 결과가 대시보드다).
- [ ] reflect를 시연하려면 hindsight 설정(`.env`)에 `HINDSIGHT_API_REFLECT_WALL_TIMEOUT=600`을 넣고 hindsight를 다시 띄운다.
  - 기본 300초면 CPU 검색이 느린 PC에서 reflect가 504로 끊길 수 있다. 개발 PC에서 Opus 5.5 reflect 한 번이 247초 걸렸고, LLM은 약 43초, 나머지는 recall 2회(각 약 100초, 임베딩·리랭커)였다.
  - 은행 가져오기·recall·대시보드에는 필요 없다.

## 2. 대시보드 띄우기

```
cd AI-Dictionary\workspace\dashboard
node server.mjs
```

- [ ] 첫 화면 출력에 `다른 PC: http://<이 PC의 IP>:8787`이 나온다.
- [ ] 처음 실행할 때 Windows 방화벽 창이 뜨면 **개인 네트워크 허용**을 누른다. 회사 정책으로 막혀 있으면 관리자에게 TCP 8787 인바운드 허용을 요청하거나, 대시보드를 띄운 PC 브라우저에서만 시연한다.
- [ ] 대시보드를 띄운 PC 브라우저에서 `http://localhost:8787`이 열린다.
- [ ] 다른 PC나 휴대폰(같은 망)에서 `http://<이 PC의 IP>:8787`이 열린다.
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

git clone만으로는 그 PC의 hindsight에 은행이 들어가지 않는다. 아래 가져오기 명령을 **한 번 따로** 실행해야 한다. 대시보드만 쓸 때는 이 절을 건너뛴다.

- 한 번만 하면 된다. 같은 이름의 은행이 이미 있으면 실패하므로, 다시 할 때는 `target_bank_id`를 다른 이름으로 바꾼다.
- hindsight가 8888이 아닌 포트에서 돌면 아래 명령의 `localhost:8888`을 그 포트로 바꾸고, 점검 스크립트에는 `$env:HINDSIGHT_URL="http://localhost:<포트>"`를 준다.

은행 `aidic-scenario`를 통째로 옮긴다. 분석 결과(사실·문서·엔티티)는 그대로 들어가고 **LLM 호출은 없다.** 그 PC의 hindsight가 벡터와 색인만 자기 임베딩 모델로 다시 만든다.

- [ ] 그 PC의 hindsight에 `aidic-scenario` 은행이 **아직 없다**(이 방식은 새 은행으로만 만든다). 이미 있으면 `target_bank_id`를 다른 이름으로 바꾼다.
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

이 은행은 시나리오 시험이 끝나기 전 상태 그대로다. 사진 261장은 모두 있고, 하루 타임라인은 69개 가운데 16개만 들어 있다. 사진 설명은 Claude Opus 5.5가 썼고, 그 설명에서 뽑은 기억(사실)은 hindsight LLM을 Opus 5.5로 고정하기 전에 Gemini 4 Argon이 정리했다. 가져오기는 LLM을 부르지 않으므로 그 PC에서 Google을 부르는 일은 없다. 개발 PC에서 이 은행에 Opus 5.5로 reflect를 돌려도 문제없었다.

## 4. 시연 전 5분 점검

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
| 가져오기 뒤 검색 품질이 개발 PC보다 낮다 | 그 PC의 임베딩 모델·전문 검색 방식이 다르다(1절) |
| reflect가 504로 끊긴다 | `HINDSIGHT_API_REFLECT_WALL_TIMEOUT=600`을 넣고 hindsight를 다시 띄웠는지(1절) |
| 모두 안 될 때 | 인터넷이 되면 claude.ai 대시보드 링크로 같은 내용을 볼 수 있다(사진 포함 공개 링크) |

---

## 6. 발리 실데이터(`test/claude_finish`) + Docker Hindsight 다른 PC 실행 안내

`test/claude_finish` 브랜치에는 발리 실사진 사본(`workspace/var/bali/photos/`), 색인(`index.jsonl`), `without hindsight`·`with hindsight` 채점 결과, 그리고 Hindsight 은행 백업(`workspace/var/bali/bali-trip.zip`)이 함께 들어 있다.

### 6-1. 저장소 받기와 대시보드 바로 보기 (Docker · API 키 불필요)

```powershell
git clone -b test/claude_finish https://github.com/tt2-woo/AI-Dictionary.git
cd AI-Dictionary\workspace\dashboard
node scripts/check-env.mjs
node server.mjs
```

- 대시보드를 띄운 PC 브라우저에서 `http://localhost:8787/#bali` 를 열면 발리 사진 307장, 설명 표본, `without hindsight`(6/15) 및 `with hindsight`(15/15) 채점표·비교표·비용표가 바로 열린다.

### 6-2. Python 실행 환경 세팅 (단위 시험 · 채점 재실행용)

- Python 3.11 + `uv`가 필요하다 (`winget install --id=astral-sh.uv -e`).
- `AI-Dictionary\workspace` 폴더에서:
  ```powershell
  cd AI-Dictionary\workspace
  uv sync
  copy env.example .env
  ```
- 새로 실행(채점 등)을 돌릴 때만 `workspace/.env`의 `ANTHROPIC_API_KEY=` 뒤에 키를 채운다.
- 단위 시험 확인(API 호출 없음):
  ```powershell
  uv run pytest -q
  ```

### 6-3. Docker로 Hindsight(`pgroonga` + `claude-opus-5-5`) 띄우고 `bali-trip` 은행 복원하기

1. 같은 상위 폴더에 포크한 `hindsight` 저장소(`dev` 브랜치)를 받는다:
   ```powershell
   git clone -b dev https://github.com/tt2-woo/hindsight.git
   cd hindsight
   ```
2. `hindsight/.env` 파일을 만들고(반드시 `.env`는 커밋 금지) `reflect` 실행 시 쓸 키를 적는다(`recall`만 쓸 때는 빈 키여도 기동 가능하나, `reflect` 재실행 시 유효한 키 필요):
   ```ini
   HINDSIGHT_API_LLM_API_KEY=sk-ant-...
   ```
3. Docker Desktop을 켠 뒤 `pgroonga` + AI Dictionary 설정으로 DB와 Hindsight 서버를 띄운다(첫 실행 시 `bge-m3` 임베딩 모델을 받아 약 1~3분 소요):
   ```powershell
   docker compose --env-file .env `
     -f docker/docker-compose/pgroonga/docker-compose.yaml `
     -f docker/docker-compose/pgroonga/docker-compose.aidic.yaml `
     up -d
   ```
   - 상태 확인: `curl.exe http://localhost:8888/version` (`"observations": false` 확인), 웹 UI는 `http://localhost:9999`
4. `AI-Dictionary` 폴더에서 `bali-trip` 은행을 **LLM 호출 없이(\$0)** 그대로 복원한다:
   ```powershell
   cd ..\AI-Dictionary
   curl.exe -X POST "http://localhost:8888/v1/default/banks/bali-trip/transfer/import?mode=restore&target_bank_id=bali-trip" -F "file=@workspace/var/bali/bali-trip.zip"
   ```
   - 복원 완료 확인(`completed` 및 문서 `307`개):
     ```powershell
     curl.exe "http://localhost:8888/v1/default/banks/bali-trip/operations?limit=3"
     curl.exe "http://localhost:8888/v1/default/banks/bali-trip/stats"
     ```

### 6-4. 다른 PC에서 채점 스크립트 다시 돌려보기(선택)

- `AI-Dictionary\workspace`에서 `var\bali\run.cmd`는 경로가 자동으로 잡히도록 상대 경로로 작성되어 있다:
  ```powershell
  cd AI-Dictionary\workspace
  var\bali\run.cmd status_check eval.hindsight status
  var\bali\run.cmd eval_b01 eval.run B01
  var\bali\run.cmd hs_eval_b01 eval.hindsight eval B01
  ```

