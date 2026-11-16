/* 스톡 피치 리그 — 평가 기준일 종가 기준 최종 평가표 (인쇄용) */
(() => {
  const { esc, pct, pp, dir, price, md, mdK, kst, num } = SP;
  const $ = (s) => document.querySelector(s);
  const span = (v, t) => `<span class="${dir(v)}">${t}</span>`;
  let F = null;

  function render() {
    const f = F, bms = f.benchmarks || [], rows = f.pitches || [];
    const spy = bms.find((b) => b.kind === "etf"), bh = bms.find((b) => b.id === "p_bh");
    $("#f-title").textContent = "최종 평가표";
    $("#f-sub").textContent = `${mdK(f.baseDate)} 종가 대비 ${mdK(f.finalDate)} 종가, 원화 기준 누적 총수익률 (배당 포함)`;

    const label = { final: "확정", provisional: "잠정", pending: "진행 중" }[f.status];
    const text = {
      final: `${mdK(f.finalDate)} 한국·미국 장 마감 종가로 확정한 결과이며 이후 바뀌지 않습니다. 평가일 이후의 수익률은 「실시간 현황」 탭에서 볼 수 있습니다. 계산 시각 ${kst(f.computedAt)} (한국 시각).` + ((f.missing || []).length ? ` 다음 종목은 평가일 종가를 받지 못해 직전 종가로 계산했으니 확인이 필요합니다: ${esc(f.missing.join(", "))}.` : ""),
      provisional: `평가일 종가를 집계하는 중입니다. 한국·미국 장 마감 가격이 모두 반영되면 확정됩니다. 아래는 ${kst(f.computedAt)} 현재 잠정 결과입니다.` + ((f.missing || []).length ? ` 아직 평가일 종가가 들어오지 않은 종목: ${esc(f.missing.join(", "))}.` : ""),
      pending: `아직 평가일(${mdK(f.finalDate)}) 전입니다. 아래는 ${mdK(f.asOfDate)} 가격 기준 현재 순위이며 최종 결과가 아닙니다. 평가일 종가가 확정되면 이 표는 고정됩니다.`,
    }[f.status];
    $("#f-banner").innerHTML = (f.demo ? "" : "") +
      `<div class="banner ${f.status === "final" ? "info" : ""}"><span class="stamp">${label}</span>${text}</div>`;

    $("#f-table").innerHTML = `<thead><tr>
      <th class="l">순위</th><th class="l">추천자</th><th class="l">종목</th><th class="l">티커</th><th class="l">시장</th>
      <th>기준가<br>(${md(f.baseDate)})</th><th>평가가</th><th>가격일</th><th>주가<br>수익률</th><th>배당<br>기여</th>
      <th>현지통화<br>총수익률</th><th>환율<br>효과</th><th>원화 누적<br>수익률</th>
      <th>S&amp;P 500<br>대비</th><th>동일가중<br>보유 대비</th></tr></thead><tbody>` +
      rows.map((p) => p.error
        ? `<tr><td class="rankc">–</td><td class="l">${esc(p.student)}</td><td class="l" colspan="13">${esc(p.name || p.ticker)} (${esc(p.ticker)}): ${esc(p.error)}</td></tr>`
        : `<tr><td class="rankc">${p.rank}</td><td class="l who-cell">${esc(p.student)}</td><td class="l">${esc(p.name)}</td><td class="l">${esc(p.symbol)}</td><td class="l">${SP.marketName(p.market)}</td>
          <td class="num">${price(p.basePrice, p.currency)}</td><td class="num">${price(p.price, p.currency)}</td><td class="num">${md(p.priceDate)}</td>
          <td class="num">${pct(p.priceRet)}</td><td class="num">${pct(p.divRet)}</td><td class="num">${pct(p.localRet)}</td>
          <td class="num">${p.currency === "KRW" ? "–" : pct(p.fxRet)}</td>
          <td class="num"><strong>${span(p.krwRet, pct(p.krwRet))}</strong></td>
          <td class="num">${spy ? pp(p.krwRet - spy.krwRet) : "–"}</td><td class="num">${bh ? pp(p.krwRet - bh.krwRet) : "–"}</td></tr>`).join("") + "</tbody>";

    const beat = (b) => rows.filter((p) => p.krwRet != null && p.krwRet > b.krwRet).length;
    const n = rows.filter((p) => p.rank).length;
    $("#f-bench").innerHTML = `<thead><tr><th class="l">기준</th><th class="l">설명</th><th>원화 누적수익률</th><th>기간 중 최대 낙폭</th><th>이 기준을 이긴 종목</th></tr></thead><tbody>` +
      bms.map((b) => `<tr><td class="l"><strong>${esc(b.name)}</strong></td><td class="l">${esc(b.desc || (b.symbol ? `${b.symbol}, 현지통화 ${pct(b.localRet)}` : ""))}</td>
        <td class="num"><strong>${span(b.krwRet, pct(b.krwRet))}</strong></td><td class="num">${pct(b.mdd)}</td><td class="num">${beat(b)} / ${n}종목</td></tr>`).join("") + "</tbody>";

    const fx = Object.entries(f.fx || {}).map(([c, v]) => `${c}/KRW ${num(v.base, 2)} → ${num(v.now, 2)} (${pct(v.ret)})`).join(", ");
    $("#f-notes").innerHTML = `환율: ${fx || "해당 없음"}. 수익률은 Yahoo Finance 수정주가(배당·분할 반영) 기준이며, 미국 종목은 현지통화 총수익률에 환율 변동을 곱해 원화로 환산했습니다. ` +
      `가격일이 평가일과 다른 종목은 해당 시장이 그날 열리지 않았거나 아직 종가가 반영되지 않은 경우입니다. 리밸런싱 주기: ${{ daily: "매 거래일", weekly: "매주", none: "없음" }[f.rebalance] || f.rebalance}, 변동성 창: ${f.volWindow}거래일.`;
  }

  function csv() {
    const bms = F.benchmarks || [], spy = bms.find((b) => b.kind === "etf"), bh = bms.find((b) => b.id === "p_bh");
    const q = (v) => `"${String(v ?? "").replace(/"/g, '""')}"`;
    const r4 = (v) => (v == null ? "" : (v * 100).toFixed(4));
    const lines = [["순위", "추천자", "종목", "티커", "시장", "통화", "기준가", "평가가", "가격일", "주가수익률(%)", "배당기여(%)", "현지통화총수익률(%)", "환율효과(%)", "원화누적수익률(%)", "S&P500대비(%p)", "동일가중보유대비(%p)"].map(q).join(",")];
    (F.pitches || []).forEach((p) => lines.push((p.error
      ? ["", p.student, p.name || p.ticker, p.ticker, "", "", "", "", "", "", "", "", "", p.error, "", ""]
      : [p.rank, p.student, p.name, p.symbol, p.market, p.currency, p.basePrice, p.price, p.priceDate, r4(p.priceRet), r4(p.divRet), r4(p.localRet), r4(p.fxRet), r4(p.krwRet), spy ? r4(p.krwRet - spy.krwRet) : "", bh ? r4(p.krwRet - bh.krwRet) : ""]).map(q).join(",")));
    bms.forEach((b) => lines.push(["기준", "", b.name, b.symbol || "", "", "", "", "", "", "", "", "", "", r4(b.krwRet), "", ""].map(q).join(",")));
    const a = document.createElement("a");
    a.href = URL.createObjectURL(new Blob(["﻿" + lines.join("\r\n")], { type: "text/csv;charset=utf-8" }));
    a.download = `stock-pitch-${F.status === "final" ? "final" : "provisional"}-${F.asOfDate}.csv`;
    a.click();
    setTimeout(() => URL.revokeObjectURL(a.href), 2000);
  }

  $("#btn-print").addEventListener("click", () => window.print());
  $("#btn-csv").addEventListener("click", () => F && csv());
  SP.subscribe((j) => { if (j.final) { F = j.final; render(); } });
})();
