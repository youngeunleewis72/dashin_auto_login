use anyhow::{Context, Result};
use std::{marker::PhantomData, rc::Rc};
use windows::{
    Win32::{
        Foundation::{CloseHandle, HANDLE, WAIT_ABANDONED, WAIT_OBJECT_0, WAIT_TIMEOUT},
        System::Threading::{CreateMutexW, ReleaseMutex, WaitForSingleObject},
    },
    core::PCWSTR,
};

// Shared across app copies and Windows sessions. No credentials in the name.
pub const MUTEX_NAME: &str = r"Global\dashin-auto-login.operation.v1";

pub struct Lock {
    handle: HANDLE,
    abandoned: bool,
    // Windows mutex ownership is thread-affine. Prevent transfer to another thread.
    _thread: PhantomData<Rc<()>>,
}

impl Lock {
    pub fn acquire(name: &str) -> Result<Option<Self>> {
        let wide: Vec<u16> = name.encode_utf16().chain(Some(0)).collect();
        let handle = unsafe { CreateMutexW(None, false, PCWSTR(wide.as_ptr())) }
            .context("중복 실행 방지 뮤텍스 생성 실패")?;
        let state = unsafe { WaitForSingleObject(handle, 0) };
        if state == WAIT_OBJECT_0 || state == WAIT_ABANDONED {
            return Ok(Some(Self {
                handle,
                abandoned: state == WAIT_ABANDONED,
                _thread: PhantomData,
            }));
        }
        // Capture the Win32 error before CloseHandle can change it.
        let error = windows::core::Error::from_thread();
        unsafe {
            let _ = CloseHandle(handle);
        }
        if state == WAIT_TIMEOUT {
            return Ok(None);
        }
        Err(error).context("중복 실행 방지 뮤텍스 획득 실패")
    }

    pub fn was_abandoned(&self) -> bool {
        self.abandoned
    }
}

impl Drop for Lock {
    fn drop(&mut self) {
        unsafe {
            let _ = ReleaseMutex(self.handle);
            let _ = CloseHandle(self.handle);
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::{
        io::{BufRead, BufReader, Write},
        process::{Child, Command, Stdio},
        time::{SystemTime, UNIX_EPOCH},
    };

    // Invoked only in a child test process with a unique mutex name. Never runs
    // CYBOS, logs in, or starts the collector.
    #[test]
    fn child_process_holds_mutex() {
        let Ok(name) = std::env::var("DASHIN_MUTEX_TEST_NAME") else {
            return;
        };
        let _lock = Lock::acquire(&name).unwrap().expect("child acquires mutex");
        println!("MUTEX_READY");
        std::io::stdout().flush().unwrap();
        let mut line = String::new();
        std::io::stdin().read_line(&mut line).unwrap();
    }

    struct ChildGuard(Child);
    impl Drop for ChildGuard {
        fn drop(&mut self) {
            let _ = self.0.kill();
            let _ = self.0.wait();
        }
    }

    #[test]
    fn rejects_another_process_and_recovers_after_forced_exit() {
        let unique = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .unwrap()
            .as_nanos();
        let name = format!(r"Local\dashin-mutex-test-{}-{unique}", std::process::id());
        let mut child = ChildGuard(
            Command::new(std::env::current_exe().unwrap())
                .args([
                    "--exact",
                    "single_instance::tests::child_process_holds_mutex",
                    "--nocapture",
                ])
                .env("DASHIN_MUTEX_TEST_NAME", &name)
                .stdin(Stdio::piped())
                .stdout(Stdio::piped())
                .spawn()
                .unwrap(),
        );
        let reader = BufReader::new(child.0.stdout.take().unwrap());
        let ready = reader
            .lines()
            .any(|line| line.unwrap().contains("MUTEX_READY"));
        assert!(ready, "child did not acquire mutex");
        assert!(
            Lock::acquire(&name).unwrap().is_none(),
            "must reject another process"
        );

        // Retain an unowned handle so the object survives child termination,
        // allowing us to verify WAIT_ABANDONED recovery, not just recreation.
        let wide: Vec<u16> = name.encode_utf16().chain(Some(0)).collect();
        let observer = unsafe { CreateMutexW(None, false, PCWSTR(wide.as_ptr())) }.unwrap();
        child.0.kill().unwrap();
        child.0.wait().unwrap();
        let recovered = Lock::acquire(&name).unwrap().expect("recover after crash");
        assert!(recovered.was_abandoned());
        drop(recovered);
        let normal = Lock::acquire(&name).unwrap().expect("normal release");
        assert!(!normal.was_abandoned());
        drop(normal);
        unsafe {
            CloseHandle(observer).unwrap();
        }
    }
}
