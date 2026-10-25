mod collector;
mod connection_popup;
mod credentials;
mod cybos;
mod logging;
mod single_instance;

use anyhow::{Context, Result, bail, ensure};
use clap::{Parser, Subcommand};
use std::{
    path::{Path, PathBuf},
    process::{Command, Stdio},
    thread,
    time::{Duration, Instant},
};
use windows::Win32::UI::Shell::IsUserAnAdmin;

#[derive(Parser)]
#[command(
    version,
    about = "32-bit CYBOS Plus login and read-only quote verification"
)]
struct Cli {
    #[arg(
        long,
        global = true,
        default_value = r"C:\DAISHIN\STARTER\ncStarter.exe"
    )]
    starter: PathBuf,
    #[arg(long, global = true, default_value = "120", value_parser = clap::value_parser!(u64).range(1..=3600))]
    timeout: u64,
    /// Directory for per-execution logs (default: logs next to the executable).
    #[arg(long, global = true)]
    log_dir: Option<PathBuf>,
    #[arg(long, global = true, hide = true)]
    log_child: bool,
    #[command(subcommand)]
    command: Action,
}

#[derive(Subcommand)]
enum Action {
    /// Report installation, architecture, privileges and COM availability.
    Doctor,
    /// Check the actual Plus connection (exit 10 when disconnected).
    Status,
    /// Request a read-only quote; no trading API is used.
    Quote {
        #[arg(default_value = "A005930")]
        code: String,
    },
    /// Log in with the selected local certificate and CYBOS_CERT_PASSWORD.
    Login {
        /// Open the normal login UI instead of using environment credentials.
        #[arg(long)]
        manual: bool,
        #[arg(long, default_value = "A005930")]
        code: String,
    },
    /// Verify CYBOS login and quote, then execute the collector batch and wait.
    Run {
        #[arg(
            long,
            default_value = r"C:\myprojects\study\kr_stocks_dashin_collector\run_schedule.bat"
        )]
        batch: PathBuf,
        #[arg(long, default_value = "A005930")]
        code: String,
    },
    #[command(hide = true)]
    Worker {
        operation: String,
        code: Option<String>,
    },
}

fn main() {
    let cli = Cli::parse();
    let result = if cli.log_child || matches!(cli.command, Action::Worker { .. }) {
        execute(cli)
    } else {
        logging::run_logged(cli.log_dir.as_deref())
    };
    let code = match result {
        Ok(code) => code,
        Err(error) => {
            eprintln!("ERROR: {error:#}");
            1
        }
    };
    std::process::exit(code);
}

fn execute(cli: Cli) -> Result<i32> {
    // Acquire only at the public command boundary. `run` invokes Login
    // internally and must retain the same guard through collector completion.
    let _lock = if matches!(cli.command, Action::Login { .. } | Action::Run { .. }) {
        ensure!(
            unsafe { IsUserAnAdmin().as_bool() },
            "관리자 권한 PowerShell에서 실행하세요"
        );
        let Some(lock) = single_instance::Lock::acquire(single_instance::MUTEX_NAME)? else {
            eprintln!("이미 login 또는 run 작업이 실행 중입니다. 추가 실행을 중단합니다.");
            return Ok(20);
        };
        if lock.was_abandoned() {
            println!("이전 작업의 비정상 종료로 해제된 잠금을 인수했습니다.");
        }
        Some(lock)
    } else {
        None
    };
    run(cli)
}

fn validate_code(code: &str) -> Result<()> {
    ensure!(
        code.len() == 7
            && code.starts_with('A')
            && code.as_bytes()[1..].iter().all(u8::is_ascii_digit),
        "종목코드는 A와 숫자 6자리여야 합니다 (예: A005930)"
    );
    Ok(())
}

fn run(cli: Cli) -> Result<i32> {
    ensure!(
        cfg!(target_arch = "x86"),
        "32비트 i686-pc-windows-msvc 빌드가 필요합니다"
    );
    if let Action::Worker { operation, code } = &cli.command {
        if operation == "connection-watch" {
            connection_popup::watch(&cli.starter)?;
            return Ok(0);
        }
        return cybos::worker(operation, code.as_deref()).map(i32::from);
    }
    let elevated = unsafe { IsUserAnAdmin().as_bool() };
    if matches!(cli.command, Action::Doctor) {
        println!(
            "architecture: x86 (32-bit)\nstarter exists: {}\nadministrator: {elevated}",
            cli.starter.is_file()
        );
        let result = probe(
            &cli.starter,
            "doctor",
            None,
            Duration::from_secs(cli.timeout),
        )?;
        print!("{}", result.1);
        ensure!(
            result.0 == 0 && elevated && cli.starter.is_file(),
            "환경 진단 미통과: 위 결과를 확인하세요"
        );
        return Ok(0);
    }
    ensure!(elevated, "관리자 권한 PowerShell에서 실행하세요");
    match cli.command {
        Action::Status => {
            let (code, output) = probe(
                &cli.starter,
                "status",
                None,
                Duration::from_secs(cli.timeout),
            )?;
            print!("{output}");
            Ok(i32::from(code))
        }
        Action::Quote { code } => {
            validate_code(&code)?;
            let (exit, output) = probe(
                &cli.starter,
                "quote",
                Some(&code),
                Duration::from_secs(cli.timeout),
            )?;
            print!("{output}");
            Ok(i32::from(exit))
        }
        Action::Run { batch, code } => {
            collector::validate_batch(&batch)?;
            run(Cli {
                starter: cli.starter,
                timeout: cli.timeout,
                log_dir: cli.log_dir,
                log_child: true,
                command: Action::Login {
                    manual: false,
                    code,
                },
            })?;
            collector::run(&batch)
        }
        Action::Login { manual, code } => {
            validate_code(&code)?;
            let _connection_watch = connection_popup::Watcher::start(&cli.starter)?;
            println!("CYBOS 연결 상태 확인 중…");
            let (status, output) = probe(&cli.starter, "status", None, Duration::from_secs(15))?;
            ensure!(
                matches!(status, 0 | 10),
                "연결 확인 실패. 재시도하지 않습니다: {output}"
            );
            if status != 0 {
                ensure!(cli.starter.is_file(), "CYBOS 실행기를 찾을 수 없습니다");
                let mut command = Command::new(&cli.starter);
                command
                    .current_dir(cli.starter.parent().context("실행기 상위 경로 없음")?)
                    .args(["/prj:cp", "/logintab:cert"])
                    // A persistent GUI process must not keep the log pipe open
                    // after this CLI finishes.
                    .stdin(Stdio::null())
                    .stdout(Stdio::null())
                    .stderr(Stdio::null());
                if !manual {
                    let certificate = credentials::load()?;
                    command.args(certificate_arguments(&certificate));
                }
                credentials::strip_environment(&mut command);
                let _starter = command.spawn().context("CYBOS 실행기 시작 실패")?;
                drop(command);
                println!("CYBOS 연결 대기 중 (최대 {}초)…", cli.timeout);
                let (status, output) = probe(
                    &cli.starter,
                    "wait-connected",
                    None,
                    Duration::from_secs(cli.timeout),
                )?;
                ensure!(
                    status == 0,
                    "로그인 연결 확인 실패. 재시도하지 않습니다: {output}"
                );
            } else {
                println!("기존 CYBOS 연결을 재사용합니다.");
            }
            let (exit, output) = probe(
                &cli.starter,
                "quote",
                Some(&code),
                Duration::from_secs(cli.timeout),
            )?;
            print!("{output}");
            ensure!(exit == 0, "로그인 후 시세 조회 검증 실패");
            println!("연결 및 시세 조회 검증 완료");
            Ok(0)
        }
        _ => unreachable!(),
    }
}

fn certificate_arguments(password: &str) -> [String; 2] {
    [format!("/pwdcert:{password}"), "/autostart".into()]
}

// Isolate synchronous COM calls so a stuck BlockRequest cannot hang the CLI forever.
fn probe(
    starter: &Path,
    operation: &str,
    code: Option<&str>,
    timeout: Duration,
) -> Result<(u8, String)> {
    ensure!(
        !connection_popup::has_server_ip_error(0, starter),
        "기존 CPSTART '서버 IP 읽기 실패' 창을 감지했습니다. COM 확인이나 로그인을 다시 시작하지 않습니다"
    );
    let mut command = Command::new(std::env::current_exe()?);
    credentials::strip_environment(&mut command);
    command
        .args(["worker", operation])
        .stdout(Stdio::piped())
        .stderr(Stdio::piped());
    if let Some(code) = code {
        command.arg(code);
    }
    let mut child = command.spawn().context("COM worker 시작 실패")?;
    let deadline = Instant::now() + timeout;
    loop {
        if connection_popup::has_server_ip_error(child.id(), starter) {
            let _ = child.kill();
            child.wait()?;
            return Ok((
                11,
                format!(
                    "COM {operation}: CPUTIL/CPSTART '서버 IP 읽기 실패' 감지. 확인용 worker만 종료하고 중단했습니다. CYBOS를 자동 재실행하지 않습니다.\n"
                ),
            ));
        }
        if child.try_wait()?.is_some() {
            let output = child.wait_with_output()?;
            let text = format!(
                "{}{}",
                String::from_utf8_lossy(&output.stdout),
                String::from_utf8_lossy(&output.stderr)
            );
            return Ok((
                output
                    .status
                    .code()
                    .and_then(|v| u8::try_from(v).ok())
                    .unwrap_or(1),
                text,
            ));
        }
        if Instant::now() >= deadline {
            let _ = child.kill();
            let _ = child.wait();
            bail!("COM {operation} 시간 초과 ({}초)", timeout.as_secs());
        }
        thread::sleep(Duration::from_millis(100));
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn certificate_login_does_not_pass_id_or_login_password() {
        let args = certificate_arguments("test & certificate password");
        assert_eq!(args, ["/pwdcert:test & certificate password", "/autostart"]);
        assert!(
            !args
                .iter()
                .any(|arg| arg.starts_with("/id:") || arg.starts_with("/pwd:"))
        );
    }
    #[test]
    fn restrict_quote_to_stock_codes() {
        assert!(validate_code("A005930").is_ok());
        for code in ["005930", "A00593X", "A005930/", "A가나다라", "U001", ""] {
            assert!(validate_code(code).is_err(), "{code}");
        }
    }
}
