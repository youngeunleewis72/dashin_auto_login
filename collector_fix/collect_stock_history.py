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

def get_active_stocks():
    """활성화된 주식 종목 코드를 DB에서 가져옵니다."""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT stock_code FROM stock_info WHERE is_active = TRUE;")
    stocks = [row[0] for row in cursor.fetchall()]
    cursor.close()
    conn.close()
    return stocks

def get_date_ranges_for_stocks():
    """DB에서 활성 종목별로 가장 최근 수집된 날짜(MAX)를 인덱스를 활용해 빠르게 가져옵니다."""
    conn = get_db_connection()
    cursor = conn.cursor()
    
    result = {'day': {}, 'min': {}}
    
    # 일봉 최신 날짜 조회 (LATERAL + ORDER BY DESC LIMIT 1로 인덱스 즉시 조회)
    cursor.execute("""
        SELECT s.stock_code, d.max_date
        FROM stock_info s
        CROSS JOIN LATERAL (
            SELECT date AS max_date
            FROM stock_day
            WHERE stock_code = s.stock_code
            ORDER BY date DESC
            LIMIT 1
        ) d
        WHERE s.is_active = TRUE;
    """)
    for row in cursor.fetchall():
        if row[1]:
            max_date = int(row[1].strftime("%Y%m%d"))
            result['day'][row[0]] = {'max': max_date}
            
    # 분봉 최신 날짜 조회 (전체 테이블 GROUP BY 스캔 대신 최신 청크/인덱스 1건 조회)
    cursor.execute("""
        SELECT s.stock_code, m.max_time
        FROM stock_info s
        CROSS JOIN LATERAL (
            SELECT time AS max_time
            FROM stock_min
            WHERE stock_code = s.stock_code
            ORDER BY time DESC
            LIMIT 1
        ) m
        WHERE s.is_active = TRUE;
    """)
    for row in cursor.fetchall():
        if row[1]:
            max_date = int(row[1].strftime("%Y%m%d"))
            result['min'][row[0]] = {'max': max_date}
            
    cursor.close()
    conn.close()
    return result

def build_code_map():
    """DB의 종목코드(예: '005930')를 대신증권 API 종목코드(예: 'A005930')로 변환하기 위한 맵을 생성합니다."""
    cpCodeMgr = win32com.client.Dispatch("CpUtil.CpCodeMgr")
    code_map = {}
    for market in [1, 2]: # 1: KOSPI, 2: KOSDAQ
        for code in cpCodeMgr.GetStockListByMarket(market):
            db_code = code[1:] if code[0].isalpha() else code
            code_map[db_code] = code
    return code_map

def check_and_wait(cpStatus):
    """API 요청 제한(15초 60건)을 확인하고 도달 시 대기합니다."""
    remain_count = cpStatus.GetLimitRemainCount(1) # 1: 시세(NONTRADE)
    if remain_count <= 2:
        remain_time = cpStatus.LimitRequestRemainTime
        print(f"[API 제한 도달 임박] 남은 횟수: {remain_count}, {remain_time}ms 대기 중...")
        time.sleep(remain_time / 1000 + 0.1)

def fetch_history(stock_code, daishin_code, chart_type, from_date, to_date, cpStatus, cpStockChart):
    """
    특정 종목의 과거 데이터를 수집합니다.
    - chart_type: 'm'(1분봉), 'D'(1일봉)
    """
    cpStockChart.SetInputValue(0, daishin_code)
    cpStockChart.SetInputValue(1, ord('1')) # 기간으로 요청
    cpStockChart.SetInputValue(2, to_date)
    cpStockChart.SetInputValue(3, from_date)
    
    if chart_type == 'm':
        # 0:날짜, 1:시간, 2:시가, 3:고가, 4:저가, 5:종가, 8:거래량, 9:거래대금
        cpStockChart.SetInputValue(5, [0, 1, 2, 3, 4, 5, 8, 9])
    else:
        # 0:날짜, 2:시가, 3:고가, 4:저가, 5:종가, 8:거래량, 9:거래대금 (시간 제외)
        cpStockChart.SetInputValue(5, [0, 2, 3, 4, 5, 8, 9])
        
    cpStockChart.SetInputValue(6, ord(chart_type))
    cpStockChart.SetInputValue(9, ord('1')) # 수정주가 사용
    
    result = []
    
    while True:
        check_and_wait(cpStatus)
        cpStockChart.BlockRequest()
        
        status = cpStockChart.GetDibStatus()
        if status != 0:
            print(f"통신 오류 ({daishin_code}, {chart_type}): {cpStockChart.GetDibMsg1()}")
            raise RuntimeError("주식 이력 API 통신 실패")
            
        count = cpStockChart.GetHeaderValue(3)
        if count == 0:
            break
            
        for i in range(count):
            if chart_type == 'm':
                date = cpStockChart.GetDataValue(0, i)
                time_val = cpStockChart.GetDataValue(1, i)
                open_p = cpStockChart.GetDataValue(2, i)
                high_p = cpStockChart.GetDataValue(3, i)
                low_p = cpStockChart.GetDataValue(4, i)
                close_p = cpStockChart.GetDataValue(5, i)
                volume = cpStockChart.GetDataValue(6, i)
                trading_value = cpStockChart.GetDataValue(7, i)
                
                # YYYYMMDD HHMM -> TIMESTAMPTZ 형태 문자열 (KST 기준)
                date_str = str(date)
                time_str = f"{time_val:04d}"
                dt_str = f"{date_str[:4]}-{date_str[4:6]}-{date_str[6:8]} {time_str[:2]}:{time_str[2:4]}:00+09:00"
                
                # 장구분 판단
                if time_val < 900:
                    session = 'PRE'
                elif time_val <= 1530:
                    session = 'REGULAR'
                else:
                    session = 'AFTER'
                
                result.append((dt_str, stock_code, open_p, high_p, low_p, close_p, volume, trading_value, session))
            else:
                date = cpStockChart.GetDataValue(0, i)
                open_p = cpStockChart.GetDataValue(1, i)
                high_p = cpStockChart.GetDataValue(2, i)
                low_p = cpStockChart.GetDataValue(3, i)
                close_p = cpStockChart.GetDataValue(4, i)
                volume = cpStockChart.GetDataValue(5, i)
                trading_value = cpStockChart.GetDataValue(6, i)
                
                # YYYYMMDD -> DATE 형태 문자열
                date_str = str(date)
                dt_str = f"{date_str[:4]}-{date_str[4:6]}-{date_str[6:8]}"
                
                result.append((dt_str, stock_code, open_p, high_p, low_p, close_p, volume, trading_value))
                
        # 다음 페이지 데이터가 있는지 확인
        if not cpStockChart.Continue:
            break
            
    return result

def save_to_db(table_name, data):
    """수집된 데이터를 DB에 저장합니다."""
    if not data:
        return
        
    conn = get_db_connection()
    cursor = conn.cursor()
    
    if table_name == 'stock_min':
        query = """
            INSERT INTO stock_min (time, stock_code, open, high, low, close, volume, trading_value, market_session)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (time, stock_code) DO NOTHING
        """
    else:
        query = """
            INSERT INTO stock_day (date, stock_code, open, high, low, close, volume, trading_value)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (date, stock_code) DO NOTHING
        """
        
    try:
        # 데이터가 많을 경우를 대비해 executemany 사용 (chunk 삽입 방식이 더 빠를 수 있음)
        cursor.executemany(query, data)
        conn.commit()
    except Exception as e:
        print(f"DB 저장 오류 ({table_name}): {e}")
        conn.rollback()
        raise
    finally:
        cursor.close()
        conn.close()

def get_latest_trading_date(cpStatus, cpStockChart):
    """삼성전자 데이터를 조회하여 가장 최근 영업일을 확인합니다."""
    cpStockChart.SetInputValue(0, "A005930") # 삼성전자
    cpStockChart.SetInputValue(1, ord('2')) # 개수로 요청
    cpStockChart.SetInputValue(4, 1) # 1개 요청
    cpStockChart.SetInputValue(5, [0]) # 0: 날짜
    cpStockChart.SetInputValue(6, ord('D')) # 일봉
    cpStockChart.SetInputValue(9, ord('1')) # 수정주가
    
    check_and_wait(cpStatus)
    cpStockChart.BlockRequest()
    
    status = cpStockChart.GetDibStatus()
    if status != 0:
        raise RuntimeError("최근 영업일 API 통신 실패")
    if status == 0 and cpStockChart.GetHeaderValue(3) > 0:
        return cpStockChart.GetDataValue(0, 0) # YYYYMMDD (int)
        
    # 실패시 오늘 날짜 반환
    from datetime import datetime
    return int(datetime.now().strftime("%Y%m%d"))

def main():
    print("=== 주식 일봉/분봉 수집 시작 ===")
    
    try:
        cpStatus = win32com.client.Dispatch('CpUtil.CpCybos')
        if cpStatus.IsConnect == 0:
            print("❌ CYBOS Plus에 연결되어 있지 않습니다. HTS 로그인을 먼저 해주세요.")
            raise RuntimeError("CYBOS Plus 미연결")
            
        cpStockChart = win32com.client.Dispatch("CpSysDib.StockChart")
    except Exception as e:
        print(f"❌ CYBOS 객체 생성 오류: {e}")
        raise

    # 최근 주식장 영업일 확인
    latest_trading_date = get_latest_trading_date(cpStatus, cpStockChart)
    print(f"📈 최근 영업일: {latest_trading_date}")

    stocks = get_active_stocks()
    code_map = build_code_map()
    date_ranges = get_date_ranges_for_stocks()
    
    # 일봉 4년, 분봉 2년 초기 수집 날짜 범위 설정 (신규 종목용)
    now = datetime.now()
    day_from_date = int((now - timedelta(days=365 * 4)).strftime("%Y%m%d"))
    min_from_date = int((now - timedelta(days=365 * 2)).strftime("%Y%m%d"))
    global_to_date = latest_trading_date
    
    # 휴일이거나 이미 모든 데이터가 최신인 경우 빠른 종료
    all_up_to_date = True
    for stock in stocks:
        if stock not in code_map:
            continue
        day_max = date_ranges['day'].get(stock, {}).get('max', 0)
        if day_max < latest_trading_date:
            all_up_to_date = False
            break
            
    if all_up_to_date and len(stocks) > 0:
        print("✅ 모든 종목이 이미 최신 상태입니다. (주식장 휴일이거나 당일 수집 완료)")
        print("=== 수집이 모두 완료되었습니다 ===")
        return

    print(f"DB에서 활성 종목 {len(stocks)}개를 가져왔습니다.")
    print(f"초기 수집 설정 기간 - 일봉: 4년, 분봉: 2년 (기존 종목은 최신 영업일 증분만 수집)")
    
    total = len(stocks)
    skipped_count = 0
    for idx, stock_code in enumerate(stocks):
        daishin_code = code_map.get(stock_code)
        if not daishin_code:
            print(f"[{idx+1}/{total}] {stock_code} - 매핑되는 대신증권 코드가 없어 스킵합니다.")
            continue
            
        day_db_max = date_ranges['day'].get(stock_code, {}).get('max', 0)
        min_db_max = date_ranges['min'].get(stock_code, {}).get('max', 0)

        day_ranges = [] # 일봉 수집 구간
        if stock_code in date_ranges['day']:
            if global_to_date > day_db_max:
                day_ranges.append((day_db_max, global_to_date))
        else:
            day_ranges.append((day_from_date, global_to_date))

        min_ranges = [] # 분봉 수집 구간
        if stock_code in date_ranges['min']:
            # 일봉이 아직 최신 영업일 전이고 분봉도 최신 영업일 전인 경우에만 증분 수집
            # (거래량이 0이어서 당일 분봉이 없는 종목을 매번 재요청하는 현상 방지)
            if global_to_date > min_db_max and day_db_max < global_to_date:
                min_ranges.append((min_db_max, global_to_date))
        elif day_db_max < global_to_date:
            min_ranges.append((min_from_date, global_to_date))
            
        if not day_ranges and not min_ranges:
            skipped_count += 1
            continue
            
        print(f"[{idx+1}/{total}] {stock_code} ({daishin_code}) 수집... ", end="", flush=True)
        
        day_count, min_count = 0, 0
        
        # 1. 분봉 먼저 수집 및 저장 (중단 시 재실행할 때 일봉 완료 여부로 판단하기 위함)
        for start_dt, end_dt in min_ranges:
            min_data = fetch_history(stock_code, daishin_code, 'm', start_dt, end_dt, cpStatus, cpStockChart)
            save_to_db('stock_min', min_data)
            min_count += len(min_data)

        # 2. 일봉 수집 및 저장
        for start_dt, end_dt in day_ranges:
            day_data = fetch_history(stock_code, daishin_code, 'D', start_dt, end_dt, cpStatus, cpStockChart)
            save_to_db('stock_day', day_data)
            day_count += len(day_data)
            
        print(f"일봉 {day_count}건, 분봉 {min_count}건 완료.")
        
    if skipped_count > 0:
        print(f"ℹ️ 이미 최신 상태인 {skipped_count}개 종목은 건너뛰었습니다.")
    print("=== 수집이 모두 완료되었습니다 ===")

if __name__ == "__main__":
    main()
