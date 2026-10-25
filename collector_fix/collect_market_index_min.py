import os
import time
import win32com.client
import pg8000.dbapi as psycopg2
from dotenv import load_dotenv
from datetime import datetime, timedelta

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

def get_latest_dates_for_indices():
    """DB에서 각 지표별로 가장 최근에 수집된 분봉 데이터의 날짜를 가져옵니다."""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT symbol, MAX(time) FROM market_index_min GROUP BY symbol;")
    # row[1]은 datetime 객체이므로 YYYYMMDD 형태의 정수로 변환 (데이터가 있는 경우에만)
    result = {row[0]: int(row[1].strftime("%Y%m%d")) for row in cursor.fetchall() if row[1]}
    cursor.close()
    conn.close()
    return result

def fetch_index_history(daishin_code, symbol, from_date, to_date, cpStatus, cpStockChart):
    """
    특정 지표의 과거 1분봉 데이터를 수집합니다.
    """
    cpStockChart.SetInputValue(0, daishin_code)
    cpStockChart.SetInputValue(1, ord('1')) # 기간으로 요청
    cpStockChart.SetInputValue(2, to_date)
    cpStockChart.SetInputValue(3, from_date)
    
    # 0:날짜, 1:시간, 5:종가
    cpStockChart.SetInputValue(5, [0, 1, 5])
        
    cpStockChart.SetInputValue(6, ord('m')) # 분봉
    cpStockChart.SetInputValue(9, ord('1')) # 수정주가 사용 (지표의 경우 영향이 없을 수 있음)
    
    result = []
    
    while True:
        check_and_wait(cpStatus)
        cpStockChart.BlockRequest()
        
        status = cpStockChart.GetDibStatus()
        if status != 0:
            print(f"통신 오류 ({daishin_code} - {symbol}): {cpStockChart.GetDibMsg1()}")
            raise RuntimeError("시장 지표 API 통신 실패")
            
        count = cpStockChart.GetHeaderValue(3)
        if count == 0:
            break
            
        for i in range(count):
            date = cpStockChart.GetDataValue(0, i)
            time_val = cpStockChart.GetDataValue(1, i)
            close_p = cpStockChart.GetDataValue(2, i)
            
            # YYYYMMDD HHMM -> TIMESTAMPTZ 형태 문자열 (KST 기준)
            date_str = str(date)
            time_str = f"{time_val:04d}"
            dt_str = f"{date_str[:4]}-{date_str[4:6]}-{date_str[6:8]} {time_str[:2]}:{time_str[2:4]}:00+09:00"
            
            result.append((dt_str, symbol, close_p))
                
        # 다음 페이지 데이터가 있는지 확인
        if not cpStockChart.Continue:
            break
            
    return result

def save_to_db(data):
    """수집된 데이터를 DB에 저장합니다."""
    if not data:
        return
        
    conn = get_db_connection()
    cursor = conn.cursor()
    
    query = """
        INSERT INTO market_index_min (time, symbol, value)
        VALUES (%s, %s, %s)
        ON CONFLICT (time, symbol) DO NOTHING
    """
        
    try:
        # 데이터가 많을 경우를 대비해 executemany 사용
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
    print("=== 시장 지표(1분봉) 수집 시작 ===")
    
    try:
        cpStatus = win32com.client.Dispatch('CpUtil.CpCybos')
        if cpStatus.IsConnect == 0:
            print("❌ CYBOS Plus에 연결되어 있지 않습니다. HTS 로그인을 먼저 해주세요.")
            raise RuntimeError("CYBOS Plus 미연결")
            
        cpStockChart = win32com.client.Dispatch("CpSysDib.StockChart")
    except Exception as e:
        print(f"❌ CYBOS 객체 생성 오류: {e}")
        raise

    # 수집할 지표 목록 (대신증권 코드: DB 심볼)
    # 추후 환율, 선물 등의 Cybos 코드를 확인하여 여기에 추가하면 동일하게 수집됩니다.
    INDEX_CODES = {
        'U001': 'KOSPI',      # 코스피 종합
        'U201': 'KOSDAQ',     # 코스닥 종합
        'U180': 'KOSPI200'    # 코스피 200
    }
    
    latest_dates = get_latest_dates_for_indices()
    
    # 최근 3개월 (90일) 날짜 범위 설정 (원하는 기간으로 조절 가능)
    now = datetime.now()
    three_months_ago = now - timedelta(days=90)
    
    to_date = int(now.strftime("%Y%m%d"))
    from_date = int(three_months_ago.strftime("%Y%m%d"))
    
    total = len(INDEX_CODES)
    for idx, (daishin_code, symbol) in enumerate(INDEX_CODES.items()):
        target_from_date = from_date
        
        # 이어받기/건너뛰기 로직
        if symbol in latest_dates:
            last_date_int = latest_dates[symbol]
            
            # DB에 저장된 최근 날짜가 수집 목표 종료일과 같다면 이미 수집 완료된 것으로 간주
            if last_date_int >= to_date:
                print(f"[{idx+1}/{total}] {symbol} ({daishin_code}) - 이미 최신({to_date}) 데이터가 수집되어 건너뜁니다.")
                continue
                
            # DB에 저장된 최근 날짜가 수집 시작일보다 크다면, 그 날짜부터만 수집하도록 변경 (Incremental)
            if last_date_int > target_from_date:
                target_from_date = last_date_int
                
        print(f"[{idx+1}/{total}] {symbol} ({daishin_code}) 수집 중 ({target_from_date}~{to_date})... ", end="", flush=True)
        
        min_data = fetch_index_history(daishin_code, symbol, target_from_date, to_date, cpStatus, cpStockChart)
        save_to_db(min_data)
        
        print(f"분봉 {len(min_data)}건 완료.")
        
    print("=== 수집이 모두 완료되었습니다 ===")

if __name__ == "__main__":
    main()
