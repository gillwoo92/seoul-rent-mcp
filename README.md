# 서울 아파트 전월세 MCP 서버

국토교통부 아파트 전월세 실거래가 공공데이터([공공데이터포털](https://www.data.go.kr), `RTMSDataSvcAptRent`)를
[MCP](https://modelcontextprotocol.io) 도구로 제공하는 [FastMCP](https://gofastmcp.com) 단일 파일 서버입니다.
서울 25개 구의 전세 시세, 월별 추이, 갱신 인상률, 월세화 비율, 단지별 시세를 조회할 수 있습니다.

## 제공 도구

| 도구 | 설명 |
|---|---|
| `seoul_jeonse_by_district` | **서울 지역별 전세 시세.** 25개 구를 ㎡당 전세가 순으로 비교하고, 구를 지정하면 동별로 비교 |
| `monthly_price_trend` | **월별 시세 추이.** 전세 중간값, ㎡당·평당 가격, 거래량, 월세 비율과 전월 대비 변화 |
| `renewal_increase` | **갱신 계약 인상률.** 종전 보증금 대비 인상률, 계약갱신청구권(5% 상한) 사용/미사용 비교, 구별 순위 |
| `wolse_ratio` | **월세화 비율.** 구·동별, 면적대별 월세 비중과 갱신 시 전세→월세 전환 비율 |
| `complex_price_table` | **단지별 시세표.** 아파트 이름(일부)으로 찾아 면적별 전세·월세 최근가, 중간값, 최근 거래 |

### 매개변수

| 이름 | 형식 | 설명 |
|---|---|---|
| `district` | `강남구`, `강남`, `서울 강남구` | 생략하거나 `서울`이면 25개 구 전체 |
| `month` / `end_month` | `202610` 또는 `2026-10` | 생략하면 지난달 |
| `months` | 정수 | 여러 달을 조회하는 도구에서 기간(개월) |
| `apartment` | 문자열 | `complex_price_table` 전용, 아파트 이름 일부 |

금액은 API 원본 단위인 **만원**이며, `6억 2,000만원`처럼 읽기 쉬운 표기도 함께 반환합니다.

## 설치

Python 3.10 이상이 필요합니다.

```bash
git clone https://github.com/gillwoo92/seoul-rent-mcp.git
cd seoul-rent-mcp
python -m venv .venv
```

```bash
# Windows
.venv\Scripts\pip install -r requirements.txt

# macOS / Linux
.venv/bin/pip install -r requirements.txt
```

외부 HTTP 호출과 XML 해석은 표준 라이브러리로 처리하므로 `fastmcp` 외에 필요한 패키지는 없습니다.

## API 키 설정

1. [공공데이터포털](https://www.data.go.kr)에서 **국토교통부_아파트 전월세 실거래가 자료**를 활용신청하고 일반 인증키를 받습니다.
2. `.env.example` 을 `.env` 로 복사하고 키를 입력합니다.

```
DATA_GO_KR_API_KEY=발급받은_일반_인증키
```

`.env` 는 `.gitignore` 에 포함되어 있으니 **절대 커밋하지 마세요.**

## 실행

```bash
# stdio (MCP 클라이언트가 직접 실행)
.venv/bin/python rent_mcp_server.py

# Streamable HTTP  →  http://127.0.0.1:8002/mcp
.venv/bin/python rent_mcp_server.py --http 8002
```

Windows 에서는 `.venv\Scripts\python.exe` 를 사용하세요.

## MCP 클라이언트에 연결

프로젝트 루트의 `.mcp.json` (Claude Code) 또는 `claude_desktop_config.json` (Claude Desktop) 에 추가합니다.

```json
{
  "mcpServers": {
    "seoul-rent": {
      "type": "stdio",
      "command": "C:\\경로\\seoul-rent-mcp\\.venv\\Scripts\\python.exe",
      "args": ["C:\\경로\\seoul-rent-mcp\\rent_mcp_server.py"],
      "env": { "PYTHONIOENCODING": "utf-8" }
    }
  }
}
```

Claude Code 에서는 새 세션을 열고 `/mcp` 로 `seoul-rent` 가 connected 인지 확인합니다.

## 사용 예시

- "서울 구별 전세 시세 순위 보여줘" → `seoul_jeonse_by_district`
- "강남구 최근 1년 전세 시세 추이" → `monthly_price_trend`
- "지난달 갱신 계약 인상률, 청구권 쓴 거랑 비교해줘" → `renewal_increase`
- "월세 비율 높은 구는 어디야?" → `wolse_ratio`
- "헬리오시티 면적별 전세 시세" → `complex_price_table`

## 동작 방식과 주의점

- **조회 단위**: API가 구 하나·한 달 단위라, 서울 전체는 25개 구를 병렬(8개씩)로 받습니다. 받은 데이터는 6시간 메모리 캐시됩니다.
- **최근 데이터**: 실거래 신고 기한(계약 후 30일) 때문에 최근 1~2개월 수치는 이후 신고분이 더해지며 바뀔 수 있습니다.
- **전세/월세 구분**: 월세가 0이면 전세, 0보다 크면 월세(반전세 포함)로 봅니다.
- **갱신 인상률**: 전세→전세 갱신만 계산합니다. 5% 상한에 맞춘 갱신이 많아 중간값은 대개 5.0%이므로 지역 비교는 평균과 5% 초과 비율로 합니다.
- **순위 기준**: 면적 차이를 보정하려고 전세 순위는 ㎡당 보증금 중간값으로 매깁니다.
