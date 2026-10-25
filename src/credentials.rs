use anyhow::{Context, Result, bail, ensure};
use std::process::Command;
use windows::{
    Win32::{
        Foundation::ERROR_FILE_NOT_FOUND,
        System::Registry::{HKEY_CURRENT_USER, RRF_RT_REG_SZ, RegGetValueW},
    },
    core::{PCWSTR, w},
};

const NAMES: [&str; 3] = ["CYBOS_ID", "CYBOS_PASSWORD", "CYBOS_CERT_PASSWORD"];

pub fn load() -> Result<String> {
    load_with(read_variable)
}

fn load_with(mut read: impl FnMut(&str) -> Result<Option<String>>) -> Result<String> {
    let name = "CYBOS_CERT_PASSWORD";
    let value = read(name)?.with_context(|| {
        format!("{name} 환경변수가 없습니다. scripts/Set-CybosEnvironment.ps1을 실행하세요")
    })?;
    // Do not trim passwords: leading/trailing spaces can be significant.
    ensure!(
        !value.is_empty() && !value.contains(['\0', '\r', '\n']),
        "{name} 환경변수가 비어 있거나 유효하지 않습니다"
    );
    Ok(value)
}

fn read_variable(name: &str) -> Result<Option<String>> {
    match std::env::var(name) {
        Ok(value) => return Ok(Some(value)),
        Err(std::env::VarError::NotUnicode(_)) => bail!("{name} 환경변수 인코딩 오류"),
        Err(std::env::VarError::NotPresent) => {}
    }
    // Read persistent user environment directly, including when the scheduler's
    // existing session has not inherited recently changed user variables yet.
    let name_wide: Vec<u16> = name.encode_utf16().chain(Some(0)).collect();
    let mut bytes = 0;
    unsafe {
        let result = RegGetValueW(
            HKEY_CURRENT_USER,
            w!("Environment"),
            PCWSTR(name_wide.as_ptr()),
            RRF_RT_REG_SZ,
            None,
            None,
            Some(&mut bytes),
        );
        if result == ERROR_FILE_NOT_FOUND {
            return Ok(None);
        }
        result
            .ok()
            .with_context(|| format!("{name} 사용자 환경변수 조회 실패"))?;
        let mut buffer = vec![0u16; (bytes as usize).div_ceil(2)];
        RegGetValueW(
            HKEY_CURRENT_USER,
            w!("Environment"),
            PCWSTR(name_wide.as_ptr()),
            RRF_RT_REG_SZ,
            None,
            Some(buffer.as_mut_ptr().cast()),
            Some(&mut bytes),
        )
        .ok()
        .with_context(|| format!("{name} 사용자 환경변수 읽기 실패"))?;
        buffer.truncate(bytes as usize / 2);
        if buffer.last() == Some(&0) {
            buffer.pop();
        }
        Ok(Some(
            String::from_utf16(&buffer).with_context(|| format!("{name} 인코딩 오류"))?,
        ))
    }
}

pub fn strip_environment(command: &mut Command) {
    for name in NAMES {
        command.env_remove(name);
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn preserves_password_spaces_and_special_characters() {
        let value = load_with(|name| {
            assert_eq!(name, "CYBOS_CERT_PASSWORD");
            Ok(Some(" p&$\"ass ".to_owned()))
        })
        .unwrap();
        assert_eq!(value, " p&$\"ass ");
    }

    #[test]
    fn reports_variable_name_without_secret_values() {
        let error =
            load_with(|name| Ok((name != "CYBOS_CERT_PASSWORD").then(|| "secret-marker".into())))
                .unwrap_err()
                .to_string();
        assert!(error.contains("CYBOS_CERT_PASSWORD"));
        assert!(!error.contains("secret-marker"));
        for invalid in ["", "pass\nword", "pass\0word"] {
            assert!(load_with(|_| Ok(Some(invalid.into()))).is_err());
        }
    }
}
