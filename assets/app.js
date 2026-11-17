/* 스톡 피치 리그 — 대시보드 */
(() => {
  const { esc, pct, pp, dir, arrow, price, md, mdK, dow, kst, num } = SP;
  const $ = (s, el = document) => el.querySelector(s);
  const PORT_COLOR = { p_bh: "var(--c-bh)", p_ew: "var(--c-ew)", p_rp: "var(--c-rp)" };
  const SEL_COLORS = ["var(--c-s0)", "var(--c-s1)", "var(--c-s2)"];
  const SHORT = { p_bh: "동일가중 보유", p_ew: "동일가중 리밸런싱", p_rp: "리스크 패리티" };

  const state = { d: null, final: null, status: null, source: null, sel: [], open: null, q: "", market: "", sort: { key: "rank", asc: true }, showAll: true };
  try { state.sel = JSON.parse(localStorage.getItem("sp-sel") || "[]").filter((s) => s && s.sid && s.slot < 3).slice(0, 3); } catch (e) { /* 저장소를 못 쓰면 선택만 기억하지 않는다 */ }
  const saveSel = () => { try { localStorage.setItem("sp-sel", JSON.stringify(state.sel)); } catch (e) { /* 무시 */ } };

  const bmColor = (b) => (b.kind === "etf" ? "var(--c-spy)" : PORT_COLOR[b.id] || "var(--muted)");
  /* 시장 기준(ETF·지수)은 모두 먹색 선. 두 번째부터는 점선으로 구분한다 */
  const bmDash = (b) => b.kind === "etf" && state.d.benchmarks.filter((x) => x.kind === "etf").indexOf(b) > 0;
  const keyOf = (b) => `<span class="key ${bmDash(b) ? "dash" : ""}" style="--k:${bmColor(b)}"></span>`;
  const selOf = (sid) => state.sel.find((s) => s.sid === sid);
  const span = (v, text) => `<span class="${dir(v)}">${text}</span>`;

  function toggleSelect(sid, pitchId) {
    const cur = selOf(sid);
    if (cur && (pitchId == null || state.open === pitchId)) {
      state.sel = state.sel.filter((s) => s.sid !== sid);
      state.open = null;
    } else if (cur) {
      state.open = pitchId;
    } else {
      if (state.sel.length >= 3) state.sel.shift();
      const used = state.sel.map((s) => s.slot);
      state.sel.push({ sid, slot: [0, 1, 2].find((i) => !used.includes(i)) });
      state.open = pitchId ?? null;
    }
    saveSel();
    renderChart();
    renderTable();
  }

  /* 장 상태는 접속한 시각 기준으로 판단한다 (서버가 저장한 상태는 마지막 갱신 때의 것이라 낡을 수 있다) */
  const HOURS = { KR: ["Asia/Seoul", 9 * 60, 15 * 60 + 30], US: ["America/New_York", 9 * 60 + 30, 16 * 60] };
  function marketNow(k, s) {
    const h = HOURS[k];
    if (!h) return s.state === "open" ? "open" : "closed";
    const p = new Intl.DateTimeFormat("en-CA", { timeZone: h[0], weekday: "short", year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", hour12: false }).formatToParts(new Date());
    const g = (t) => p.find((x) => x.type === t).value;
    const mins = (+g("hour") % 24) * 60 + +g("minute");
    if (g("weekday") === "Sat" || g("weekday") === "Sun" || mins < h[1] || mins >= h[2]) return "closed";
    return s.lastBar === `${g("year")}-${g("month")}-${g("day")}` ? "open" : "wait";   // 개장 시간인데 오늘 가격이 아직 없으면 휴장이거나 갱신 전
  }

  /* ── 머리 ─────────────────────────────────────────────── */
  function renderHead() {
    const m = state.d.meta;
    document.title = m.title;
    $("#title").textContent = m.title;
    $("#subtitle").textContent = m.subtitle || "";
    const today = SP.todayKst();
    const left = SP.dayDiff(today, m.finalDate);
    $("#dday").textContent = left > 0 ? `D-${left}` : left === 0 ? "D-DAY" : `D+${-left}`;
    $("#period").textContent = `${mdK(m.baseDate)} 종가에서 출발, ${mdK(m.finalDate)} 종가로 평가`;
    const frac = Math.min(1, Math.max(0, SP.dayDiff(m.baseDate, today) / Math.max(1, SP.dayDiff(m.baseDate, m.finalDate))));
    $("#race-fill").style.width = frac * 100 + "%";
    $("#race-dot").style.left = frac * 100 + "%";
    $("#race-note").textContent = left > 0 ? "평가일까지 남은 날" : left === 0 ? "오늘 종가로 평가" : `${md(m.finalDate)} 평가 완료, 이후 계속 갱신`;
    $("#race-start").textContent = `${md(m.baseDate)} 출발`;
    $("#race-end").textContent = `${md(m.finalDate)} 평가`;

    const mk = (k) => {
      const s = (m.markets || {})[k];
      if (!s) return "";
      const st = state.source === "demo" ? s.state : marketNow(k, s);
      const label = { open: "거래 중", closed: "마감", wait: "휴장 또는 갱신 대기" }[st] || "마감";
      return `<div><dt>${SP.marketName(k)} 장</dt><dd><span class="dot ${st === "open" ? "open" : ""}"></span>${label}</dd></div>`;
    };
    const usd = (m.fx || {}).USD;
    $("#status").innerHTML =
      `<div><dt>갱신</dt><dd>${kst(m.updatedAt)} (한국 시각)</dd></div>` + mk("KR") + mk("US") +
      (usd ? `<div><dt>원/달러</dt><dd class="num">${num(usd.now, 1)}원 <span class="${dir(usd.ret)}" style="font-weight:500">기준일 대비 ${pct(usd.ret)}</span></dd></div>` : "") +
      `<div><dt>추천자</dt><dd>${m.nRecommenders}명, ${m.nStocks}종목</dd></div>`;

    const b = [];
    if (state.source === "demo") b.push(`<div class="banner">미리 보기 화면입니다. 추천자와 종목은 실제 명단이지만 <b>가격과 수익률은 가상 데이터</b>입니다.</div>`);
    const st = state.status;
    if (st && st.ok === false) b.push(`<div class="banner">최근 갱신(${kst(st.at)})에 실패해 이전 데이터를 보여주고 있습니다. ${esc(st.message || "")}</div>`);
    const ageH = (Date.now() - Date.parse(m.updatedAt)) / 36e5;
    const nowD = new Date(), wdU = nowD.getUTCDay(), hrU = nowD.getUTCHours();
    const quiet = wdU === 6 || wdU === 0 || (wdU === 5 && hrU >= 21) || (wdU === 1 && hrU < 1);   // 주말(한국 토 06시 ~ 월 10시)은 몇 시간에 한 번만 갱신
    if (state.source !== "demo" && ageH > (quiet ? 8 : 1.5)) b.push(`<div class="banner">마지막 갱신 후 ${ageH < 2 ? Math.round(ageH * 60) + "분" : Math.floor(ageH) + "시간"}이 지났습니다. 화면의 가격은 ${kst(m.updatedAt)} 기준이며, 자동 갱신이 늦어지고 있습니다.</div>`);
    const f = state.final;
    if (f && f.status === "final") b.push(`<div class="banner info">${mdK(f.finalDate)} 종가 기준 최종 평가가 확정되었습니다. <a href="#final">최종 평가 탭에서 보기</a></div>`);
    else if (f && f.status === "provisional") b.push(`<div class="banner info">평가일 종가를 집계하고 있습니다. 미국 장 마감 후 확정됩니다. <a href="#final">잠정 평가표 보기</a></div>`);
    $("#banners").innerHTML = b.join("");

    $("#tab-final-note").textContent = f ? { final: `(${md(m.finalDate)} 확정)`, provisional: "(잠정)", pending: `(${md(m.finalDate)} 예정)` }[f.status] || "" : "";
    document.querySelectorAll("[data-final]").forEach((e) => (e.textContent = mdK(m.finalDate)));
    document.querySelectorAll("[data-base]").forEach((e) => (e.textContent = mdK(m.baseDate)));
    document.querySelectorAll("[data-rebal]").forEach((e) => (e.textContent = { daily: "매 거래일", weekly: "매주 첫 거래일", none: "(리밸런싱 없음)" }[m.rebalance] || m.rebalance));
    document.querySelectorAll("[data-volw]").forEach((e) => (e.textContent = m.volWindow));
  }

  function renderBench() {
    $("#bench").style.setProperty("--n", state.d.benchmarks.length);
    $("#bench").innerHTML = state.d.benchmarks.map((b) => `
      <div>
        <div class="nm">${keyOf(b)}${esc(b.name)}</div>
        <div class="v num ${dir(b.krwRet)}">${pct(b.krwRet)}</div>
        <div class="d num">당일 ${span(b.dayRet, arrow(b.dayRet) + pct(b.dayRet).replace(/^[+−]/, ""))}</div>
      </div>`).join("");
  }

  /* ── 차트 ─────────────────────────────────────────────── */
  function futureDays(last, final) {
    const out = [];
    const end = Date.parse(final + "T00:00:00Z");
    for (let t = Date.parse(last + "T00:00:00Z") + 864e5; t <= end; t += 864e5) {
      const w = new Date(t).getUTCDay();
      if (w > 0 && w < 6) out.push(new Date(t).toISOString().slice(0, 10));
    }
    return out;
  }
  function niceStep(range, n) {
    const raw = range / n, p = Math.pow(10, Math.floor(Math.log10(raw))), f = raw / p;
    return (f <= 1 ? 1 : f <= 2 ? 2 : f <= 2.5 ? 2.5 : f <= 5 ? 5 : 10) * p;
  }

  function renderChart() {
    const host = $("#chart"), D = state.d;
    if (!D || !host.clientWidth) return;   // 탭이 숨겨져 있으면 보일 때 다시 그린다
    const dates = D.dates, T = dates.length, S = D.series || {};
    const W = Math.max(280, host.clientWidth - 16), narrow = W < 640, H = narrow ? 300 : 400;
    const fut = futureDays(dates[T - 1], D.meta.finalDate);
    const slots = dates.concat(fut), nX = Math.max(slots.length, 2);
    let finIdx = -1;
    slots.forEach((d, i) => { if (d <= D.meta.finalDate) finIdx = i; });

    state.sel = state.sel.filter((s) => S[s.sid]);
    const names = D.stockNames || {};
    const hl = D.benchmarks.filter((b) => S[b.sid]).map((b) => ({ sid: b.sid, name: b.name, short: SHORT[b.id] || b.name, color: bmColor(b), dash: bmDash(b), sel: false }))
      .concat(state.sel.map((s) => ({ sid: s.sid, name: names[s.sid] || s.sid, short: names[s.sid] || s.sid, color: SEL_COLORS[s.slot], sel: true })));
    const others = state.showAll ? Object.keys(S).filter((k) => k.startsWith("s_") && !selOf(k)) : [];

    let lo = 0, hi = 0;
    hl.map((h) => h.sid).concat(others).forEach((k) => S[k].forEach((v) => { const p = (v - 1) * 100; if (p < lo) lo = p; if (p > hi) hi = p; }));
    if (hi - lo < 2) { hi += 1; lo -= 1; }
    const step = niceStep(hi - lo, narrow ? 4 : 6);
    lo = Math.floor(lo / step) * step; hi = Math.ceil(hi / step) * step;

    const m = { l: 48, r: 16, t: 24, b: 30 };
    const futRoom = (fut.length / (nX - 1)) * (W - m.l - m.r);
    const labels = !narrow || futRoom >= 130;
    if (labels && futRoom < 150) m.r = 158;
    const pw = W - m.l - m.r, ph = H - m.t - m.b;
    const x = (i) => m.l + (i / (nX - 1)) * pw;
    const y = (v) => m.t + (1 - ((v - 1) * 100 - lo) / (hi - lo)) * ph;
    const path = (arr) => arr.map((v, i) => (i ? "L" : "M") + x(i).toFixed(1) + " " + y(v).toFixed(1)).join("");

    let g = "";
    if (fut.length) g += `<rect class="future" x="${x(T - 1)}" y="${m.t}" width="${x(nX - 1) - x(T - 1)}" height="${ph}"/>`;
    for (let v = lo; v <= hi + 1e-9; v += step) {
      const yy = y(1 + v / 100);
      g += `<line class="${Math.abs(v) < 1e-9 ? "zero" : "grid"}" x1="${m.l}" x2="${m.l + pw}" y1="${yy}" y2="${yy}"/>`;
      g += `<text class="tick" x="${m.l - 8}" y="${yy + 4}" text-anchor="end">${v > 0 ? "+" : v < 0 ? "−" : ""}${Math.abs(+v.toFixed(2))}%</text>`;
    }
    const every = Math.ceil(nX / (narrow ? 5 : 9));
    for (let i = 0; i < nX; i += every) {
      if (finIdx >= 0 && Math.abs(finIdx - i) < every * 0.6) continue;
      g += `<text class="tick" x="${x(i)}" y="${H - 8}" text-anchor="${i === 0 ? "start" : "middle"}">${md(slots[i])}</text>`;
    }
    if (finIdx >= 0) {
      const fx = x(finIdx), atEdge = finIdx === nX - 1;
      g += `<line class="finish" x1="${fx}" x2="${fx}" y1="${m.t - 6}" y2="${m.t + ph}"/>`;
      g += `<text class="finish-label" x="${fx + (atEdge ? -6 : 6)}" y="${m.t - 10}" text-anchor="${atEdge ? "end" : "start"}">${md(D.meta.finalDate)} 평가</text>`;
      g += `<text class="tick" x="${fx}" y="${H - 8}" text-anchor="${atEdge ? "end" : "middle"}">${md(slots[finIdx])}</text>`;
    }
    others.forEach((k) => (g += `<path class="ln ln-other" d="${path(S[k])}"/>`));
    g += `<path class="ln ln-near" id="near" d="" visibility="hidden"/>`;
    hl.forEach((h) => (g += `<path class="ln ${h.sel ? "ln-sel" : "ln-hl"}" style="stroke:${h.color}"${h.dash ? ' stroke-dasharray="6 4"' : ""} d="${path(S[h.sid])}"/>`));

    const ends = hl.map((h) => ({ h, v: S[h.sid][T - 1], y: y(S[h.sid][T - 1]) }));
    ends.forEach((e) => (g += `<circle class="end-dot" cx="${x(T - 1)}" cy="${e.y}" r="${e.h.sel ? 5 : 4}" style="fill:${e.h.color}"/>`));
    if (labels) {
      const lab = (hl.length <= 5 ? ends : ends.filter((e) => e.h.sel)).map((e) => ({ ...e, ly: e.y })).sort((a, b) => a.ly - b.ly);
      for (let i = 1; i < lab.length; i++) lab[i].ly = Math.max(lab[i].ly, lab[i - 1].ly + 16);
      for (let i = lab.length - 1; i >= 0; i--) {
        const cap = i === lab.length - 1 ? m.t + ph - 2 : lab[i + 1].ly - 16;
        lab[i].ly = Math.min(lab[i].ly, cap);
      }
      lab.forEach((e) => {
        const nm = e.h.short.length > 15 ? e.h.short.slice(0, 14) + "…" : e.h.short;
        g += `<text class="end-label" x="${x(T - 1) + 10}" y="${e.ly + 4}">${esc(nm)} <tspan>${pct(e.v - 1, 1)}</tspan></text>`;
      });
    }
    g += `<g id="cross" visibility="hidden"><line class="cross" y1="${m.t}" y2="${m.t + ph}"/>${hl.map((h) => `<circle class="end-dot" r="4.5" style="fill:${h.color}"/>`).join("")}</g>`;
    g += `<rect id="hit" x="${m.l}" y="${m.t}" width="${pw}" height="${ph}" fill="transparent"/>`;

    host.innerHTML = `<svg viewBox="0 0 ${W} ${H}" height="${H}" role="img" aria-label="${esc(mdK(D.meta.baseDate))} 종가 이후 원화 기준 누적수익률 선 그래프. 수치는 아래 순위표에 있습니다.">${g}</svg><div class="tip" hidden></div>` +
      (T < 2 ? `<p class="chart-note">기준일 다음 거래일의 가격이 들어오면 선이 그려집니다.</p>` : "");

    const svg = $("svg", host), tip = $(".tip", host), cross = $("#cross", svg), near = $("#near", svg);
    let nearSid = null;
    function move(ev) {
      const r = svg.getBoundingClientRect();
      const px = ((ev.clientX - r.left) / r.width) * W, py = ((ev.clientY - r.top) / r.height) * H;
      const i = Math.min(T - 1, Math.max(0, Math.round(((px - m.l) / pw) * (nX - 1))));
      cross.setAttribute("visibility", "visible");
      const ln = cross.firstChild; ln.setAttribute("x1", x(i)); ln.setAttribute("x2", x(i));
      hl.forEach((h, n) => { const c = cross.childNodes[n + 1]; c.setAttribute("cx", x(i)); c.setAttribute("cy", y(S[h.sid][i])); });
      nearSid = null;
      let best = 14;
      others.forEach((k) => { const dy = Math.abs(y(S[k][i]) - py); if (dy < best) { best = dy; nearSid = k; } });
      if (nearSid) { near.setAttribute("d", path(S[nearSid])); near.setAttribute("visibility", "visible"); } else near.setAttribute("visibility", "hidden");
      const rows = hl.slice().sort((a, b) => S[b.sid][i] - S[a.sid][i]).map((h) =>
        `<div class="r"><span class="key ${h.dash ? "dash" : ""}" style="--k:${h.color}"></span><span>${esc(h.name)}</span><b>${pct(S[h.sid][i] - 1)}</b></div>`).join("");
      tip.innerHTML = `<h4>${mdK(dates[i])} (${dow(dates[i])})${i === 0 ? " 기준일" : ""}</h4>${rows}` +
        (nearSid ? `<div class="near r"><span class="key" style="--k:var(--ink-2)"></span><span>${esc(names[nearSid] || nearSid)}</span><b>${pct(S[nearSid][i] - 1)}</b></div><div style="color:var(--muted);font-size:11.5px">누르면 이 종목을 고정합니다</div>` : "");
      tip.hidden = false;
      const cx = (x(i) / W) * r.width + 8, tw = tip.offsetWidth;
      tip.style.left = Math.max(4, cx + 14 + tw > r.width ? cx - tw - 14 : cx + 14) + "px";
      tip.style.top = Math.max(4, Math.min((py / H) * r.height - 20, r.height - tip.offsetHeight - 4)) + "px";
    }
    function leave() { cross.setAttribute("visibility", "hidden"); near.setAttribute("visibility", "hidden"); tip.hidden = true; nearSid = null; }
    svg.addEventListener("pointermove", move);
    svg.addEventListener("pointerdown", move);
    svg.addEventListener("pointerleave", leave);
    svg.addEventListener("pointercancel", leave);
    svg.addEventListener("click", () => { if (nearSid) toggleSelect(nearSid, null); });
  }

  /* ── 순위표 ───────────────────────────────────────────── */
  function renderTable() {
    const D = state.d, tb = $("#league tbody");
    const spy = D.benchmarks.find((b) => b.kind === "etf");
    const q = state.q.trim().toLowerCase();
    let rows = D.pitches.map((p) => ({ ...p, vsSpy: spy && p.krwRet != null ? p.krwRet - spy.krwRet : null }));
    const total = rows.filter((p) => p.rank).length;
    rows = rows.filter((p) => (!state.market || p.market === state.market) &&
      (!q || [p.student, p.name, p.ticker, p.symbol].some((s) => (s || "").toLowerCase().includes(q))));
    const { key, asc } = state.sort;
    const val = (p) => (key === "rank" ? -(p.krwRet ?? -Infinity) : p[key] ?? -Infinity);
    const byRet = key === "rank" || key === "krwRet";
    let list = rows.map((p) => ({ t: "p", p, v: val(p) }));
    if (byRet) list = list.concat(D.benchmarks.map((b) => ({ t: "b", p: b, v: key === "rank" ? -b.krwRet : b.krwRet })));
    list.sort((a, b) => ("error" in a.p) - ("error" in b.p) || (asc ? a.v - b.v : b.v - a.v));
    if (!byRet) list = list.concat(D.benchmarks.map((b) => ({ t: "b", p: b })));
    const maxAbs = Math.max(0.01, ...D.pitches.map((p) => Math.abs(p.krwRet || 0)), ...D.benchmarks.map((b) => Math.abs(b.krwRet)));
    const bar = (v) => `<td class="barcell"><div class="bar"><i class="${dir(v) === "down" ? "down" : "up"}" style="width:${(Math.abs(v) / maxAbs) * 50}%"></i></div></td>`;
    const wOf = (sid) => D.benchmarks.filter((b) => b.weights).map((b) => { const w = b.weights.find((x) => x.sid === sid); return w ? `${SHORT[b.id] || b.name} ${(w.w * 100).toFixed(1)}%` : ""; }).filter(Boolean);

    tb.innerHTML = list.map(({ t, p }) => {
      if (t === "b") {
        return `<tr class="bm"><td class="rank"><span class="tag">기준</span></td><td class="l hide-m"></td>
          <td class="l stock"><div class="nmline">${keyOf(p)}<b>${esc(p.name)}</b></div>${p.desc ? `<small>${esc(p.desc)}</small>` : `<small>${esc(p.symbol || "")}</small>`}</td>
          <td class="px num">${p.price != null ? price(p.price, p.currency) : ""}<small class="${dir(p.dayRet)}">${arrow(p.dayRet)}${pct(p.dayRet).replace(/^[+−]/, "")}</small></td>
          <td class="hide-m num">${p.localRet != null ? span(p.localRet, pct(p.localRet)) : ""}</td>
          <td class="hide-m num">${p.fxRet != null && p.currency !== "KRW" ? span(p.fxRet, pct(p.fxRet)) : ""}</td>
          <td class="ret ${dir(p.krwRet)}">${pct(p.krwRet)}</td>${bar(p.krwRet)}<td class="hide-m"></td></tr>`;
      }
      if (p.error) {
        return `<tr class="bm"><td class="rank"><span class="tag">확인</span></td><td class="l hide-m">${esc(p.student)}</td>
          <td class="l stock" colspan="7"><b>${esc(p.name || p.ticker)}</b> <small class="who">${esc(p.student)}</small><span class="err">${esc(p.ticker)}: ${esc(p.error)}</span></td></tr>`;
      }
      const s = selOf(p.sid), color = s ? SEL_COLORS[s.slot] : "transparent";
      const mv = p.prevRank ? p.prevRank - p.rank : 0;
      const open = s && state.open === p.id;
      let html = `<tr class="row ${s ? "sel" : ""}" style="--k:${color}" data-id="${p.id}" data-sid="${p.sid}" tabindex="0" role="button" aria-expanded="${!!open}" aria-label="${esc(p.name)} (추천자 ${esc(p.student)}) 상세 ${open ? "닫기" : "보기"}">
        <td class="rank"><span class="rk">${p.rank}</span>${mv ? `<span class="mv ${mv > 0 ? "up" : "down"}">${mv > 0 ? "▲" : "▼"}${Math.abs(mv)}</span>` : ""}</td>
        <td class="l hide-m who-cell">${esc(p.student)}</td>
        <td class="l stock"><div class="nmline">${s ? `<span class="key" style="--k:${color}"></span>` : ""}<b>${esc(p.name)}</b></div><small>${esc(p.symbol)} <span class="tag">${SP.marketName(p.market)}</span></small><small class="who">${esc(p.student)}</small></td>
        <td class="px num">${price(p.price, p.currency)}<small class="${dir(p.localDayRet)}">${arrow(p.localDayRet)}${pct(p.localDayRet).replace(/^[+−]/, "")}</small></td>
        <td class="hide-m num">${span(p.localRet, pct(p.localRet))}</td>
        <td class="hide-m num">${p.currency === "KRW" ? `<span class="flat">–</span>` : span(p.fxRet, pct(p.fxRet))}</td>
        <td class="ret ${dir(p.krwRet)}">${pct(p.krwRet)}</td>${bar(p.krwRet)}
        <td class="hide-m num">${span(p.vsSpy, pp(p.vsSpy))}</td></tr>`;
      if (open) {
        const fr = state.final && state.final.status === "final" ? (state.final.pitches || []).find((x) => x.symbol === p.symbol && x.rank) : null;
        const divs = (p.dividends || []).map((d) => `${md(d.date)} ${price(d.amount, p.currency)}`).join(", ");
        html += `<tr class="detail" style="--k:${color}"><td colspan="9"><dl class="facts">
          <div><dt>기준가 (${md(p.baseBarDate)} 종가)</dt><dd>${price(p.basePrice, p.currency)}</dd></div>
          <div><dt>현재가 (${md(p.priceDate)})</dt><dd>${price(p.price, p.currency)}</dd></div>
          <div><dt>주가 수익률</dt><dd>${span(p.priceRet, pct(p.priceRet))}</dd></div>
          <div><dt>배당 기여</dt><dd>${span(p.divRet, pct(p.divRet))} <small>${divs ? "배당락 " + divs : "기간 중 배당락 없음"}</small></dd></div>
          <div><dt>현지통화 총수익률</dt><dd>${span(p.localRet, pct(p.localRet))}</dd></div>
          ${p.currency !== "KRW" ? `<div><dt>환율 (${esc(p.currency)}/KRW)</dt><dd>${num(p.fxBase, 1)} → ${num(p.fxNow, 1)} <small>${pct(p.fxRet)}</small></dd></div>` : ""}
          <div><dt>원화 누적수익률</dt><dd>${span(p.krwRet, pct(p.krwRet))} <small>${total}종목 중 ${p.rank}위</small></dd></div>
          ${fr ? `<div><dt>최종 평가 (${md(state.final.finalDate)} 종가)</dt><dd>${span(fr.krwRet, pct(fr.krwRet))} <small>${fr.rank}위</small></dd></div>` : ""}
          ${D.benchmarks.filter((b) => b.kind === "etf").map((b) => `<div><dt>${esc(b.name)} 대비</dt><dd>${span(p.krwRet - b.krwRet, pp(p.krwRet - b.krwRet))}</dd></div>`).join("")}
          <div><dt>3개월 변동성 (연율)</dt><dd>${p.vol3m != null ? (p.vol3m * 100).toFixed(1) + "%" : "–"}</dd></div>
          <div><dt>기간 중 최대 낙폭</dt><dd>${pct(p.mdd)}</dd></div>
        </dl>
        <p class="detail-foot">포트폴리오 내 비중: ${wOf(p.sid).join(", ") || "–"}. <a href="https://finance.yahoo.com/quote/${encodeURIComponent(p.symbol)}" target="_blank" rel="noopener">Yahoo Finance에서 ${esc(p.symbol)} 보기</a></p></td></tr>`;
      }
      return html;
    }).join("") || `<tr><td colspan="9" class="empty">조건에 맞는 종목이 없습니다. 검색어를 지워 보세요.</td></tr>`;

    $("#rank-sub").textContent = `${mdK(D.meta.lastDate)} 기준. 줄을 누르면 상세 정보가 열리고 그래프에 선이 추가됩니다 (최대 3종목).`;
    document.querySelectorAll("#league thead button").forEach((b) => {
      if (b.dataset.sort === key) b.setAttribute("aria-sort", asc ? "ascending" : "descending"); else b.removeAttribute("aria-sort");
    });
  }

  function renderWeights() {
    const D = state.d, ports = D.benchmarks.filter((b) => b.weights);
    if (!ports.length) { $("#weights").hidden = true; return; }
    const sids = ports[ports.length - 1].weights.slice().sort((a, b) => b.w - a.w).map((w) => w.sid);
    const max = Math.max(...ports.flatMap((p) => p.weights.map((w) => w.w)));
    $("#weights-table").innerHTML = `<thead><tr><th class="l">종목</th>${ports.map((p) => `<th class="l">${esc(p.name)}</th>`).join("")}</tr></thead><tbody>` +
      sids.map((sid) => `<tr style="border-top:1px solid var(--hair)"><td class="l">${esc((D.stockNames || {})[sid] || sid)}</td>${ports.map((p) => {
        const w = (p.weights.find((x) => x.sid === sid) || { w: 0 }).w;
        return `<td class="l num"><span class="wbar" style="--k:${bmColor(p)};width:${(w / max) * 90}px"></span>${(w * 100).toFixed(1)}%</td>`;
      }).join("")}</tr>`).join("") + "</tbody>";
  }

  function renderAll() { renderHead(); renderBench(); renderChart(); renderTable(); renderWeights(); }

  /* ── 이벤트 ───────────────────────────────────────────── */
  $("#league tbody").addEventListener("click", (e) => {
    if (e.target.closest("a")) return;
    const tr = e.target.closest("tr.row");
    if (tr) toggleSelect(tr.dataset.sid, tr.dataset.id);
  });
  $("#league tbody").addEventListener("keydown", (e) => {
    const tr = e.target.closest("tr.row");
    if (tr && (e.key === "Enter" || e.key === " ")) {
      e.preventDefault();
      const id = tr.dataset.id;
      toggleSelect(tr.dataset.sid, id);
      const again = $(`#league tr.row[data-id="${id}"]`);
      if (again) again.focus();
    }
  });
  $("#league thead").addEventListener("click", (e) => {
    const b = e.target.closest("button[data-sort]");
    if (!b) return;
    const k = b.dataset.sort;
    state.sort = state.sort.key === k ? { key: k, asc: !state.sort.asc } : { key: k, asc: k === "rank" };
    renderTable();
  });
  $("#search").addEventListener("input", (e) => { state.q = e.target.value; renderTable(); });
  $("#market-filter").addEventListener("click", (e) => {
    const b = e.target.closest("button");
    if (!b) return;
    state.market = b.dataset.m;
    document.querySelectorAll("#market-filter button").forEach((x) => x.setAttribute("aria-pressed", x === b));
    renderTable();
  });
  $("#show-all").addEventListener("change", (e) => { state.showAll = e.target.checked; renderChart(); });
  setInterval(() => { if (state.d) renderHead(); }, 60 * 1000);   // 장 상태·경과 시간 표시를 1분마다 새로 계산
  let rz;
  window.addEventListener("resize", () => { clearTimeout(rz); rz = setTimeout(renderChart, 150); });
  if (window.matchMedia) window.matchMedia("(prefers-color-scheme: dark)").addEventListener?.("change", renderChart);

  /* ── 탭: 실시간 현황 / 최종 평가 ─────────────────────── */
  function showTab() {
    const fin = location.hash === "#final";
    $("#view-live").hidden = fin;
    $("#view-final").hidden = !fin;
    $("#tab-live").setAttribute("aria-selected", !fin);
    $("#tab-final").setAttribute("aria-selected", fin);
    document.body.classList.toggle("tab-final", fin);
    if (!fin) renderChart();
  }
  $("#tab-live").addEventListener("click", () => { history.replaceState(null, "", location.pathname + location.search); showTab(); });
  $("#tab-final").addEventListener("click", () => { history.replaceState(null, "", location.pathname + location.search + "#final"); showTab(); });
  window.addEventListener("hashchange", showTab);
  showTab();

  const hold = (html) => { $("#banners").innerHTML = html; document.querySelector(".race").hidden = true; $("#status").hidden = true; document.querySelector(".tabs").hidden = true; $("#view-live").hidden = true; $("#view-final").hidden = true; };
  SP.subscribe((j) => {
    if (!j.current) {
      hold(`<div class="banner">Firebase에 아직 저장된 데이터가 없습니다. GitHub 저장소의 Actions 탭에서 “Update stock pitch data”를 한 번 실행하세요. 실행이 끝나면 이 화면이 자동으로 바뀝니다.</div>`);
      return;
    }
    document.querySelector(".tabs").hidden = false;
    document.querySelector(".race").hidden = false; $("#status").hidden = false;
    state.d = j.current; state.final = j.final || null; state.status = j.status || null; state.source = j.source;
    state.d.pitches = state.d.pitches || []; state.d.benchmarks = state.d.benchmarks || [];
    showTab();
    renderAll();
  }, (err) => {
    if (state.d) return;
    if (err.code === "unconfigured") hold(`<div class="banner"><b>아직 실제 데이터에 연결되지 않았습니다.</b> <code>assets/config.js</code>의 <code>databaseURL</code>에 Firebase Realtime Database 주소를 넣고, GitHub Actions에서 “Update stock pitch data”를 한 번 실행하면 추천 종목이 모두 표시됩니다. <a href="?demo">가상 데이터로 화면 미리 보기</a></div>`);
    else hold(`<div class="banner">데이터를 불러오지 못했습니다. ${esc(err.message)}. <code>assets/config.js</code>의 Firebase 주소와 데이터베이스 읽기 규칙(<code>stockpitch</code>의 <code>.read</code>)을 확인하세요.</div>`);
  });
})();
