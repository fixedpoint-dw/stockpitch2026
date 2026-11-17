/* 데이터 불러오기(Firebase REST + 실시간 알림)와 표시 형식 도우미 */
const SP = (() => {
  const cfg = window.SP_CONFIG || {};
  const configured = !!cfg.databaseURL;
  const demo = new URLSearchParams(location.search).has("demo");   // 주소 뒤에 ?demo → 가상 데이터 미리 보기
  const base = configured ? cfg.databaseURL.replace(/\/+$/, "") + "/" + (cfg.path || "stockpitch") : null;

  async function load() {
    if (demo) {
      const r = await fetch("data/demo.json", { cache: "no-store" });
      if (!r.ok) throw new Error("data/demo.json 을 읽지 못했습니다");
      return { ...(await r.json()), source: "demo" };
    }
    if (!configured) throw Object.assign(new Error("Firebase 주소가 설정되지 않았습니다"), { code: "unconfigured" });
    const r = await fetch(base + ".json", { cache: "no-store" });
    if (!r.ok) throw new Error("Firebase 응답 오류 (HTTP " + r.status + ")");
    return { ...((await r.json()) || {}), source: "firebase" };
  }

  /* 처음 한 번 읽고, 서버가 새로 저장할 때마다 다시 읽는다. 여러 화면이 같은 결과를 함께 쓴다. */
  const listeners = [];
  let started = false, last = null, timer = null;
  let missed = false;   // 화면이 가려진 동안 놓친 갱신이 있는지
  const refresh = () => load()
    .then((j) => { last = j; listeners.forEach((l) => l.onData(j)); })
    .catch((e) => listeners.forEach((l) => l.onError && l.onError(e)));
  const soon = () => {
    if (document.hidden) { missed = true; return; }   // 보고 있지 않은 탭은 다시 읽지 않는다 (다시 보일 때 한 번에 읽음)
    clearTimeout(timer); timer = setTimeout(refresh, 800);
  };
  function subscribe(onData, onError) {
    listeners.push({ onData, onError });
    if (last) onData(last);
    if (started) return;
    started = true;
    refresh();
    if (demo || !configured) return;
    try {
      let first = true;
      const es = new EventSource(base + "/status.json");
      const hit = () => { if (first) { first = false; return; } soon(); };
      es.addEventListener("put", hit);
      es.addEventListener("patch", hit);
    } catch (e) { /* EventSource 미지원 → 아래 주기 확인으로 충분 */ }
    setInterval(soon, 5 * 60 * 1000);
    document.addEventListener("visibilitychange", () => { if (!document.hidden) { missed = false; soon(); } });
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

  return { demo, configured, subscribe, esc, sign, dir, pct, pp, arrow, num, price, md, mdK, dow, kst, todayKst, dayDiff, marketName };
})();
