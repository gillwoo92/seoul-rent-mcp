# 🏠 서울 아파트 전월세 MCP 서버

국토교통부 **아파트 전월세 실거래가** 공공데이터([공공데이터포털](https://www.data.go.kr), `RTMSDataSvcAptRent`)를
[MCP](https://modelcontextprotocol.io) 도구로 제공하는 [FastMCP](https://gofastmcp.com) 단일 파일 서버입니다.
Claude Code, Claude Desktop 등 MCP 클라이언트에 연결하면 "강남구 전세 시세 추이 알려줘" 같은 질문에 실거래 데이터로 답할 수 있습니다.

## 제공 도구

| 도구 | 설명 |
|---|---|
| `seoul_jeonse_by_district` | **서울 지역별 전세 시세.** 25개 구를 ㎡당 전세가 순으로 비교하고, 구를 지정하면 그 구의 동별로 비교 |
| `monthly_price_trend` | **월별 시세 추이.** 전세 중간값, ㎡당·평당 가격, 거래량, 월세 비율과 전월 대비 변화 |
| `renewal_increase` | **갱신 계약 인상률.** 종전 보증금 대비 인상률, 계약갱신청구권(5% 상한) 사용/미사용 비교, 구별 순위, 인상 폭이 큰 거래 |
| `wolse_ratio` | **월세화 비율.** 구·동별, 면적대별 월세 비중과 갱신 시 전세→월세 전환 비율 |
| `complex_price_table` | **단지별 시세표.** 아파트 이름(일부만 입력해도 됨)으로 찾아 면적별 전세·월세 최근가, 중간값, 최저·최고가, 최근 거래 내역 |

### 매개변수

| 이름 | 사용하는 도구 | 기본값 | 설명 |
|---|---|---|---|
| `district` | 전체 | 서울 전체 | 구 이름. `강남구`, `강남`, `서울 강남구` 모두 가능. 생략하거나 `서울`이면 25개 구 |
| `month` | 시세·인상률·월세화 | 지난달 | 계약 연월. `202610` 또는 `2026-10` |
| `end_month` | 추이·단지 | 지난달 | 조회 기간의 마지막 달 |
| `months` | 추이(1~24), 인상률·단지(1~12) | 12 / 1 / 6 | 조회할 개월 수 |
| `apartment` | `complex_price_table` | (필수) | 아파트 이름 또는 일부 (예: `헬리오`, `경희궁자이`) |

- **금액 단위**: API 원본 단위인 **만원**으로 계산하고, `6억 2,000만원`처럼 읽기 쉬운 표기를 함께 반환합니다.
- **기본 월이 지난달인 이유**: 이번 달은 아직 신고된 거래가 거의 없기 때문입니다.

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

1. [공공데이터포털](https://www.data.go.kr)에 회원가입하고 **국토교통부_아파트 전월세 실거래가 자료**를 활용신청합니다 (무료).
2. 마이페이지에서 **일반 인증키**를 확인합니다.
3. `.env.example` 을 `.env` 로 복사하고 키를 입력합니다.

```
DATA_GO_KR_API_KEY=발급받은_일반_인증키
```

서버는 자기 파일 옆의 `.env` 를 읽고, 같은 이름의 환경변수가 이미 있으면 그 값을 우선합니다.
`.env` 는 `.gitignore` 에 포함되어 있으니 **절대 커밋하지 마세요.**

## 실행

```bash
# stdio (MCP 클라이언트가 직접 실행하는 방식)
.venv/bin/python rent_mcp_server.py

# Streamable HTTP  →  http://127.0.0.1:8002/mcp
.venv/bin/python rent_mcp_server.py --http 8002
```

Windows 에서는 `.venv/bin/python` 대신 `.venv\Scripts\python.exe` 를 사용하세요.

## MCP 클라이언트에 연결

### Claude Code

```bash
claude mcp add seoul-rent --env PYTHONIOENCODING=utf-8 -- /절대/경로/seoul-rent-mcp/.venv/bin/python /절대/경로/seoul-rent-mcp/rent_mcp_server.py
```

또는 프로젝트 루트의 `.mcp.json` 에 추가합니다.

```json
{
  "mcpServers": {
    "seoul-rent": {
      "type": "stdio",
      "command": "/절대/경로/seoul-rent-mcp/.venv/bin/python",
      "args": ["/절대/경로/seoul-rent-mcp/rent_mcp_server.py"],
      "env": { "PYTHONIOENCODING": "utf-8" }
    }
  }
}
```

`.mcp.json` 으로 등록한 서버를 매번 묻지 않고 쓰려면 `.claude/settings.local.json` 에 승인 목록을 넣습니다.

```json
{ "enabledMcpjsonServers": ["seoul-rent"] }
```

새 세션을 열고 `/mcp` 를 입력해 `seoul-rent` 가 **connected** 인지 확인합니다.

### Claude Desktop

`claude_desktop_config.json` 에 추가합니다.

```json
{
  "mcpServers": {
    "seoul-rent": {
      "command": "C:\\경로\\seoul-rent-mcp\\.venv\\Scripts\\python.exe",
      "args": ["C:\\경로\\seoul-rent-mcp\\rent_mcp_server.py"],
      "env": { "PYTHONIOENCODING": "utf-8" }
    }
  }
}
```

Windows 에서는 한글 깨짐을 막기 위해 `PYTHONIOENCODING=utf-8` 을 넣는 것을 권장합니다.

## 사용 예시

연결한 뒤 Claude 에게 이렇게 물어보세요.

- "서울 구별 전세 시세 순위 보여줘" → `seoul_jeonse_by_district`
- "마포구 동별 전세 시세" → `seoul_jeonse_by_district` (`district=마포`)
- "강남구 최근 1년 전세 시세 추이" → `monthly_price_trend`
- "지난달 갱신 계약 인상률, 청구권 쓴 거랑 비교해줘" → `renewal_increase`
- "월세 비율 높은 구는 어디야?" → `wolse_ratio`
- "헬리오시티 면적별 전세 시세" → `complex_price_table`

### 응답 예시 (2026년 10월 실데이터, 요약)

`seoul_jeonse_by_district`

```json
{
  "month": "2026-10",
  "scope": "서울 전체",
  "overall": { "total_deals": 16329, "jeonse_count": 8207, "median_deposit": "6억 2,000만원", "median_per_pyeong_manwon": 2760.0 },
  "ranking": [
    { "rank": 1, "district": "강남구", "median_deposit": "9억 2,000만원", "median_per_m2_manwon": 1178.5, "median_per_pyeong_manwon": 3896.0 },
    { "rank": 2, "district": "서초구", "median_deposit": "8억 7,000만원", "median_per_m2_manwon": 1108.1, "median_per_pyeong_manwon": 3663.0 },
    { "rank": 3, "district": "마포구", "median_deposit": "7억 5,000만원", "median_per_m2_manwon": 1035.5, "median_per_pyeong_manwon": 3423.0 }
  ]
}
```

`renewal_increase`

```json
{
  "counts": { "renewals": 7611, "jeonse_to_jeonse": 4306, "jeonse_to_wolse": 469 },
  "jeonse_renewal": {
    "used_renewal_right":    { "count": 2387, "mean_increase_pct": 4.3,  "over_5pct_share": 0.0 },
    "without_renewal_right": { "count": 1919, "mean_increase_pct": 10.8, "over_5pct_share": 49.1 }
  }
}
```

## 지표 계산 방식

- **전세/월세 구분**: 월 임대료(`monthlyRent`)가 0이면 전세, 0보다 크면 월세(보증부 월세·반전세 포함)로 봅니다.
- **전세 시세**: 전세 보증금의 **중간값**을 씁니다. 평균은 고가 단지 몇 건에 크게 끌려가기 때문입니다.
- **㎡당·평당 가격**: 거래마다 보증금 ÷ 전용면적을 구한 뒤 중간값을 냅니다. 평당 = ㎡당 × 3.3058. 면적 차이를 보정하려고 **지역 순위는 ㎡당 가격 기준**입니다.
- **월별 변화율**: `per_m2_change_pct` 는 ㎡당 전세가의 전월 대비 변화, `deals_change_pct` 는 거래 건수의 전월 대비 변화입니다.
- **갱신 인상률**: `(이번 보증금 ÷ 종전 보증금 − 1) × 100`. 종전·이번 계약이 모두 전세인 갱신만 계산합니다.
  - `over_5pct_share`: 5% 넘게 오른 비율 · `frozen_share`: 동결 · `decreased_share`: 인하
  - 5% 상한에 맞춘 갱신이 많아 중간값은 대개 5.0%입니다. 그래서 **구별 순위는 평균 인상률과 5% 초과 비율로 정렬**합니다.
- **월세화 비율**: 전체 전월세 거래 중 월세 비중. 면적대는 40㎡ 이하 / 40~60 / 60~85 / 85~135 / 135㎡ 초과로 나눕니다.
- **전세→월세 전환율**: 종전 계약이 전세였던 갱신 계약 중 이번에 월세로 바뀐 비율입니다.
- **단지별 시세**: 단지 고유번호(`aptSeq`)로 묶고, 전용면적을 반올림해 면적별로 나눕니다.

## 동작 방식과 주의점

- **조회 단위**: API가 구 하나·한 달 단위로만 조회되므로, 서울 전체는 25개 구를 8개씩 병렬로 받습니다. 서울 전체 한 달 조회는 약 2초 걸립니다.
- **캐시**: 받은 데이터는 구·월별로 **6시간 메모리 캐시**되어, 같은 달을 다른 도구로 다시 조회하면 바로 끝납니다.
- **최근 데이터**: 실거래 신고 기한이 계약 후 30일이라 **최근 1~2개월 수치는 이후 신고분이 더해지며 바뀔 수 있습니다.** 모든 응답에 이 안내가 붙습니다.
- **한 달 변동**: 중간값은 그달 거래된 집의 구성(면적·단지)에 따라 흔들리므로, 한 달 수치보다 몇 달의 흐름을 보세요.
- **호출 수**: 서울 전체 12개월 추이는 25개 구 × 12개월 = 300회 호출합니다. 공공데이터포털 개발 계정의 일일 호출 한도를 고려하세요.
- **오류 안내**: 인증키 오류, 서울이 아닌 지역(예: 부산), 찾을 수 없는 아파트 이름은 이유를 설명하는 메시지로 반환합니다.

## 데이터 출처

모든 데이터는 국토교통부 아파트 전월세 실거래가 자료(공공데이터포털)에서 제공합니다.
