import os
import time
from datetime import datetime, timedelta
import win32com.client
import pg8000.dbapi as psycopg2
from dotenv import load_dotenv

def get_db_connection():
    """DB 연결 객체를 반환합니다."""
    load_dotenv()
    DB_HOST = os.getenv("DB_HOST", "localhost")
    DB_PORT = os.getenv("DB_PORT", "5432")
    DB_NAME = os.getenv("DB_NAME", "kr_stocks")
    DB_USER = os.getenv("DB_USER", "postgres")
    DB_PASSWORD = os.getenv("DB_PASSWORD", "password")
    
    return psycopg2.connect(
        host=DB_HOST,
        port=int(DB_PORT),
        database=DB_NAME,
        user=DB_USER,
        password=DB_PASSWORD
    )

def check_and_wait(cpStatus):
    """API 요청 제한(15초 60건)을 확인하고 도달 시 대기합니다."""
    remain_count = cpStatus.GetLimitRemainCount(1) # 1: 시세(NONTRADE)
    if remain_count <= 2:
        remain_time = cpStatus.LimitRequestRemainTime
        print(f"[API 제한 도달 임박] 남은 횟수: {remain_count}, {remain_time}ms 대기 중...")
        time.sleep(remain_time / 1000 + 0.1)

def get_market_code_7222(market_type):
    # 대신증권 API 시장구분 코드 (CpSvrNew7222 기준)
    if market_type == 'KOSPI':
        return ord('B') # 거래소
    elif market_type == 'KOSDAQ':
        return ord('C') # 코스닥
    return ord('B')

def get_market_code_8112(market_type):
    # 대신증권 API 시장구분 코드 (PgAtime8112 기준)
    if market_type == 'KOSPI':
        return ord('1')
    elif market_type == 'KOSDAQ':
        return ord('2')
    return ord('1')

def fetch_investor_trend(market_type, investor_code, cpStatus, cpSvr, target_date):
    """
    특정 시장과 투자자의 당일 1분봉(시간대별) 매매 동향(증감)을 수집합니다.
    """
    cpSvr.SetInputValue(0, get_market_code_7222(market_type)) 
    cpSvr.SetInputValue(1, investor_code)            
    cpSvr.SetInputValue(2, ord('2')) # 2: 증감 (분별 데이터)
    cpSvr.SetInputValue(4, ord('2')) # 2: 금액 (순매수대금, 단위: 백만원/천원 등 API 스펙에 따름)
    
    result = []
    
    while True:
        check_and_wait(cpStatus)
        cpSvr.BlockRequest()
        
        status = cpSvr.GetDibStatus()
        if status != 0:
            print(f"통신 오류 ({market_type}, {investor_code}): {cpSvr.GetDibMsg1()}")
            raise RuntimeError("투자자 동향 API 통신 실패")
            
        count = cpSvr.GetHeaderValue(0)
        if count == 0:
            break
            
        date_str = target_date.strftime("%Y-%m-%d")
            
        for i in range(count):
            time_val = cpSvr.GetDataValue(0, i) # 시간 HHMM
            net_buy = cpSvr.GetDataValue(3, i)  # 순매수 (대금)
            
            time_str = f"{time_val:04d}"
            dt_str = f"{date_str} {time_str[:2]}:{time_str[2:4]}:00+09:00"
            
            result.append((dt_str, net_buy))
            
        # Continue 플래그 확인 (당일 데이터이므로 보통 한 페이지에 다 나오지만 혹시 모름)
        if not cpSvr.Continue:
            break
            
    return result

def is_invalid_argument(error):
    """pywin32 can wrap E_INVALIDARG inside DISP_E_EXCEPTION's EXCEPINFO."""
    codes = [getattr(error, "hresult", None)]
    info = getattr(error, "excepinfo", None)
    if isinstance(info, (tuple, list)) and len(info) > 5:
        codes.append(info[5])
    return any(isinstance(code, int) and (code & 0xFFFFFFFF) == 0x80070057 for code in codes)


def fetch_program_trend(market_type, cpStatus, cpProg, target_date):
    """
    시장 전체 프로그램 매매 당일 분봉 데이터를 수집합니다.
    (DsCbo1.PgAtime8112 TR 사용)
    """
    # PgAtime8112 파라미터 유추 적용
    # 만약 이 파라미터가 맞지 않는다면 CYBOS 도움말을 참조하여 수정해야 합니다.
    # 일반적으로 SetInputValue(0, 시장코드)
    # GetHeaderValue(0) = count
    # GetDataValue(0, i) = 시간
    # GetDataValue(3, i) 또는 (9, i) = 순매수
    try:
        cpProg.SetInputValue(0, get_market_code_8112(market_type))
    except Exception as e:
        print(f"PgAtime8112 인자 설정 오류: {e}")
        raise
        
    result = []
    
    while True:
        check_and_wait(cpStatus)
        cpProg.BlockRequest()
        
        status = cpProg.GetDibStatus()
        if status != 0:
            print(f"통신 오류 (PROGRAM, {market_type}): {cpProg.GetDibMsg1()}")
            raise RuntimeError("프로그램 매매 API 통신 실패")
            
        count = cpProg.GetHeaderValue(0)
        if count == 0:
            break
            
        date_str = target_date.strftime("%Y-%m-%d")
            
        for i in range(count):
            try:
                time_val = cpProg.GetDataValue(0, i)
            except Exception as e:
                # Compatibility with the existing header/row mismatch: accept
                # only exhaustion of the NEXT row after at least one full row.
                # Invalid fields or an unreadable first row must remain failures.
                if i > 0 and is_invalid_argument(e):
                    print(f"[PROGRAM] 실제 수신 {i}행에서 종료 (헤더 값 {count})")
                    break
                raise

            arb_sell = cpProg.GetDataValue(1, i) or 0
            arb_buy = cpProg.GetDataValue(2, i) or 0
            nonarb_sell = cpProg.GetDataValue(3, i) or 0
            nonarb_buy = cpProg.GetDataValue(4, i) or 0
            total_net = cpProg.GetDataValue(7, i) or 0
            time_str = f"{time_val:04d}"
            dt_str = f"{date_str} {time_str[:2]}:{time_str[2:4]}:00+09:00"
            result.extend([
                (dt_str, 'PROGRAM_ARB', arb_buy - arb_sell),
                (dt_str, 'PROGRAM_NONARB', nonarb_buy - nonarb_sell),
                (dt_str, 'PROGRAM_TOTAL', total_net),
            ])
                
        # PgaTime8112 객체는 Continue 속성을 지원하지 않아 접근 시 COM 예외(E_INVALIDARG)가 발생할 수 있습니다.
        # 하루치(또는 수일치) 데이터가 한 번에 반환되므로 페이징 없이 종료합니다.
        break
            
    return result

def save_to_db(data):
    """수집된 데이터를 DB에 저장합니다."""
    if not data:
        return
        
    conn = get_db_connection()
    cursor = conn.cursor()
    
    query = """
        INSERT INTO investor_trend_min (time, market_type, investor_type, net_buy_value)
        VALUES (%s, %s, %s, %s)
        ON CONFLICT (time, market_type, investor_type) DO UPDATE
        SET net_buy_value = EXCLUDED.net_buy_value
    """
        
    try:
        cursor.executemany(query, data)
        conn.commit()
    except Exception as e:
        print(f"DB 저장 오류: {e}")
        conn.rollback()
        raise
    finally:
        cursor.close()
        conn.close()

def main():
    print("=== 투자자별 / 프로그램 매매 동향(1분봉) 수집 시작 ===")
    
    try:
        cpStatus = win32com.client.Dispatch('CpUtil.CpCybos')
        if cpStatus.IsConnect == 0:
            print("[오류] CYBOS Plus에 연결되어 있지 않습니다. HTS 로그인을 먼저 해주세요.")
            raise RuntimeError("CYBOS Plus 미연결")
            
        cpInvestor = win32com.client.Dispatch("CpSysDib.CpSvrNew7222")
        cpProgram = win32com.client.Dispatch("DsCbo1.PgAtime8112")
    except Exception as e:
        print(f"[오류] CYBOS 객체 생성 오류: {e}")
        raise

    # CYBOS 서버의 기준일(마지막 거래일) 가져오기
    target_date = datetime.now()
    try:
        chart = win32com.client.Dispatch("CpSysDib.StockChart")
        chart.SetInputValue(0, "U001") # KOSPI 종합지수
        chart.SetInputValue(1, ord('2')) # 개수로 요청
        chart.SetInputValue(4, 1) # 1개
        chart.SetInputValue(5, [0]) # 0: 날짜
        chart.SetInputValue(6, ord('D')) # D: 일차트
        chart.BlockRequest()
        
        if chart.GetDibStatus() == 0 and chart.GetHeaderValue(3) > 0:
            last_date_int = chart.GetDataValue(0, 0)
            target_date = datetime.strptime(str(last_date_int), "%Y%m%d")
    except Exception as e:
        print(f"[경고] 기준일 조회 실패 (기본값 오늘 날짜 사용): {e}")
    
    now_date = datetime.now()
    if now_date.hour < 8:
        now_date = now_date - timedelta(days=1)
        
    print(f"[알림] 현재 CYBOS 서버 영업일 기준: {target_date.strftime('%Y-%m-%d')}")
    if target_date.strftime("%Y-%m-%d") != now_date.strftime("%Y-%m-%d"):
        print("  -> 오늘(또는 수집 기준일)이 휴장일이거나, 장 시작 전이어서 마지막 거래일의 데이터를 수집합니다.")
        print("  -> (DB의 UPSERT 기능으로 인해 중복 시 덮어쓰기 되므로 안전합니다.)")

    # 투자자 구분 매핑 (CpSvrNew7222 기준)
    INVESTOR_MAP = {
        'RETAIL': 1,      # 개인
        'FOREIGNER': 2,   # 외국인
        'INSTITUTION': 3  # 기관계
    }
    
    MARKETS = ['KOSPI', 'KOSDAQ']
    
    all_data = []

    for market in MARKETS:
        print(f"\n[{market}] 데이터 수집 시작...")
        
        # 1. 투자자별 동향 수집
        for inv_name, inv_code in INVESTOR_MAP.items():
            print(f"  - {inv_name} 수집 중... ", end="", flush=True)
            try:
                inv_data = fetch_investor_trend(market, inv_code, cpStatus, cpInvestor, target_date)
                formatted_data = [(dt, market, inv_name, val) for dt, val in inv_data]
                all_data.extend(formatted_data)
                print(f"{len(inv_data)}건 완료.")
            except Exception as e:
                print(f"오류 발생: {e}")
                raise
                
        # 2. 프로그램 매매 수집
        print(f"  - PROGRAM 수집 중... ", end="", flush=True)
        try:
            prog_data = fetch_program_trend(market, cpStatus, cpProgram, target_date)
            formatted_data = [(dt, market, inv_type, val) for dt, inv_type, val in prog_data]
            all_data.extend(formatted_data)
            # 한 번의 시간대에 3개의 row가 추가되므로 3으로 나누어 표시
            print(f"{len(prog_data)//3}건 완료.")
        except Exception as e:
            print(f"오류 발생: {e}")
            import traceback
            traceback.print_exc()
            raise
            
    print(f"\n총 {len(all_data)}건의 데이터를 DB에 저장합니다...")
    save_to_db(all_data)
    print("=== 수집이 모두 완료되었습니다 ===")

if __name__ == "__main__":
    main()
