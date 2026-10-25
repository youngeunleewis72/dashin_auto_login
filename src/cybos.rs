use anyhow::{Context, Result, ensure};
use windows::{
    Win32::System::{Com::*, Variant::VARIANT},
    core::{BSTR, GUID, PCWSTR},
};

struct Apartment;
impl Apartment {
    fn new() -> Result<Self> {
        // Each worker owns a single STA; dispatch objects are dropped before this guard.
        unsafe {
            CoInitializeEx(None, COINIT_APARTMENTTHREADED).ok()?;
        }
        Ok(Self)
    }
}
impl Drop for Apartment {
    fn drop(&mut self) {
        unsafe {
            CoUninitialize();
        }
    }
}

struct Dispatch(IDispatch);
impl Dispatch {
    fn new(progid: &str) -> Result<Self> {
        let name: Vec<u16> = progid.encode_utf16().chain(Some(0)).collect();
        unsafe {
            let clsid = CLSIDFromProgID(PCWSTR(name.as_ptr()))
                .with_context(|| format!("{progid}: COM 등록 확인 실패"))?;
            Ok(Self(
                CoCreateInstance(&clsid, None, CLSCTX_ALL)
                    .with_context(|| format!("{progid}: COM 생성 실패"))?,
            ))
        }
    }

    fn invoke(&self, name: &str, flags: DISPATCH_FLAGS, mut args: Vec<VARIANT>) -> Result<VARIANT> {
        let wide: Vec<u16> = name.encode_utf16().chain(Some(0)).collect();
        let name_ptr = PCWSTR(wide.as_ptr());
        let mut id = 0;
        let mut result = VARIANT::default();
        // Automation arguments are passed in reverse order. VARIANT owns its BSTRs.
        args.reverse();
        let params = DISPPARAMS {
            rgvarg: args.as_mut_ptr(),
            cArgs: args.len() as u32,
            ..Default::default()
        };
        unsafe {
            self.0
                .GetIDsOfNames(&GUID::zeroed(), &name_ptr, 1, 0, &mut id)
                .with_context(|| format!("COM member {name}"))?;
            self.0
                .Invoke(
                    id,
                    &GUID::zeroed(),
                    0,
                    flags,
                    &params,
                    Some(&mut result),
                    None,
                    None,
                )
                .with_context(|| format!("COM call {name}"))?;
        }
        Ok(result)
    }
    fn call(&self, name: &str, args: Vec<VARIANT>) -> Result<VARIANT> {
        self.invoke(name, DISPATCH_METHOD, args)
    }
    fn connected(&self) -> Result<bool> {
        Ok(i32::try_from(&self.invoke("IsConnect", DISPATCH_PROPERTYGET, vec![])?)? == 1)
    }
}

pub fn worker(operation: &str, code: Option<&str>) -> Result<u8> {
    if operation != "doctor" {
        ensure!(
            unsafe { windows::Win32::UI::Shell::IsUserAnAdmin().as_bool() },
            "관리자 권한 PowerShell에서 실행하세요"
        );
    }
    let _apartment = Apartment::new()?;
    let cybos = Dispatch::new("CpUtil.CpCybos")?;
    match operation {
        "doctor" => {
            let _stock = Dispatch::new("DsCbo1.StockMst")?;
            println!(
                "COM: CpUtil.CpCybos, DsCbo1.StockMst OK\nconnected: {}",
                cybos.connected()?
            );
            Ok(0)
        }
        "status" => {
            let connected = cybos.connected()?;
            println!("connected: {connected}");
            Ok(if connected { 0 } else { 10 })
        }
        "wait-connected" => {
            // Reuse one COM object rather than repeatedly creating workers and
            // potentially launching CpStart on every disconnected poll.
            wait_for_connection(
                || cybos.connected(),
                || std::thread::sleep(std::time::Duration::from_secs(1)),
            )?;
            println!("connected: true");
            Ok(0)
        }
        "quote" => {
            ensure!(cybos.connected()?, "CYBOS Plus 미연결");
            let code = code.context("종목코드 필요")?;
            super::validate_code(code)?;
            let stock = Dispatch::new("DsCbo1.StockMst")?;
            stock.call("SetInputValue", vec![0i32.into(), code.into()])?;
            let result = i32::try_from(&stock.call("BlockRequest", vec![])?)?;
            ensure!(result == 0, "BlockRequest 실패: {result}");
            let status = i32::try_from(&stock.call("GetDibStatus", vec![])?)?;
            ensure!(status == 0, "시세 응답 실패: {status}");
            let returned_code =
                BSTR::try_from(&stock.call("GetHeaderValue", vec![0i32.into()])?)?.to_string();
            let name =
                BSTR::try_from(&stock.call("GetHeaderValue", vec![1i32.into()])?)?.to_string();
            let price = i32::try_from(&stock.call("GetHeaderValue", vec![11i32.into()])?)?;
            ensure!(
                returned_code == code && !name.is_empty() && price > 0,
                "시세 응답 내용 검증 실패"
            );
            println!(
                "quote: code={returned_code}, name={name}, price={price} (조회 응답값이며 실시간성은 보장하지 않음)"
            );
            Ok(0)
        }
        _ => anyhow::bail!("알 수 없는 worker 명령"),
    }
}

fn wait_for_connection(
    mut check: impl FnMut() -> Result<bool>,
    mut pause: impl FnMut(),
) -> Result<()> {
    while !check()? {
        pause();
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn connection_error_stops_polling_instead_of_restarting() {
        let mut checks = 0;
        let mut pauses = 0;
        let result = wait_for_connection(
            || {
                checks += 1;
                match checks {
                    1 | 2 => Ok(false),
                    3 => anyhow::bail!("CPSTART server IP error"),
                    _ => panic!("must not retry after an error"),
                }
            },
            || pauses += 1,
        );
        assert!(result.is_err());
        assert_eq!((checks, pauses), (3, 2));
    }
}
