# 사용 매뉴얼

기준일: 2026-10-25. 명령은 별도 표시가 없으면 프로젝트 폴더의 **관리자 PowerShell**에서 실행한다.

## 1. 빠른 실행

```powershell
cd C:\myprojects\study\dashin_auto_login
$cybosCli = '.\target\i686-pc-windows-msvc\release\dashin-auto-login.exe'

# 수집 없이 로그인·연결·시세 응답만 검증
& $cybosCli login
$LASTEXITCODE

# 로그인·조회 검증 성공 후 수집 배치 실행
& $cybosCli run
$LASTEXITCODE
```

`login` 성공 메시지는 `연결 및 시세 조회 검증 완료`다. `run`은 이어서 배치를 실행하고
`수집 배치 종료 코드: 0`을 출력해야 정상 종료다. 실제 데이터 저장 결과는 수집기 로그와
DB에서도 확인한다. 이미 연결되어 있으면 기존 연결을 재사용한다.

## 2. 실행 조건과 설치

| 항목 | 조건 |
|---|---|
| 운영체제 | Windows |
| CYBOS | CYBOS Plus 설치, 계정·필요한 이용 동의·인증서 준비 |
| 빌드 | Rust MSVC와 Visual Studio C++ x86 빌드 도구 |
| 실행 권한 | 관리자 권한 |
| 인증서 | CYBOS 공동인증 탭에서 사용할 인증서 사전 선택 |
| 수집기 | 수집기 폴더의 `.venv\Scripts\python.exe`, DB 및 수집기 `.env` 설정 |
| 예약 실행 | 동일한 Windows 사용자 계정으로 로그온 상태 유지, PC 켜짐·절전 아님 |

```powershell
rustup target add i686-pc-windows-msvc
cargo build --release --locked
```

기본 빌드 대상은 `.cargo/config.toml`에 지정되어 있다. Windows가 64비트여도 CYBOS COM을
호출하는 이 실행기는 32비트로 빌드한다.

## 3. 공동인증서 비밀번호 등록·변경

```powershell
.\scripts\Set-CybosEnvironment.ps1
```

인증서 비밀번호만 숨김 입력한다. `CYBOS_CERT_PASSWORD`라는 사용자 영구 환경변수에
저장되며 ID·로그인 비밀번호는 필요 없다. 비밀번호를 변경하거나 인증서를 갱신했으면
CYBOS에서 사용할 인증서를 확인하고 위 스크립트로 새 비밀번호를 등록한다.

프로세스 환경변수가 우선이다. 없으면 사용자 영구 환경변수를 읽는다. 예전 터미널에 오래된
값이 남아 있으면 그 세션의 값만 제거한 뒤 다시 실행할 수 있다.

```powershell
Remove-Item Env:CYBOS_CERT_PASSWORD -ErrorAction SilentlyContinue
```

이는 사용자 영구 환경변수를 삭제하지 않는다. 값을 화면에 출력해 확인하지 않는다.
기존 연결이 유효하면 인증정보를 읽지 않고 연결을 재사용할 수 있다.

사용자 영구 저장까지 제거하려면 다음을 실행한다.

```powershell
[Environment]::SetEnvironmentVariable('CYBOS_CERT_PASSWORD', $null, 'User')
Remove-Item Env:CYBOS_CERT_PASSWORD -ErrorAction SilentlyContinue
```

환경변수는 암호화된 비밀 저장소가 아니다. CYBOS 실행기가 비밀번호를 명령행 인자로
받으므로 로컬 프로세스 조회에도 노출될 수 있다. 비밀번호·토큰을 채팅이나 명령 기록에
직접 입력하지 않는다. 작업 스케줄러 XML에는 인증서 비밀번호를 넣지 않는다.

## 4. 명령과 옵션

| 명령 | 수행 내용 |
|---|---|
| `doctor` | 아키텍처·경로·권한·COM 진단; 순수 파일 검사만 하는 명령은 아님 |
| `status` | 실제 Plus 연결 확인 |
| `quote A005930` | 지정한 종목 시세 조회 |
| `login` | 연결 재사용 또는 공동인증서 로그인 후 시세 검증 |
| `login --manual` | 공동인증 로그인 UI에서 직접 인증한 뒤 연결·시세 검증 |
| `run` | 로그인·시세 검증 후 기본 수집 배치 실행 |

공통 옵션:

| 옵션 | 기본값·설명 |
|---|---|
| `--starter` | `C:\DAISHIN\STARTER\ncStarter.exe` |
| `--timeout` | 120초, 1~3600 허용; 연결 대기·조회에 적용, 수집 시간 제한 아님 |
| `--log-dir` | 생략 시 실행 파일 옆 `logs` 폴더 |
| `--help` | 도움말 |

`login`과 `run`의 `--code`는 검증용 종목이며 기본 `A005930`이다.
`run --batch`로 다른 배치의 절대 경로를 지정할 수 있다.

```powershell
& $cybosCli --timeout 180 login --code A000660
& $cybosCli --log-dir .\logs run --batch 'C:\myprojects\study\kr_stocks_dashin_collector\run_schedule.bat'
```

종목코드는 `A`와 숫자 6자리만 허용한다. 배치 경로의 공백은 지원하지만 cmd 특수문자는
허용하지 않는다. 내부 `worker`와 `--log-child`는 직접 사용하지 않는다.

## 5. 수집기 수정본 적용

새 PC나 수집기 복원 후 사용하는 절차다. 이미 최신 수정본을 적용했다면 반복 적용할 필요 없다.

```powershell
.\scripts\Apply-CollectorFix.ps1
# 프로그램 매매 예외 수정만 필요한 경우
.\scripts\Apply-CollectorFix.ps1 -Files collect_investor_trend_min.py
```

기본 대상은 `C:\myprojects\study\kr_stocks_dashin_collector`다. 적용 전 원본 해시를
검사하며, 알 수 없는 수정이 있으면 덮어쓰지 않고 중단한다. 원본은 수집기 폴더 안
`backup-login-fix-날짜-시각` 폴더에 백업한다. 수집 중에는 적용하지 않는다.

수집 순서:

1. `collect_stock_info.py`
2. `collect_market_index_min.py`
3. `collect_investor_trend_min.py`
4. `collect_stock_history.py`

수정된 `daily_runner.py`는 첫 실패에서 후속 실행을 중단하고 실패 코드를 반환한다.
수집기에는 기존 Telegram 알림이 포함되어 있어 실제 `run` 실행 시 알림이 전송될 수 있다.
이전에 저장한 데이터는 다음 단계 실패 시 전체 롤백하지 않는다.

## 6. 로그 확인

| 실행 방법 | 기본 로그 위치 |
|---|---|
| 수동 실행, `--log-dir` 생략 | `target\i686-pc-windows-msvc\release\logs` |
| 제공한 스크립트로 등록한 예약 작업 | 프로젝트의 `logs` 폴더 |
| `--log-dir` 지정 | 지정한 폴더 |

파일명은 `run-YYYYMMDD-HHmmss-밀리초-PID.log`다. 시각은 PC 로컬 시각이며 파일은 UTF-8이다.
시작 시 콘솔에 실제 파일 경로도 출력한다.

```powershell
# 예약 로그 중 최신 파일의 마지막 80줄
$latestLog = Get-ChildItem .\logs -Filter '*.log' |
    Sort-Object LastWriteTime -Descending | Select-Object -First 1
if ($latestLog) { Get-Content -LiteralPath $latestLog.FullName -Tail 80 }
```

`execution finished exit_code=0`은 프로세스 정상 종료다. 강제 종료나 정전 시 종료 행이
없을 수 있다. 실제 수집 누락 여부까지 이 코드가 증명하지는 않는다.
현재 인증서 비밀번호와 일치하는 출력은 마스킹하지만 임의의 다른 민감정보까지 자동으로
식별하지는 않는다. 로그 공유 전 내용을 확인한다. 자동 보관 기간·삭제 기능은 아직 없다.

## 7. 작업 스케줄러 등록·확인

### 등록

인증정보를 저장한 동일한 Windows 사용자의 관리자 PowerShell에서 실행한다.
아래 `16:10`은 예시이며 실제 운영 시각을 넣는다.

```powershell
# XML 미리보기만 생성; 작업 등록·수집 없음
.\scripts\Register-CollectionTask.ps1 -At 16:10 -Preview
# 실제 등록; 즉시 수집하지 않음
.\scripts\Register-CollectionTask.ps1 -At 16:10
```

기본 작업 이름은 `Daishin-CYBOS-Collect`다. 관리자 권한으로 등록하면 예약 실행 시
설정한 계정의 최고 권한으로 실행되며 매번 UAC 확인을 요구하지 않는다.
로그오프 상태에서 서비스처럼 실행하는 구성은 아니다.

| 설정 | 값 |
|---|---|
| 트리거 | 월~금, 지정한 PC 로컬 시각 |
| 사용자 | 등록한 Windows 사용자, 로그온된 대화형 세션 |
| 권한 | 가장 높은 수준 |
| 동작 | Rust EXE의 `run`, 절대 배치 경로·로그 경로 지정 |
| 중복 | 스케줄러 IgnoreNew + 실행기 뮤텍스 |
| 누락된 예약 자동 보충·재시도 | 없음 |
| PC 절전 해제 | 없음 |
| 실행 시간 제한 | 없음; 수집 강제 종료 방지 |
| 증시 휴일 제외 | 없음; 평일 휴장일에도 실행 |

### 화면에서 확인

`Win + R` → `taskschd.msc` → **작업 스케줄러 라이브러리**에서
`Daishin-CYBOS-Collect`를 찾는다. 안 보이면 `F5`로 새로고침한다.

일반 탭의 **사용자가 로그온할 때만 실행**, **가장 높은 수준의 권한으로 실행**,
트리거 탭의 평일·시각, 동작 탭의 EXE·인자·시작 위치를 확인한다.
목록에서는 마지막 실행 시간·마지막 실행 결과·다음 실행 시간을 확인한다.

### PowerShell에서 확인·변경

```powershell
Get-ScheduledTask -TaskName 'Daishin-CYBOS-Collect' |
    Select-Object TaskName, State, Principal, Triggers, Actions
Get-ScheduledTaskInfo -TaskName 'Daishin-CYBOS-Collect' |
    Select-Object LastRunTime, LastTaskResult, NextRunTime

# 이 스크립트가 관리하는 작업이고 실행 중이 아닐 때만 수정 가능
.\scripts\Register-CollectionTask.ps1 -At 18:00 -Update

# 예약 일시 중지·재개; 실행 중 수집 자체를 종료하는 명령은 아님
Disable-ScheduledTask -TaskName 'Daishin-CYBOS-Collect'
Enable-ScheduledTask -TaskName 'Daishin-CYBOS-Collect'
```

예약 작업의 ‘실행’ 버튼은 실제 수집과 알림을 시작한다. 다른 작업이 실행 중이지 않은지
확인한 뒤 사용한다. 첫 예약 실행은 로그와 DB 결과까지 확인한다. PC 경로나 EXE 위치를
변경하면 `-Update`로 작업 동작 경로도 갱신해야 한다.

## 8. 종료 코드

| 코드 | 의미와 대응 |
|---|---|
| 0 | 명령 정상 종료. `run`에서는 배치도 0 반환 |
| 1 | 일반 오류·권한·COM·인증·조회·시간 초과 등. 로그 확인 |
| 2 | CLI 사용법 오류. 단, 수집 배치나 외부 실행기의 코드와는 구분 필요 |
| 10 | `status` 결과 정상 미연결 |
| 11 | 직접 조회 명령 등에서 서버 IP 오류 감지. `login`·`run`에서는 실패로 전환되어 보통 1 반환 |
| 20 | 다른 `login`·`run`이 실행 중이라 차단 |
| 기타 비정상 값 | `run`이 전달한 배치·Python 종료 코드일 수 있음 |

외부 `ncStarter.exe`의 종료 코드는 Rust CLI의 코드와 별개다. 로그의 단계도 같이 확인한다.
스케줄러 `0x0`은 정상 종료, Rust 중복 차단 20은 `0x14`로 표시될 수 있다.
스케줄러 상태 메시지나 HRESULT는 CLI 코드와 별개이므로 무조건 같은 뜻으로 해석하지 않는다.

## 9. 오류 대응

| 증상 | 대응 |
|---|---|
| 관리자 권한 오류 | 동일 계정의 관리자 PowerShell 사용, 예약 작업 최고 권한 확인 |
| `CYBOS_CERT_PASSWORD` 누락 | 환경변수 등록 스크립트 실행, 예약 실행 계정 확인 |
| 기존 연결 자동선택 보류 | 복수 연결·UI 식별 실패 가능. 직접 확인하고 로그 보존 |
| `Sign On Error` 서버 접속 실패 | 접속·인증 성공으로 간주하지 말고 중단. 오류 문구와 발생 단계 기록 |
| CPUTIL/CPSTART 서버 IP 읽기 실패 | 반복 실행하지 않음. 아래 절차로 상태 확인 |
| 프로그램 매매 COM 오류 | 외부·내부 HRESULT와 최초 실패 행/필드 확인. 오류를 일괄 무시하지 않음 |
| 중복 실행 코드 20 | 기존 수집 완료를 기다림. 배치를 직접 실행해 우회하지 않음 |
| 예약 기록·로그 없음 | 작업 활성화·다음 시각·로그온·절전·실행 경로 확인 |
| 원본 변경 해시 오류 | 수정본 강제 복사 대신 기존 변경과 비교·검토 |

### 서버 IP 읽기 실패가 반복될 때

1. `run`이나 `login`을 계속 추가 실행하지 않는다. 예약 실행도 우선 비활성화한다.
2. 팝업 제목(`CPUTIL` 또는 `CPSTART`)과 터미널의 마지막 단계·로그를 기록한다.
3. 남아 있는 수집 프로세스가 없는지 확인한다. Rust만 강제 종료하면 수집기가 남을 수 있다.
4. CYBOS 프로그램을 정상 종료하고, 필요하면 작업을 저장한 뒤 PC를 재부팅한다.
5. CYBOS Plus 수동 로그인 → Rust `login` → `run` 순서로 한 단계씩 확인한다.

코드는 이 오류 감지 시 재시도 없이 중단하도록 구현되어 있다. 하지만 CYBOS 내부 오류의
근본 원인을 고친 것은 아니며 실제 팝업 감지의 모든 상황을 재현 검증한 것도 아니다.
재부팅 후 성공한 사례가 있지만 재부팅을 영구 해결책으로 보지 않는다.

### 중복 실행 범위

잠금은 최신 실행기의 `login`과 `run`에 적용되며 수집 종료까지 유지된다. 배치 직접 실행이나
구버전 실행은 보호하지 않는다. Windows는 소유 프로세스 비정상 종료 시 잠금을 해제하지만
별도 수집 프로세스까지 자동 종료하지는 않는다. 잠금 파일을 찾아 지울 필요는 없다.

## 10. 업데이트와 개발 검증

수집·로그인 작업이 실행 중이지 않을 때 빌드한다. 예약 작업이 실행 파일을 사용 중이면
빌드 파일 교체가 실패할 수 있다. 필요한 경우 예약을 잠시 비활성화하고 완료를 기다린다.

```powershell
cargo fmt --check
cargo test --locked
cargo clippy --locked -- -D warnings
python -B -m unittest discover -s tests -p test_collector_fix.py
cargo build --release --locked
```

테스트는 실제 CYBOS 로그인·DB 저장·Telegram 전송을 실행하지 않는다.
미로그인 상태에서의 전체 자동화, 첫 예약 시각 실행, 잠금 화면·RDP 조건은 별도 운영 검증이
필요하다. 현재 확인 범위와 개발 이력은 [작업 내역](WORK_LOG.md)을 참고한다.
