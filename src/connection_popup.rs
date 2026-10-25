//! Handle only the existing-connection chooser. Never select a new connection,
//! enter credentials, or dismiss sign-on errors.
use anyhow::{Context, Result, ensure};
use std::{
    path::{Path, PathBuf},
    process::{Child, Command},
    thread,
    time::Duration,
};
use windows::{
    Win32::{
        Foundation::{CloseHandle, HWND, LPARAM},
        System::{Com::*, Threading::*},
        UI::{Accessibility::*, WindowsAndMessaging::*},
    },
    core::{BOOL, PCWSTR, PWSTR, w},
};

pub struct Watcher(Child);

// Observe our worker's errors and the installed CpStart's error. Never dismiss
// these dialogs or terminate the external CYBOS process.
pub fn has_server_ip_error(pid: u32, starter: &Path) -> bool {
    struct Search {
        pid: u32,
        found: bool,
        cpstart: PathBuf,
    }
    unsafe extern "system" fn inspect_child(window: HWND, parameter: LPARAM) -> BOOL {
        let found = unsafe { &mut *(parameter.0 as *mut bool) };
        if is_server_ip_message(&window_text(window)) {
            *found = true;
        }
        BOOL(1)
    }
    unsafe extern "system" fn inspect_window(window: HWND, parameter: LPARAM) -> BOOL {
        let search = unsafe { &mut *(parameter.0 as *mut Search) };
        let mut owner = 0;
        unsafe {
            GetWindowThreadProcessId(window, Some(&mut owner));
        }
        let title = window_text(window);
        let own_error = owner == search.pid && matches!(title.as_str(), "CPUTIL" | "CPSTART");
        let external_error = title == "CPSTART"
            && process_path(window).is_ok_and(|path| {
                path.to_string_lossy()
                    .eq_ignore_ascii_case(&search.cpstart.to_string_lossy())
            });
        if own_error || external_error {
            let mut found = false;
            unsafe {
                let _ = EnumChildWindows(
                    Some(window),
                    Some(inspect_child),
                    LPARAM((&mut found as *mut bool) as isize),
                );
            }
            search.found |= found;
        }
        BOOL(1)
    }
    let Some(root) = starter.parent().and_then(Path::parent) else {
        return false;
    };
    let mut search = Search {
        pid,
        found: false,
        cpstart: root.join("CYBOSPLUS").join("CpStart.exe"),
    };
    unsafe {
        let _ = EnumWindows(
            Some(inspect_window),
            LPARAM((&mut search as *mut Search) as isize),
        );
    }
    search.found
}

fn window_text(window: HWND) -> String {
    let mut buffer = [0u16; 512];
    let length = unsafe { GetWindowTextW(window, &mut buffer) };
    String::from_utf16_lossy(&buffer[..length.max(0) as usize])
}

fn is_server_ip_message(text: &str) -> bool {
    normalize(text) == "서버IP읽기실패"
}
impl Watcher {
    pub fn start(starter: &Path) -> Result<Self> {
        let mut command = Command::new(std::env::current_exe()?);
        command
            .arg("--starter")
            .arg(starter)
            .args(["worker", "connection-watch"]);
        crate::credentials::strip_environment(&mut command);
        Ok(Self(
            command.spawn().context("기존 연결 선택 감시 시작 실패")?,
        ))
    }
}
impl Drop for Watcher {
    fn drop(&mut self) {
        let _ = self.0.kill();
        let _ = self.0.wait();
    }
}

pub fn watch(starter: &Path) -> Result<()> {
    ensure!(
        unsafe { windows::Win32::UI::Shell::IsUserAnAdmin().as_bool() },
        "기존 연결 선택에는 관리자 권한이 필요합니다"
    );
    unsafe {
        CoInitializeEx(None, COINIT_MULTITHREADED).ok()?;
    }
    // The watcher is a disposable process, terminated by its owner's RAII guard.
    let automation: IUIAutomation =
        unsafe { CoCreateInstance(&CUIAutomation, None, CLSCTX_INPROC_SERVER)? };
    let install_root = starter
        .parent()
        .and_then(Path::parent)
        .context("CYBOS 설치 경로 확인 실패")?;
    let mut handled = None;
    loop {
        ensure!(
            !has_server_ip_error(0, starter),
            "CPSTART 서버 IP 오류가 있어 기존 연결 선택 감시를 중단합니다"
        );
        if let Ok(window) = unsafe { FindWindowW(PCWSTR::null(), w!("기존 연결 선택")) } {
            if handled != Some(window.0 as usize) {
                // Log at most once per window, including unsupported layouts.
                handled = Some(window.0 as usize);
                match select_existing(&automation, window, install_root) {
                    Ok(()) => println!("CYBOS Plus 기존 연결로 접속을 선택했습니다."),
                    Err(error) => eprintln!("기존 연결 자동선택 보류: {error:#}"),
                }
            }
        } else {
            handled = None;
        }
        thread::sleep(Duration::from_millis(400));
    }
}

fn process_path(window: HWND) -> Result<PathBuf> {
    let mut pid = 0;
    unsafe {
        GetWindowThreadProcessId(window, Some(&mut pid));
        let process = OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, false, pid)?;
        let mut buffer = vec![0u16; 32768];
        let mut size = buffer.len() as u32;
        let result = QueryFullProcessImageNameW(
            process,
            PROCESS_NAME_WIN32,
            PWSTR(buffer.as_mut_ptr()),
            &mut size,
        );
        let _ = CloseHandle(process);
        result?;
        buffer.truncate(size as usize);
        Ok(PathBuf::from(String::from_utf16(&buffer)?))
    }
}

fn allowed_process(path: &Path, root: &Path) -> bool {
    let path = path.to_string_lossy().to_lowercase();
    let root = root.to_string_lossy().trim_end_matches('\\').to_lowercase();
    let filename = Path::new(&path)
        .file_name()
        .and_then(|v| v.to_str())
        .unwrap_or("");
    path.starts_with(&format!("{root}\\"))
        && matches!(filename, "ncstarter.exe" | "dibserver.exe" | "cpstart.exe")
}

fn select_existing(automation: &IUIAutomation, window: HWND, root: &Path) -> Result<()> {
    ensure!(
        allowed_process(&process_path(window)?, root),
        "CYBOS 프로세스의 창으로 확인되지 않았습니다"
    );
    unsafe {
        let dialog = automation.ElementFromHandle(window)?;
        let condition = automation.CreateTrueCondition()?;
        let all = dialog.FindAll(TreeScope_Descendants, &condition)?;
        let mut rows = Vec::new();
        let mut buttons = Vec::new();
        for index in 0..all.Length()? {
            let element = all.GetElement(index)?;
            let kind = element.CurrentControlType()?;
            if kind == UIA_ListItemControlTypeId || kind == UIA_DataItemControlTypeId {
                rows.push(element);
            } else if kind == UIA_ButtonControlTypeId
                && normalize(&element.CurrentName()?.to_string()) == "기존연결로접속"
            {
                buttons.push(element);
            }
        }
        ensure!(
            rows.len() == 1 && buttons.len() == 1,
            "연결 목록 또는 버튼이 하나로 확인되지 않습니다. 수동 확인이 필요합니다"
        );
        let row = &rows[0];
        let row_name = row.CurrentName()?.to_string();
        let children = row.FindAll(TreeScope_Descendants, &condition)?;
        let mut text = row_name.clone();
        for index in 0..children.Length()? {
            text.push(' ');
            text.push_str(&children.GetElement(index)?.CurrentName()?.to_string());
        }
        ensure!(
            is_plus_row(&row_name, &text),
            "단일 CYBOS Plus(cp) 연결로 확인되지 않습니다. 수동 확인이 필요합니다"
        );
        ensure!(
            buttons[0].CurrentIsEnabled()?.as_bool(),
            "기존 연결 버튼이 비활성 상태입니다"
        );
        let selection: IUIAutomationSelectionItemPattern =
            row.GetCurrentPatternAs(UIA_SelectionItemPatternId)?;
        selection.Select()?;
        // Resolve again after selection rather than reuse potentially stale indexes.
        let button_condition =
            automation.CreatePropertyCondition(UIA_NamePropertyId, &"기존 연결로 접속".into())?;
        let targets = dialog.FindAll(TreeScope_Descendants, &button_condition)?;
        ensure!(
            targets.Length()? == 1,
            "기존 연결 버튼을 다시 확인하지 못했습니다"
        );
        let invoke: IUIAutomationInvokePattern = targets
            .GetElement(0)?
            .GetCurrentPatternAs(UIA_InvokePatternId)?;
        invoke.Invoke()?;
    }
    Ok(())
}

fn normalize(value: &str) -> String {
    value.chars().filter(|c| !c.is_whitespace()).collect()
}
fn is_plus_row(name: &str, text: &str) -> bool {
    name.split_whitespace().next() == Some("cp") && normalize(text).contains("사이보스플러스")
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn matches_only_the_reported_cputil_message() {
        assert!(is_server_ip_message("서버 IP 읽기 실패"));
        assert!(!is_server_ip_message("서버 접속 실패"));
        assert!(!is_server_ip_message("비밀번호 오류"));
        assert!(!is_server_ip_message("서버 IP 읽기 실패 관련 도움말"));
    }
    #[test]
    fn rejects_other_servers_and_unrelated_processes() {
        assert!(is_plus_row("cp", "cp 사이보스플러스[시세/계좌조회전용]"));
        assert!(!is_plus_row("hts", "hts 사이보스플러스"));
        assert!(!is_plus_row("cp", "cp 사이보스"));
        let root = Path::new(r"C:\DAISHIN");
        assert!(allowed_process(
            Path::new(r"C:\DAISHIN\STARTER\ncStarter.exe"),
            root
        ));
        assert!(!allowed_process(
            Path::new(r"C:\DAISHIN-other\ncStarter.exe"),
            root
        ));
        assert!(!allowed_process(Path::new(r"C:\DAISHIN\unknown.exe"), root));
    }
}
