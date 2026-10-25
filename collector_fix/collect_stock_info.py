import os
import win32com.client
import pg8000.dbapi as psycopg2
from dotenv import load_dotenv

def fetch_daishin_stocks():
    """
    대신증권 CYBOS Plus API를 사용하여 주식 종목 정보를 수집합니다.
    """
    try:
        cpStatus = win32com.client.Dispatch('CpUtil.CpCybos')
    except Exception as e:
        print(f"[오류] CYBOS 객체를 생성할 수 없습니다. 에러 메시지: {e}")
        print("💡 원인일 수 있는 항목들:")
        print("  1. 대신증권 CYBOS Plus HTS가 PC에 설치되지 않았습니다. (가장 유력함)")
        print("  2. 프로그램을 '관리자 권한'으로 실행하지 않았습니다.")
        raise

    if cpStatus.IsConnect == 0:
        print("[오류] CYBOS Plus에 연결되어 있지 않습니다. CYBOS Plus (또는 대신증권 API)에 먼저 로그인해 주세요.")
        raise RuntimeError("CYBOS Plus 미연결")

    cpCodeMgr = win32com.client.Dispatch("CpUtil.CpCodeMgr")
    cpMarketEye = win32com.client.Dispatch("CpSysDib.MarketEye")

    # 1: KOSPI, 2: KOSDAQ
    market_types = {1: "KOSPI", 2: "KOSDAQ"}
    stocks = []

    print("CYBOS Plus에서 주식 종목 정보를 가져오는 중...")

    for market_code, market_name in market_types.items():
        code_list = cpCodeMgr.GetStockListByMarket(market_code)
        
        for code in code_list:
            # 주권(1), ETF(10) 등 대상 필터링
            kind = cpCodeMgr.GetStockSectionKind(code)
            if kind not in (1, 10):
                continue
                
            stock_name = cpCodeMgr.CodeToName(code)
            
            # 업종 코드 및 이름 (ETF의 경우 업종이 없을 수 있음)
            industry_code = cpCodeMgr.GetStockIndustryCode(code)
            sector = cpCodeMgr.GetIndustryName(industry_code)
            if not sector:
                sector = "기타"
            
            # 상태 (0:정상, 1:거래정지, 2:거래중단)
            status = cpCodeMgr.GetStockStatusKind(code)
            is_active = (status == 0)
            
            # 종목코드에서 첫 영문자 제거 (예: 'A005930' -> '005930', 'Q500001' -> '500001')
            db_code = code[1:] if code[0].isalpha() else code
            
            stocks.append({
                'code_daishin': code,
                'stock_code': db_code,
                'stock_name': stock_name,
                'market_type': market_name,
                'sector': sector,
                'is_active': is_active,
                'listing_shares': 0  # MarketEye를 통해 나중에 일괄 업데이트
            })

    print(f"총 {len(stocks)}개의 종목 정보를 찾았습니다. 상장주식수를 조회합니다...")
    if not stocks:
        raise RuntimeError("CYBOS 종목 목록이 비어 있습니다")

    # MarketEye를 이용해 상장주식수 조회 (최대 200개씩 끊어서 요청)
    chunk_size = 200
    code_to_stock = {s['code_daishin']: s for s in stocks}
    daishin_codes = list(code_to_stock.keys())
    
    for i in range(0, len(daishin_codes), chunk_size):
        chunk = daishin_codes[i:i + chunk_size]
        
        # 0: 종목코드, 20: 총상장주식수
        cpMarketEye.SetInputValue(0, [0, 20])  
        cpMarketEye.SetInputValue(1, chunk)
        cpMarketEye.BlockRequest()
        
        # 통신 상태 확인 (0: 정상)
        if cpMarketEye.GetDibStatus() != 0:
            print(f"⚠️ 통신 에러 발생: {cpMarketEye.GetDibMsg1()}")
            raise RuntimeError("MarketEye 통신 실패")
            
        cnt = cpMarketEye.GetHeaderValue(2) # 수신 종목 수
        
        for j in range(cnt):
            ret_code = cpMarketEye.GetDataValue(0, j)
            shares = cpMarketEye.GetDataValue(1, j)
            
            if ret_code in code_to_stock:
                code_to_stock[ret_code]['listing_shares'] = shares

    return stocks

def update_db(stocks):
    """
    수집한 주식 종목 정보를 DB에 INSERT 하거나 UPDATE 합니다.
    """
    if not stocks:
        print("업데이트할 종목 데이터가 없습니다.")
        return

    load_dotenv()
    
    DB_HOST = os.getenv("DB_HOST", "localhost")
    DB_PORT = os.getenv("DB_PORT", "5432")
    DB_NAME = os.getenv("DB_NAME", "kr_stocks")
    DB_USER = os.getenv("DB_USER", "postgres")
    DB_PASSWORD = os.getenv("DB_PASSWORD", "password")

    print(f"[{DB_HOST}:{DB_PORT}] '{DB_NAME}' 데이터베이스에 연결하여 데이터 갱신 중...")
    
    try:
        conn = psycopg2.connect(
            host=DB_HOST,
            port=int(DB_PORT),
            database=DB_NAME,
            user=DB_USER,
            password=DB_PASSWORD
        )
        cursor = conn.cursor()
        
        # stock_info 테이블에 맞게 UPSERT 쿼리 작성 (PostgreSQL ON CONFLICT 구문 사용)
        query = """
            INSERT INTO stock_info (
                stock_code, stock_name, market_type, sector, 
                listing_shares, is_active, updated_at
            ) VALUES (
                %s, %s, %s, %s, %s, %s, CURRENT_TIMESTAMP
            )
            ON CONFLICT (stock_code) DO UPDATE SET
                stock_name = EXCLUDED.stock_name,
                market_type = EXCLUDED.market_type,
                sector = EXCLUDED.sector,
                listing_shares = EXCLUDED.listing_shares,
                is_active = EXCLUDED.is_active,
                updated_at = EXCLUDED.updated_at
        """
        
        # 쿼리 파라미터 리스트 생성
        data_to_insert = [
            (
                s['stock_code'], s['stock_name'], s['market_type'], 
                s['sector'], s['listing_shares'], s['is_active']
            ) 
            for s in stocks
        ]
        
        # 일괄 실행
        cursor.executemany(query, data_to_insert)
        conn.commit()
        
        print(f"✅ 총 {len(data_to_insert)}개의 종목 메타 정보가 DB에 성공적으로 저장/업데이트 되었습니다.")
        
    except psycopg2.OperationalError as e:
        print(f"❌ 데이터베이스 연결 오류: {e}")
        print("팁: PostgreSQL 서버가 실행 중인지, 그리고 .env 파일의 접속 정보가 맞는지 확인하세요.")
        raise
    except Exception as e:
        print(f"❌ DB 업데이트 중 오류 발생: {e}")
        if 'conn' in locals():
            conn.rollback()
        raise
    finally:
        if 'cursor' in locals():
            cursor.close()
        if 'conn' in locals():
            conn.close()

if __name__ == "__main__":
    print("=== 대신증권 종목 메타 정보 수집 시작 ===")
    
    # 1. API를 통해 데이터 수집
    stock_data = fetch_daishin_stocks()
    
    # 2. 수집된 데이터를 DB에 업데이트
    update_db(stock_data)
    
    print("=== 수집 및 업데이트 완료 ===")
