use anyhow::{Context, Result, ensure};
use std::{os::windows::process::CommandExt, path::Path, process::Command};

pub fn validate_batch(path: &Path) -> Result<()> {
    ensure!(
        path.is_absolute() && path.is_file(),
        "존재하는 수집 배치의 절대 경로를 지정하세요: {}",
        path.display()
    );
    ensure!(
        path.extension()
            .is_some_and(|ext| ext.eq_ignore_ascii_case("bat")),
        "수집기는 .bat 파일이어야 합니다"
    );
    let text = path
        .to_str()
        .context("수집 배치 경로는 유니코드여야 합니다")?;
    // cmd parses /C as shell text. Restrict path metacharacters rather than
    // attempting to escape arbitrary shell expressions; spaces remain supported.
    ensure!(
        !text.contains(['"', '%', '!', '^', '&', '|', '<', '>', '(', ')', '\r', '\n']),
        "수집 배치 경로에 cmd 특수문자를 사용할 수 없습니다"
    );
    Ok(())
}

pub fn run(path: &Path) -> Result<i32> {
    validate_batch(path)?;
    let system_root = std::env::var_os("SystemRoot").context("SystemRoot 환경변수 없음")?;
    let shell = Path::new(&system_root).join("System32").join("cmd.exe");
    let mut command = Command::new(shell);
    command
        .args(["/D", "/S", "/C"])
        .raw_arg(format!("\"\"{}\"\"", path.display()))
        .current_dir(path.parent().context("수집 배치 상위 경로 없음")?);
    crate::credentials::strip_environment(&mut command);
    println!("수집 배치 실행: {}", path.display());
    let status = command.status().context("수집 배치 실행 실패")?;
    let code = status
        .code()
        .context("수집 배치 종료 코드를 읽을 수 없습니다")?;
    println!("수집 배치 종료 코드: {code}");
    Ok(code)
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn batch_with_spaces_runs_in_its_directory_and_preserves_failure_code() {
        let path =
            Path::new(env!("CARGO_MANIFEST_DIR")).join("tests/fixtures/collector failure.bat");
        assert_eq!(run(&path).unwrap(), 17);
    }
}
