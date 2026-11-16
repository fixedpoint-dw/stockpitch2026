/* 데이터 불러오기(Firebase REST + 실시간 알림)와 표시 형식 도우미 */
const SP = (() => {
  const cfg = window.SP_CONFIG || {};
  const demo = new URLSearchParams(location.search).has("demo") || !cfg.databaseURL;
  const base = demo ? null : cfg.databaseURL.replace(/\/+$/, "") + "/" + (cfg.path || "stockpitch");

  async function load() {
    if (demo) {
      const r = await fetch("data/demo.json", { cache: "no-store" });
      if (!r.ok) throw new Error("data/demo.json 을 읽지 못했습니다");
      return { ...(await r.json()), source: "demo" };
    }
    const r = await fetch(base + ".json", { cache: "no-store" });
    if (!r.ok) throw new Error("Firebase 응답 오류 (HTTP " + r.status + ")");
    return { ...((await r.json()) || {}), source: "firebase" };
  }

  /* 처음 한 번 읽고, 서버가 새로 저장할 때마다 다시 읽는다 */
  function subscribe(onData, onError) {
    let timer = null;
    const refresh = () => load().then(onData).catch(onError);
    const soon = () => { clearTimeout(timer); timer = setTimeout(refresh, 800); };
    refresh();
    if (demo) return;
    try {
      let first = true;
      const es = new EventSource(base + "/status.json");
      const hit = () => { if (first) { first = false; return; } soon(); };
      es.addEventListener("put", hit);
      es.addEventListener("patch", hit);
    } catch (e) { /* EventSource 미지원 → 아래 주기 확인으로 충분 */ }
    setInterval(refresh, 5 * 60 * 1000);
    document.addEventListener("visibilitychange", () => { if (!document.hidden) soon(); });
  }

  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const sign = (v) => (v > 0 ? "+" : v < 0 ? "−" : "");
  const dir = (v) => (v > 5e-7 ? "up" : v < -5e-7 ? "down" : "flat");
  const pct = (v, d = 2) => (v == null || !isFinite(v) ? "–" : sign(v) + Math.abs(v * 100).toFixed(d) + "%");
  const pp = (v, d = 2) => (v == null || !isFinite(v) ? "–" : sign(v) + Math.abs(v * 100).toFixed(d) + "%p");
  const arrow = (v) => ({ up: "▲", down: "▼", flat: "" }[dir(v)]);
  const num = (v, d = 0) => Number(v).toLocaleString("ko-KR", { minimumFractionDigits: d, maximumFractionDigits: d });
  function price(v, cur) {
    if (v == null) return "–";
    if (cur === "KRW") return num(v) + "원";
    if (cur === "USD") return "$" + num(v, 2);
    return num(v, 2) + " " + cur;
  }
  const md = (iso) => { const [, m, d] = iso.split("-"); return +m + "/" + +d; };
  const mdK = (iso) => { const [, m, d] = iso.split("-"); return +m + "월 " + +d + "일"; };
  const dow = (iso) => "일월화수목금토"[new Date(iso + "T00:00:00Z").getUTCDay()];
  function kst(isoUtc) {
    const p = new Intl.DateTimeFormat("ko-KR", { timeZone: "Asia/Seoul", month: "numeric", day: "numeric", hour: "2-digit", minute: "2-digit", hour12: false }).formatToParts(new Date(isoUtc));
    const g = (t) => p.find((x) => x.type === t).value;
    return `${g("month")}/${g("day")} ${g("hour")}:${g("minute")}`;
  }
  const todayKst = () => new Intl.DateTimeFormat("en-CA", { timeZone: "Asia/Seoul" }).format(new Date());
  const dayDiff = (a, b) => Math.round((Date.parse(b + "T00:00:00Z") - Date.parse(a + "T00:00:00Z")) / 864e5);
  const marketName = (m) => ({ KR: "한국", US: "미국" }[m] || m);

  return { demo, subscribe, esc, sign, dir, pct, pp, arrow, num, price, md, mdK, dow, kst, todayKst, dayDiff, marketName };
})();
