use anyhow::{Context, Result};
use std::{
    fs::{self, File, OpenOptions},
    io::{self, BufRead, BufReader, Read, Write},
    path::{Path, PathBuf},
    process::{Command, Stdio},
    sync::{Arc, Mutex},
    thread,
};
use windows::Win32::System::SystemInformation::GetLocalTime;

fn timestamp() -> String {
    let time = unsafe { GetLocalTime() };
    format!(
        "{:04}{:02}{:02}-{:02}{:02}{:02}-{:03}",
        time.wYear,
        time.wMonth,
        time.wDay,
        time.wHour,
        time.wMinute,
        time.wSecond,
        time.wMilliseconds
    )
}

pub fn run_logged(directory: Option<&Path>) -> Result<i32> {
    let exe = std::env::current_exe()?;
    let directory = directory
        .map(PathBuf::from)
        .unwrap_or_else(|| exe.parent().unwrap().join("logs"));
    fs::create_dir_all(&directory).context("로그 폴더 생성 실패")?;
    let path = directory.join(format!("run-{}-{}.log", timestamp(), std::process::id()));
    let file = OpenOptions::new()
        .write(true)
        .create_new(true)
        .open(&path)
        .context("로그 파일 생성 실패")?;
    println!("실행 로그: {}", path.display());
    let mut command = Command::new(exe);
    command.args(std::env::args_os().skip(1)).arg("--log-child");
    // Preserve process environment here: the actual command must be able to
    // load temporary CYBOS_CERT_PASSWORD overrides. Its workers strip them.
    let secrets: Vec<String> = crate::credentials::load().ok().into_iter().collect();
    capture(command, file, secrets)
}

fn capture(mut command: Command, mut file: File, secrets: Vec<String>) -> Result<i32> {
    writeln!(file, "[{}] execution started", timestamp())?;
    let mut child = command
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .spawn()
        .context("로그 수집용 실행 프로세스 시작 실패")?;
    let file = Arc::new(Mutex::new(file));
    let stdout = forward(
        child.stdout.take().unwrap(),
        file.clone(),
        secrets.clone(),
        false,
    );
    let stderr = forward(child.stderr.take().unwrap(), file.clone(), secrets, true);
    let status = child.wait().context("실행 프로세스 대기 실패")?;
    stdout
        .join()
        .map_err(|_| anyhow::anyhow!("stdout 로그 스레드 중단"))??;
    stderr
        .join()
        .map_err(|_| anyhow::anyhow!("stderr 로그 스레드 중단"))??;
    let code = status.code().context("종료 코드 확인 실패")?;
    writeln!(
        file.lock()
            .map_err(|_| io::Error::other("로그 잠금 오류"))?,
        "[{}] execution finished exit_code={code}",
        timestamp()
    )?;
    Ok(code)
}

fn forward(
    reader: impl Read + Send + 'static,
    file: Arc<Mutex<File>>,
    secrets: Vec<String>,
    stderr: bool,
) -> thread::JoinHandle<io::Result<()>> {
    thread::spawn(move || {
        let mut reader = BufReader::new(reader);
        let mut buffer = Vec::new();
        while reader.read_until(b'\n', &mut buffer)? != 0 {
            let mut text = String::from_utf8_lossy(&buffer).into_owned();
            for secret in &secrets {
                if !secret.is_empty() {
                    text = text.replace(secret, "[REDACTED]");
                }
            }
            if stderr {
                io::stderr().lock().write_all(text.as_bytes())?;
            } else {
                io::stdout().lock().write_all(text.as_bytes())?;
            }
            let mut log = file
                .lock()
                .map_err(|_| io::Error::other("로그 잠금 오류"))?;
            write!(
                log,
                "[{}] [{}] {}",
                timestamp(),
                if stderr { "stderr" } else { "stdout" },
                text
            )?;
            if !text.ends_with('\n') {
                writeln!(log)?;
            }
            log.flush()?;
            buffer.clear();
        }
        Ok(())
    })
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn captures_both_streams_redacts_and_preserves_exit_code() {
        let path = Path::new(env!("CARGO_MANIFEST_DIR")).join("target/logging-test.log");
        let file = File::create(&path).unwrap();
        let shell = PathBuf::from(std::env::var_os("SystemRoot").unwrap())
            .join("System32")
            .join("cmd.exe");
        let mut command = Command::new(shell);
        use std::os::windows::process::CommandExt;
        let batch = Path::new(env!("CARGO_MANIFEST_DIR")).join("tests/fixtures/log output.bat");
        command
            .args(["/D", "/S", "/C"])
            .raw_arg(format!("\"\"{}\"\"", batch.display()));
        assert_eq!(
            capture(command, file, vec!["test-secret".into()]).unwrap(),
            17
        );
        let contents = fs::read_to_string(path).unwrap();
        assert!(!contents.contains("test-secret"));
        assert!(contents.contains("[REDACTED]"));
        assert!(contents.contains("error-output"));
        assert!(contents.contains("exit_code=17"));
    }
}
