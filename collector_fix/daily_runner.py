import os
import subprocess
import json
import urllib.request
import traceback
import sys
from dotenv import load_dotenv


def send_telegram_message(message):
    load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"))
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    chat_id = os.getenv("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        print("Telegram 설정이 없어 알림을 생략합니다.", flush=True)
        return
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    data = json.dumps({"chat_id": chat_id, "text": message}).encode("utf-8")
    try:
        req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=15):
            pass
    except Exception as error:
        # Exceptions may include the token-bearing URL. Never print their text.
        print(f"Telegram 알림 실패 ({type(error).__name__})", flush=True)


def main():
    send_telegram_message("🚀 [시작] 주식 데이터 수집 스케줄러가 시작되었습니다.")
    scripts = [
        "collect_stock_info.py",
        "collect_market_index_min.py",
        "collect_investor_trend_min.py",
        "collect_stock_history.py",
    ]
    base_dir = os.path.dirname(os.path.abspath(__file__))
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUNBUFFERED"] = "1"
    for script in scripts:
        print(f"[{script}] 실행 중...", flush=True)
        try:
            # Inherit both streams so the Rust log records progress immediately.
            result = subprocess.run(
                [sys.executable, "-u", os.path.join(base_dir, script)],
                cwd=base_dir, env=env, check=False,
            )
            if result.returncode != 0:
                message = f"❌ [에러 발생] {script} 실행 실패 (Exit Code: {result.returncode})"
                print(message, file=sys.stderr, flush=True)
                send_telegram_message(message)
                return result.returncode
            print(f"[{script}] 완료", flush=True)
        except Exception:
            print(f"[{script}] 실행 중 예외\n{traceback.format_exc()}", file=sys.stderr, flush=True)
            send_telegram_message(f"❌ [예외 발생] {script} 실행 실패. 로컬 로그를 확인하세요.")
            return 1
    send_telegram_message("✅ [종료] 주식 데이터 수집 스케줄러가 정상적으로 완료되었습니다.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
