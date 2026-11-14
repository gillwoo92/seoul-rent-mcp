"""
서울 아파트 전월세 실거래가 MCP 서버 (FastMCP 단일 파일)

데이터: 국토교통부 아파트 전월세 실거래가 (공공데이터포털 RTMSDataSvcAptRent)

도구
- seoul_jeonse_by_district : 서울 지역별 전세 시세 (구별, 또는 한 구의 동별)
- monthly_price_trend      : 월별 시세 추이 (전세가, ㎡당 가격, 거래량, 월세 비율)
- renewal_increase         : 갱신 계약 인상률 (계약갱신청구권 사용/미사용 비교)
- wolse_ratio              : 월세화 비율 (지역별 월세 비중, 전세→월세 전환)
- complex_price_table      : 단지별 시세표 (면적별 최근·중간 거래가)

API 키
    같은 폴더의 .env 파일(또는 환경변수)에서 DATA_GO_KR_API_KEY 를 읽습니다.

실행
    .venv\\Scripts\\python rent_mcp_server.py               # stdio
    .venv\\Scripts\\python rent_mcp_server.py --http 8002   # http://127.0.0.1:8002/mcp

금액 단위는 모두 만원입니다 (API 원본 단위).
"""

from __future__ import annotations

import os
import statistics
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from pathlib import Path
from typing import Any, Callable, Iterable

from fastmcp import FastMCP

API_URL = "https://apis.data.go.kr/1613000/RTMSDataSvcAptRent/getRTMSDataSvcAptRent"
TIMEOUT = 20
PAGE_SIZE = 1000
CACHE_TTL = 6 * 60 * 60
MAX_WORKERS = 8
PYEONG = 3.3058  # 1평 = 3.3058㎡

SEOUL_GU: dict[str, str] = {
    "종로구": "11110", "중구": "11140", "용산구": "11170", "성동구": "11200", "광진구": "11215",
    "동대문구": "11230", "중랑구": "11260", "성북구": "11290", "강북구": "11305", "도봉구": "11320",
    "노원구": "11350", "은평구": "11380", "서대문구": "11410", "마포구": "11440", "양천구": "11470",
    "강서구": "11500", "구로구": "11530", "금천구": "11545", "영등포구": "11560", "동작구": "11590",
    "관악구": "11620", "서초구": "11650", "강남구": "11680", "송파구": "11710", "강동구": "11740",
}
_CODE_TO_GU = {v: k for k, v in SEOUL_GU.items()}

mcp = FastMCP(
    "seoul-rent",
    instructions=(
        "국토교통부 아파트 전월세 실거래가로 서울 25개 구의 전세 시세, 월별 추이, 갱신 인상률, "
        "월세화 비율, 단지별 시세표를 조회합니다. 지역은 '강남구' 또는 '강남'처럼 구 이름으로, "
        "월은 YYYYMM 또는 YYYY-MM 으로 입력합니다. 금액 단위는 만원입니다. "
        "실거래 신고 기한(계약 후 30일) 때문에 최근 1~2개월은 건수가 계속 늘어날 수 있습니다."
    ),
)


# ---------------------------------------------------------------------------
# 설정 / 공통 헬퍼
# ---------------------------------------------------------------------------
class ApiError(Exception):
    pass


def _load_dotenv() -> None:
    env_path = Path(__file__).resolve().parent / ".env"
    if not env_path.exists():
        return
    for raw in env_path.read_text(encoding="utf-8-sig").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        k, v = k.strip(), v.strip().strip('"').strip("'")
        if k and k not in os.environ:
            os.environ[k] = v


_load_dotenv()


def _api_key() -> str:
    key = os.environ.get("DATA_GO_KR_API_KEY", "").strip()
    if not key:
        raise ApiError("DATA_GO_KR_API_KEY 가 설정되지 않았습니다. .env 파일에 DATA_GO_KR_API_KEY=인증키 를 추가하세요.")
    return key


def _resolve_gu(name: str) -> str:
    """'강남구' / '강남' / '11680' -> '강남구'"""
    t = name.strip().replace(" ", "")
    if t.startswith("서울"):
        t = t.removeprefix("서울특별시").removeprefix("서울시").removeprefix("서울")
    if t in _CODE_TO_GU:
        return _CODE_TO_GU[t]
    if t in SEOUL_GU:
        return t
    if t + "구" in SEOUL_GU:
        return t + "구"
    raise ValueError(f"'{name}' 은(는) 서울 25개 구 중 하나가 아닙니다. 예: 강남구, 마포, 송파구")


def _is_all_seoul(name: str | None) -> bool:
    return name is None or name.strip() in ("", "서울", "서울시", "서울특별시", "전체", "서울 전체")


def _target_gus(district: str | None) -> list[str]:
    return list(SEOUL_GU) if _is_all_seoul(district) else [_resolve_gu(district)]  # type: ignore[arg-type]


def _default_month() -> str:
    """지난달 (이번 달은 아직 신고가 거의 들어오지 않음)."""
    today = date.today()
    y, m = (today.year, today.month - 1) if today.month > 1 else (today.year - 1, 12)
    return f"{y}{m:02d}"


def _parse_month(text: str | None) -> str:
    if not text:
        return _default_month()
    t = text.strip().replace("-", "").replace(".", "").replace("/", "")
    if len(t) != 6 or not t.isdigit() or not 1 <= int(t[4:]) <= 12:
        raise ValueError(f"월 형식이 올바르지 않습니다: '{text}' (예: 202610 또는 2026-10)")
    return t


def _month_shift(ym: str, delta: int) -> str:
    y, m = int(ym[:4]), int(ym[4:])
    idx = y * 12 + (m - 1) + delta
    return f"{idx // 12}{idx % 12 + 1:02d}"


def _fmt_month(ym: str) -> str:
    return f"{ym[:4]}-{ym[4:]}"


def _num(v: str | None) -> float | None:
    if v is None:
        return None
    t = v.strip().replace(",", "")
    if not t:
        return None
    try:
        return float(t)
    except ValueError:
        return None


def _won(manwon: float | None) -> str | None:
    """만원 -> '7억 9,000만원'"""
    if manwon is None:
        return None
    v = int(round(manwon))
    eok, rest = divmod(v, 10000)
    if eok and rest:
        return f"{eok}억 {rest:,}만원"
    if eok:
        return f"{eok}억원"
    return f"{rest:,}만원"


def _median(xs: Iterable[float]) -> float | None:
    xs = list(xs)
    return statistics.median(xs) if xs else None


def _r(x: float | None, nd: int = 1) -> float | None:
    return round(x, nd) if x is not None else None


def _pct(part: int, whole: int) -> float | None:
    return round(part / whole * 100, 1) if whole else None


def _area_band(area: float) -> str:
    if area <= 40:
        return "초소형(40㎡ 이하)"
    if area <= 60:
        return "소형(40~60㎡)"
    if area <= 85:
        return "중형(60~85㎡)"
    if area <= 135:
        return "중대형(85~135㎡)"
    return "대형(135㎡ 초과)"


# ---------------------------------------------------------------------------
# API 조회 (구 x 월 단위, 캐시)
# ---------------------------------------------------------------------------
_cache: dict[tuple[str, str], tuple[float, list[dict[str, Any]]]] = {}
_cache_lock = threading.Lock()


def _request(lawd_cd: str, ym: str, page: int) -> ET.Element:
    params = {"serviceKey": _api_key(), "LAWD_CD": lawd_cd, "DEAL_YMD": ym,
              "pageNo": page, "numOfRows": PAGE_SIZE}
    req = urllib.request.Request(f"{API_URL}?{urllib.parse.urlencode(params)}",
                                 headers={"User-Agent": "seoul-rent-mcp/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            body = resp.read()
    except urllib.error.HTTPError as e:
        raise ApiError(f"공공데이터포털 요청 실패 (HTTP {e.code}). 인증키 또는 활용신청 상태를 확인하세요.") from e
    except (urllib.error.URLError, TimeoutError) as e:
        raise ApiError(f"공공데이터포털 연결 실패: {e}") from e
    try:
        root = ET.fromstring(body)
    except ET.ParseError as e:
        raise ApiError(f"응답을 해석하지 못했습니다: {body[:200]!r}") from e
    # 인증 오류 등은 OpenAPI_ServiceResponse 형식으로 옴
    auth_msg = root.findtext(".//returnAuthMsg")
    if auth_msg:
        raise ApiError(f"공공데이터포털 오류: {auth_msg} ({root.findtext('.//returnReasonCode')})")
    code = (root.findtext(".//resultCode") or "").strip()
    if code not in ("000", "00"):
        raise ApiError(f"공공데이터포털 오류 {code}: {root.findtext('.//resultMsg')}")
    return root


def _parse_item(it: ET.Element, gu: str) -> dict[str, Any]:
    g = lambda tag: (it.findtext(tag) or "").strip()  # noqa: E731
    deposit = _num(g("deposit")) or 0.0
    rent = _num(g("monthlyRent")) or 0.0
    area = _num(g("excluUseAr")) or 0.0
    y, m, d = g("dealYear"), g("dealMonth"), g("dealDay")
    return {
        "apt": g("aptNm"),
        "apt_seq": g("aptSeq"),
        "gu": gu,
        "dong": g("umdNm"),
        "jibun": g("jibun"),
        "road": g("roadnm") or None,
        "build_year": int(_num(g("buildYear")) or 0) or None,
        "area": area,
        "floor": int(_num(g("floor")) or 0),
        "date": f"{y}-{int(m):02d}-{int(d):02d}" if y and m and d else None,
        "deposit": deposit,
        "monthly_rent": rent,
        "type": "전세" if rent == 0 else "월세",
        "contract_type": g("contractType") or None,  # 신규 / 갱신
        "contract_term": g("contractTerm") or None,
        "used_renewal_right": g("useRRRight") == "사용",
        "pre_deposit": _num(g("preDeposit")),
        "pre_monthly_rent": _num(g("preMonthlyRent")),
    }


def _fetch(gu: str, ym: str) -> list[dict[str, Any]]:
    key = (gu, ym)
    with _cache_lock:
        hit = _cache.get(key)
        if hit and time.time() - hit[0] < CACHE_TTL:
            return hit[1]
    lawd = SEOUL_GU[gu]
    root = _request(lawd, ym, 1)
    total = int(_num(root.findtext(".//totalCount")) or 0)
    items = [_parse_item(it, gu) for it in root.findall(".//item")]
    page = 1
    while len(items) < total:
        page += 1
        more = _request(lawd, ym, page).findall(".//item")
        if not more:
            break
        items.extend(_parse_item(it, gu) for it in more)
    with _cache_lock:
        _cache[key] = (time.time(), items)
    return items


def _fetch_many(gus: list[str], months: list[str]) -> dict[tuple[str, str], list[dict[str, Any]]]:
    jobs = [(g, m) for g in gus for m in months]
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        results = list(pool.map(lambda j: _fetch(*j), jobs))
    return dict(zip(jobs, results))


def _jeonse_stats(deals: list[dict[str, Any]]) -> dict[str, Any]:
    js = [d for d in deals if d["type"] == "전세" and d["area"] > 0]
    med = _median(d["deposit"] for d in js)
    per_m2 = _median(d["deposit"] / d["area"] for d in js)
    return {
        "jeonse_count": len(js),
        "median_deposit_manwon": _r(med, 0),
        "median_deposit": _won(med),
        "median_per_m2_manwon": _r(per_m2),
        "median_per_pyeong_manwon": _r(per_m2 * PYEONG if per_m2 else None, 0),
    }


def _group(deals: Iterable[dict[str, Any]], key: Callable[[dict[str, Any]], str]) -> dict[str, list[dict[str, Any]]]:
    out: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for d in deals:
        out[key(d)].append(d)
    return out


NOTE_RECENT = "실거래 신고 기한(계약 후 30일) 때문에 최근 1~2개월 수치는 이후 신고분이 더해지며 바뀔 수 있습니다."


# ---------------------------------------------------------------------------
# 도구
# ---------------------------------------------------------------------------
@mcp.tool
def seoul_jeonse_by_district(month: str | None = None, district: str | None = None) -> dict[str, Any]:
    """서울 지역별 전세 시세. 구를 생략하면 서울 25개 구를 ㎡당 전세가 순으로, 구를 지정하면 그 구의 동별 시세를 반환합니다.

    Args:
        month: 계약 연월 (YYYYMM 또는 YYYY-MM). 생략하면 지난달.
        district: 구 이름 (예: 강남구, 마포). 생략하면 서울 전체 구별 비교.
    """
    ym = _parse_month(month)
    gus = _target_gus(district)
    data = _fetch_many(gus, [ym])
    deals = [d for g in gus for d in data[(g, ym)]]

    if len(gus) > 1:
        rows = [{"district": g, **_jeonse_stats(data[(g, ym)])} for g in gus]
        level = "구"
    else:
        rows = [{"dong": k, **_jeonse_stats(v)} for k, v in _group(deals, lambda d: d["dong"]).items()]
        level = f"{gus[0]} 동"
    rows = [r for r in rows if r["jeonse_count"]]
    rows.sort(key=lambda r: r["median_per_m2_manwon"] or 0, reverse=True)
    for i, r in enumerate(rows, 1):
        r["rank"] = i

    return {
        "month": _fmt_month(ym),
        "scope": "서울 전체" if len(gus) > 1 else gus[0],
        "level": level,
        "overall": {"total_deals": len(deals), **_jeonse_stats(deals)},
        "ranking": rows,
        "notes": [
            "순위 기준은 ㎡당 전세 보증금 중간값입니다(면적 차이를 보정). 평당 = ㎡당 × 3.3058.",
            "월세(monthlyRent > 0) 거래는 제외했습니다.",
            NOTE_RECENT,
        ],
    }


@mcp.tool
def monthly_price_trend(district: str = "서울", months: int = 12, end_month: str | None = None) -> dict[str, Any]:
    """월별 시세 추이. 월마다 전세 보증금 중간값, ㎡당 전세가, 전체 거래량, 월세 비율과 전월 대비 변화를 반환합니다.

    Args:
        district: 구 이름 (예: 송파구) 또는 '서울'(25개 구 합산, 기본).
        months: 조회할 개월 수 (1~24, 기본 12).
        end_month: 마지막 월 (YYYYMM). 생략하면 지난달.
    """
    months = max(1, min(int(months), 24))
    end = _parse_month(end_month)
    yms = [_month_shift(end, -i) for i in range(months - 1, -1, -1)]
    gus = _target_gus(district)
    data = _fetch_many(gus, yms)

    series = []
    prev: dict[str, Any] | None = None
    for ym in yms:
        deals = [d for g in gus for d in data[(g, ym)]]
        st = _jeonse_stats(deals)
        wolse = sum(1 for d in deals if d["type"] == "월세")
        row = {
            "month": _fmt_month(ym),
            "total_deals": len(deals),
            **st,
            "wolse_ratio_pct": _pct(wolse, len(deals)),
        }
        if prev and prev["median_per_m2_manwon"] and st["median_per_m2_manwon"]:
            row["per_m2_change_pct"] = _r((st["median_per_m2_manwon"] / prev["median_per_m2_manwon"] - 1) * 100)
        if prev and prev["total_deals"]:
            row["deals_change_pct"] = _r((len(deals) / prev["total_deals"] - 1) * 100)
        series.append(row)
        prev = row

    first = next((r for r in series if r["median_per_m2_manwon"]), None)
    last = next((r for r in reversed(series) if r["median_per_m2_manwon"]), None)
    summary: dict[str, Any] = {}
    if first and last and first is not last:
        summary = {
            "period": f"{first['month']} ~ {last['month']}",
            "per_m2_change_pct": _r((last["median_per_m2_manwon"] / first["median_per_m2_manwon"] - 1) * 100),
            "wolse_ratio_change_pt": _r((last["wolse_ratio_pct"] or 0) - (first["wolse_ratio_pct"] or 0)),
        }
    return {
        "scope": "서울 전체" if len(gus) > 1 else gus[0],
        "series": series,
        "summary": summary,
        "notes": [
            "*_change_pct 는 전월 대비 변화율입니다.",
            "중간값은 그 달 거래된 집의 구성(면적·연식)에 따라 흔들리므로 ㎡당 가격을 함께 보세요.",
            NOTE_RECENT,
        ],
    }


@mcp.tool
def renewal_increase(month: str | None = None, district: str | None = None, months: int = 1) -> dict[str, Any]:
    """갱신 계약 인상률. 갱신 계약의 종전 보증금 대비 이번 보증금 인상률을 계산하고,
    계약갱신청구권(인상 상한 5%) 사용 계약과 미사용 계약을 비교합니다. 전세→전세 갱신만 인상률을 계산합니다.

    Args:
        month: 마지막 계약 연월 (YYYYMM). 생략하면 지난달.
        district: 구 이름. 생략하면 서울 전체(구별 비교 포함).
        months: 합산할 개월 수 (1~12, 기본 1). 표본을 늘리고 싶을 때 키우세요.
    """
    months = max(1, min(int(months), 12))
    end = _parse_month(month)
    yms = [_month_shift(end, -i) for i in range(months - 1, -1, -1)]
    gus = _target_gus(district)
    data = _fetch_many(gus, yms)
    deals = [d for g in gus for ym in yms for d in data[(g, ym)]]

    renewals = [d for d in deals if d["contract_type"] == "갱신"]

    def is_jj(d: dict[str, Any]) -> bool:  # 전세 -> 전세
        return d["type"] == "전세" and (d["pre_monthly_rent"] or 0) == 0 and (d["pre_deposit"] or 0) > 0

    def rate(d: dict[str, Any]) -> float:
        return (d["deposit"] / d["pre_deposit"] - 1) * 100

    def stats(ds: list[dict[str, Any]]) -> dict[str, Any]:
        rs = [rate(d) for d in ds]
        if not rs:
            return {"count": 0}
        return {
            "count": len(rs),
            "median_increase_pct": _r(_median(rs)),
            "mean_increase_pct": _r(statistics.fmean(rs)),
            "over_5pct_share": _pct(sum(1 for r in rs if r > 5.05), len(rs)),
            "frozen_share": _pct(sum(1 for r in rs if abs(r) < 0.05), len(rs)),
            "decreased_share": _pct(sum(1 for r in rs if r < -0.05), len(rs)),
            "median_increase_manwon": _r(_median(d["deposit"] - d["pre_deposit"] for d in ds), 0),
        }

    jj = [d for d in renewals if is_jj(d)]
    switched = [d for d in renewals if (d["pre_monthly_rent"] or 0) == 0 and d["type"] == "월세"
                and (d["pre_deposit"] or 0) > 0]

    result: dict[str, Any] = {
        "period": f"{_fmt_month(yms[0])} ~ {_fmt_month(yms[-1])}" if months > 1 else _fmt_month(end),
        "scope": "서울 전체" if len(gus) > 1 else gus[0],
        "counts": {
            "all_deals": len(deals),
            "renewals": len(renewals),
            "renewal_share_pct": _pct(len(renewals), len(deals)),
            "jeonse_to_jeonse": len(jj),
            "jeonse_to_wolse": len(switched),
        },
        "jeonse_renewal": {
            "all": stats(jj),
            "used_renewal_right": stats([d for d in jj if d["used_renewal_right"]]),
            "without_renewal_right": stats([d for d in jj if not d["used_renewal_right"]]),
        },
    }
    if len(gus) > 1:
        by_gu = []
        for g in gus:
            s = stats([d for d in jj if d["gu"] == g])
            if s["count"]:
                by_gu.append({"district": g, **s})
        # 중간값은 5% 상한에 몰려 구별 차이가 거의 없으므로 평균·5% 초과 비율로 정렬
        by_gu.sort(key=lambda r: (r["mean_increase_pct"], r["over_5pct_share"]), reverse=True)
        result["by_district"] = by_gu
    top = sorted(jj, key=rate, reverse=True)[:5]
    result["largest_increases"] = [{
        "apt": d["apt"], "district": d["gu"], "dong": d["dong"], "area_m2": d["area"], "date": d["date"],
        "pre_deposit": _won(d["pre_deposit"]), "deposit": _won(d["deposit"]), "increase_pct": _r(rate(d)),
        "used_renewal_right": d["used_renewal_right"],
    } for d in top]
    result["notes"] = [
        "인상률 = (이번 보증금 / 종전 보증금 − 1) × 100, 전세에서 전세로 갱신한 계약만 계산합니다.",
        "계약갱신청구권을 사용하면 인상률이 5% 이내로 제한됩니다(over_5pct_share 참고).",
        "frozen_share = 동결, decreased_share = 인하된 계약 비율입니다.",
        "5% 상한에 맞춘 갱신이 많아 중간값은 대개 5.0%입니다. 지역 차이는 평균과 over_5pct_share 로 보세요.",
    ]
    return result


@mcp.tool
def wolse_ratio(month: str | None = None, district: str | None = None) -> dict[str, Any]:
    """월세화 비율. 전체 전월세 거래 중 월세 비중을 지역별·면적대별로 계산하고,
    갱신 계약 중 전세에서 월세로 바뀐 비율도 함께 반환합니다.

    Args:
        month: 계약 연월 (YYYYMM). 생략하면 지난달.
        district: 구 이름. 생략하면 서울 25개 구 비교, 지정하면 그 구의 동별 비교.
    """
    ym = _parse_month(month)
    gus = _target_gus(district)
    data = _fetch_many(gus, [ym])
    deals = [d for g in gus for d in data[(g, ym)]]

    def ratio(ds: list[dict[str, Any]]) -> dict[str, Any]:
        w = [d for d in ds if d["type"] == "월세"]
        return {
            "deals": len(ds),
            "wolse": len(w),
            "jeonse": len(ds) - len(w),
            "wolse_ratio_pct": _pct(len(w), len(ds)),
            "median_monthly_rent_manwon": _r(_median(d["monthly_rent"] for d in w), 0),
            "median_wolse_deposit_manwon": _r(_median(d["deposit"] for d in w), 0),
        }

    key = (lambda d: d["gu"]) if len(gus) > 1 else (lambda d: d["dong"])
    label = "district" if len(gus) > 1 else "dong"
    rows = [{label: k, **ratio(v)} for k, v in _group(deals, key).items() if len(v) >= 1]
    rows.sort(key=lambda r: r["wolse_ratio_pct"] or 0, reverse=True)

    by_area = [{"area_band": k, **ratio(v)} for k, v in sorted(
        _group(deals, lambda d: _area_band(d["area"])).items(), key=lambda kv: min(d["area"] for d in kv[1]))]

    renew_from_jeonse = [d for d in deals if d["contract_type"] == "갱신" and (d["pre_deposit"] or 0) > 0
                         and (d["pre_monthly_rent"] or 0) == 0]
    switched = [d for d in renew_from_jeonse if d["type"] == "월세"]

    return {
        "month": _fmt_month(ym),
        "scope": "서울 전체" if len(gus) > 1 else gus[0],
        "overall": ratio(deals),
        "by_region": rows,
        "by_area": by_area,
        "jeonse_to_wolse_on_renewal": {
            "renewals_from_jeonse": len(renew_from_jeonse),
            "switched_to_wolse": len(switched),
            "switch_rate_pct": _pct(len(switched), len(renew_from_jeonse)),
        },
        "notes": [
            "월세 = 월 임대료가 0보다 큰 계약(보증부 월세, 반전세 포함)입니다.",
            "jeonse_to_wolse_on_renewal 은 종전 계약이 전세였던 갱신 계약 중 월세로 바뀐 비율입니다.",
            NOTE_RECENT,
        ],
    }


@mcp.tool
def complex_price_table(apartment: str, district: str | None = None, months: int = 6,
                        end_month: str | None = None) -> dict[str, Any]:
    """단지별 시세표. 아파트 이름(일부만 입력해도 됨)으로 단지를 찾아 면적별 전세·월세 최근 거래가와 중간값,
    최근 거래 내역을 반환합니다.

    Args:
        apartment: 아파트 이름 또는 일부 (예: 헬리오시티, 경희궁자이).
        district: 구 이름. 지정하면 빠릅니다. 생략하면 서울 25개 구 전체에서 찾습니다.
        months: 조회할 개월 수 (1~12, 기본 6).
        end_month: 마지막 월 (YYYYMM). 생략하면 지난달.
    """
    q = apartment.strip().replace(" ", "")
    if not q:
        raise ValueError("아파트 이름을 입력하세요.")
    months = max(1, min(int(months), 12))
    end = _parse_month(end_month)
    yms = [_month_shift(end, -i) for i in range(months - 1, -1, -1)]
    gus = _target_gus(district)
    data = _fetch_many(gus, yms)
    hits = [d for v in data.values() for d in v if q in d["apt"].replace(" ", "")]
    if not hits:
        where = "서울 전체" if len(gus) > 1 else gus[0]
        raise ValueError(f"{where}에서 {_fmt_month(yms[0])} ~ {_fmt_month(yms[-1])} 사이 '{apartment}' 거래를 찾지 못했습니다. "
                         "이름 일부만 입력하거나 months 를 늘려 보세요.")

    complexes = []
    for seq, ds in _group(hits, lambda d: d["apt_seq"] or d["apt"]).items():
        ds.sort(key=lambda d: d["date"] or "", reverse=True)
        first = ds[0]
        areas = []
        for area, ads in sorted(_group(ds, lambda d: f"{round(d['area'])}").items(), key=lambda kv: int(kv[0])):
            js = [d for d in ads if d["type"] == "전세"]
            ws = [d for d in ads if d["type"] == "월세"]
            row: dict[str, Any] = {"area_m2": int(area), "area_pyeong": _r(int(area) / PYEONG), "deals": len(ads)}
            if js:
                row["jeonse"] = {
                    "count": len(js),
                    "latest": f"{_won(js[0]['deposit'])} ({js[0]['date']}, {js[0]['floor']}층)",
                    "median": _won(_median(d["deposit"] for d in js)),
                    "min": _won(min(d["deposit"] for d in js)),
                    "max": _won(max(d["deposit"] for d in js)),
                }
            if ws:
                row["wolse"] = {
                    "count": len(ws),
                    "latest": f"보증금 {_won(ws[0]['deposit'])} / 월 {int(ws[0]['monthly_rent'])}만원 "
                              f"({ws[0]['date']}, {ws[0]['floor']}층)",
                    "median_monthly_rent_manwon": _r(_median(d["monthly_rent"] for d in ws), 0),
                }
            areas.append(row)
        complexes.append({
            "apt": first["apt"],
            "district": first["gu"],
            "dong": first["dong"],
            "address": first["road"] or f"{first['dong']} {first['jibun']}",
            "build_year": first["build_year"],
            "deals": len(ds),
            "by_area": areas,
            "recent_deals": [{
                "date": d["date"], "area_m2": d["area"], "floor": d["floor"], "type": d["type"],
                "deposit": _won(d["deposit"]), "monthly_rent_manwon": int(d["monthly_rent"]),
                "contract": d["contract_type"],
            } for d in ds[:10]],
        })
    complexes.sort(key=lambda c: c["deals"], reverse=True)
    return {
        "query": apartment,
        "period": f"{_fmt_month(yms[0])} ~ {_fmt_month(yms[-1])}",
        "matched_complexes": len(complexes),
        "complexes": complexes[:10],
        "notes": ["면적은 전용면적을 반올림해 묶었습니다.", NOTE_RECENT],
    }


if __name__ == "__main__":
    if len(sys.argv) >= 2 and sys.argv[1] == "--http":
        port = int(sys.argv[2]) if len(sys.argv) >= 3 else 8002
        mcp.run(transport="http", host="127.0.0.1", port=port)
    else:
        mcp.run()
