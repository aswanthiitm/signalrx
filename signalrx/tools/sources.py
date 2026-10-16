"""Connectors to public sources. Each returns plain dicts; agent/live.py turns
them into Sources / Evidence / Observations / Events in the ledger.

Parsers are pure functions over fetched text (unit-tested on fixtures); the
fetch wrappers are thin. All endpoints are public and need no paid key.

  universe     NSE index constituent files (niftyindices.com)
  screener     financial statements, quarterly results, peers, document links
  nse          corporate announcements (SEBI Reg 30 filings)
  openfda      drug recalls (enforcement) and approvals (drugsfda) by firm
  ctgov        ClinicalTrials.gov v2: sponsored trials by phase/year
  news         Google News RSS: headlines + first-coverage dates
"""
from __future__ import annotations

import csv
import io
import re
from datetime import datetime
from email.utils import parsedate_to_datetime
from urllib.parse import quote_plus

from bs4 import BeautifulSoup

from ..knowledge import load_pack
from .http import Fetcher

MONTHS = {m: i for i, m in enumerate(["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"], 1)}


# ------------------------------------------------------------------ universe

def parse_index_csv(text: str, index: str) -> list[dict]:
    rows = list(csv.DictReader(io.StringIO(text)))
    flt = set(load_pack()["universe"]["industry_filter"])
    out = []
    for r in rows:
        ind = (r.get("Industry") or "").strip()
        if index != "nifty50" or ind in flt:
            out.append({"symbol": r.get("Symbol", "").strip(), "name": r.get("Company Name", "").strip(),
                        "industry": ind, "index": index})
    return out


def track_universe(fetch: Fetcher | None = None) -> list[dict]:
    fetch = fetch or Fetcher()
    files = load_pack()["universe"]["index_files"]
    seen, out = set(), []
    for name, url in files.items():
        try:
            rows = parse_index_csv(fetch.get(url).text, name)
        except ConnectionError:
            continue
        for r in rows:
            if r["symbol"] and r["symbol"] not in seen:
                seen.add(r["symbol"])
                out.append(r)
    return out


# ------------------------------------------------------------------ screener

def screener_period(label: str, section: str) -> str | None:
    """'Jun 2025' in quarterly table -> 'Q1FY26'; 'Mar 2025' in annual table -> 'FY25'."""
    m = re.match(r"([A-Z][a-z]{2})\s+(\d{4})", label.strip())
    if not m:
        return None
    mon, yr = MONTHS[m.group(1)], int(m.group(2))
    fy = yr + 1 if mon >= 4 else yr
    if section == "quarters":
        q = {6: 1, 9: 2, 12: 3, 3: 4}.get(mon)
        return f"Q{q}FY{fy % 100:02d}" if q else None
    return f"FY{fy % 100:02d}" if mon == 3 else None


def _num(s: str) -> float | None:
    s = s.replace(",", "").replace("%", "").strip()
    try:
        return float(s)
    except ValueError:
        return None


def parse_screener_tables(html: str) -> dict:
    """Extract statement tables. Returns {section: {row_label: {period: value}}}, values in Rs crore."""
    soup = BeautifulSoup(html, "lxml")
    out: dict = {}
    for sec in ("quarters", "profit-loss", "balance-sheet", "cash-flow", "ratios"):
        node = soup.find("section", id=sec)
        table = node.find("table") if node else None
        if not table:
            continue
        heads = [th.get_text(" ", strip=True) for th in table.find("thead").find_all("th")][1:]
        rows = {}
        for tr in table.find("tbody").find_all("tr"):
            cells = tr.find_all("td")
            if not cells:
                continue
            label = re.sub(r"\s*\+\s*$", "", cells[0].get_text(" ", strip=True)).strip()
            vals = {}
            for h, td in zip(heads, cells[1:]):
                p = screener_period(h, "quarters" if sec == "quarters" else "annual")
                v = _num(td.get_text(strip=True))
                if p and v is not None:
                    vals[p] = v
            if vals:
                rows[label] = vals
        out[sec] = rows
    about = soup.select_one(".company-profile .about, .about")
    out["about"] = about.get_text(" ", strip=True) if about else ""
    info = soup.find(id="company-info")
    out["warehouse_id"] = info.get("data-warehouse-id") if info else None
    out["company_id"] = info.get("data-company-id") if info else None
    h1 = soup.find("h1")
    out["name"] = h1.get_text(strip=True) if h1 else ""
    docs = []
    dsec = soup.find("section", id="documents")
    if dsec:
        for a in dsec.find_all("a", href=True):
            t = a.get_text(" ", strip=True)
            kind = ("transcript" if re.search(r"transcript", t, re.I) else "presentation" if re.search(r"\bppt\b|presentation", t, re.I)
                    else "annual_report" if re.search(r"financial year|annual report", t, re.I) else
                    "announcement" if a.find_parent(class_=re.compile("announcement")) else "other")
            docs.append({"title": t, "url": a["href"], "kind": kind})
    out["documents"] = docs
    return out


def screener_company(symbol: str, fetch: Fetcher, consolidated: bool = True) -> dict:
    url = f"https://www.screener.in/company/{symbol}/" + ("consolidated/" if consolidated else "")
    f = fetch.get(url)
    data = parse_screener_tables(f.text)
    data["url"] = f.url
    data["retrieved_at"] = f.retrieved_at
    return data


def screener_search(q: str, fetch: Fetcher) -> list[dict]:
    res = fetch.json("https://www.screener.in/api/company/search/", params={"q": q, "v": 3})
    return [{"name": r.get("name"), "url": r.get("url"),
             "symbol": (re.search(r"/company/([^/]+)/", r.get("url", "")) or [None, None])[1]} for r in res]


def parse_screener_peers(html: str) -> list[str]:
    soup = BeautifulSoup(html, "lxml")
    names = []
    for a in soup.select("table a[href*='/company/']"):
        names.append(a.get_text(strip=True))
    return names


def screener_peers(warehouse_id: str, fetch: Fetcher) -> list[str]:
    f = fetch.get(f"https://www.screener.in/api/company/{warehouse_id}/peers/")
    return parse_screener_peers(f.text)


# ---------------------------------------------------------------------- nse

def nse_announcements(symbol: str, fetch: Fetcher) -> list[dict]:
    """SEBI Reg-30 announcements. NSE needs a session cookie from the home page first."""
    fetch.get("https://www.nseindia.com/", use_cache=False)
    data = fetch.json("https://www.nseindia.com/api/corporate-announcements",
                      params={"index": "equities", "symbol": symbol},
                      headers={"Referer": "https://www.nseindia.com/companies-listing/corporate-filings-announcements"})
    return parse_nse_announcements(data)


def parse_nse_announcements(data) -> list[dict]:
    out = []
    for r in data or []:
        dt = r.get("an_dt") or r.get("sort_date") or ""
        try:
            d = datetime.strptime(dt[:20].strip(), "%d-%b-%Y %H:%M:%S").date().isoformat()
        except ValueError:
            d = dt[:10]
        out.append({"date": d, "subject": r.get("desc", ""), "text": r.get("attchmntText", ""),
                    "url": r.get("attchmntFile", ""), "symbol": r.get("symbol")})
    return out


# ------------------------------------------------------------------ openFDA

def openfda_recalls(firm: str, fetch: Fetcher, limit: int = 100) -> list[dict]:
    q = f'recalling_firm:"{firm}"'
    try:
        data = fetch.json("https://api.fda.gov/drug/enforcement.json", params={"search": q, "limit": limit,
                                                                              "sort": "recall_initiation_date:desc"})
    except ConnectionError:
        return []
    return parse_openfda_recalls(data)


def parse_openfda_recalls(data: dict) -> list[dict]:
    out = []
    for r in data.get("results", []):
        d = r.get("recall_initiation_date", "")
        out.append({"date": f"{d[:4]}-{d[4:6]}-{d[6:8]}" if len(d) == 8 else d, "classification": r.get("classification"),
                    "product": (r.get("product_description") or "")[:160], "reason": r.get("reason_for_recall"),
                    "firm": r.get("recalling_firm"), "status": r.get("status"), "recall_number": r.get("recall_number")})
    return out


def openfda_approvals(sponsor: str, fetch: Fetcher) -> list[dict]:
    try:
        data = fetch.json("https://api.fda.gov/drug/drugsfda.json", params={"search": f'sponsor_name:"{sponsor}"', "limit": 100})
    except ConnectionError:
        return []
    return parse_openfda_approvals(data)


def parse_openfda_approvals(data: dict) -> list[dict]:
    out = []
    for app in data.get("results", []):
        for s in app.get("submissions", []):
            if s.get("submission_type") == "ORIG" and s.get("submission_status") == "AP":
                d = s.get("submission_status_date", "")
                out.append({"application": app.get("application_number"), "date": f"{d[:4]}-{d[4:6]}-{d[6:8]}",
                            "products": [p.get("brand_name") for p in app.get("products", [])][:3]})
    return sorted(out, key=lambda x: x["date"])


def approvals_by_year(approvals: list[dict]) -> dict[int, int]:
    out: dict[int, int] = {}
    for a in approvals:
        y = int(a["date"][:4])
        out[y] = out.get(y, 0) + 1
    return dict(sorted(out.items()))


# ------------------------------------------------------------------- ctgov

def ctgov_trials(sponsor: str, fetch: Fetcher) -> list[dict]:
    try:
        data = fetch.json("https://clinicaltrials.gov/api/v2/studies",
                          params={"query.spons": sponsor, "pageSize": 200,
                                  "fields": "NCTId,BriefTitle,Phase,StartDate,OverallStatus,LeadSponsorName"})
    except ConnectionError:
        return []
    out = []
    for s in data.get("studies", []):
        ps = s.get("protocolSection", {})
        ident, status, design = ps.get("identificationModule", {}), ps.get("statusModule", {}), ps.get("designModule", {})
        out.append({"nct": ident.get("nctId"), "title": ident.get("briefTitle"), "phases": design.get("phases", []),
                    "start": (status.get("startDateStruct") or {}).get("date"), "status": status.get("overallStatus")})
    return out


# -------------------------------------------------------------------- news

def news_rss(query: str, fetch: Fetcher) -> list[dict]:
    url = f"https://news.google.com/rss/search?q={quote_plus(query)}&hl=en-IN&gl=IN&ceid=IN:en"
    try:
        return parse_rss(fetch.get(url).text)
    except ConnectionError:
        return []


def parse_rss(xml: str) -> list[dict]:
    soup = BeautifulSoup(xml, "xml")
    out = []
    for it in soup.find_all("item"):
        pub = it.pubDate.get_text() if it.pubDate else ""
        try:
            d = parsedate_to_datetime(pub).date().isoformat()
        except (TypeError, ValueError):
            d = None
        src = it.find("source")
        out.append({"title": it.title.get_text() if it.title else "", "url": it.link.get_text() if it.link else "",
                    "date": d, "publisher": src.get_text() if src else ""})
    return sorted(out, key=lambda x: x["date"] or "")


def first_coverage(items: list[dict], must: list[str]) -> dict | None:
    """Earliest headline containing all of `must` (case-insensitive): the 'mainstream coverage' date."""
    for it in items:
        t = it["title"].lower()
        if all(m.lower() in t for m in must):
            return it
    return None
