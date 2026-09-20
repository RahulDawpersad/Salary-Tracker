(() => {
  // ---------- settings ----------
  const CURRENCY = "R"; // change to "$", "£", etc. (also change the "R" in templates/index.html)
  const CAT_COLORS = {
    Housing: "#7a5c48",
    Utilities: "#3b9ac2",
    Food: "#e08a1e",
    Transport: "#7a6bd0",
    Insurance: "#2f9e6f",
    Debt: "#d4483a",
    Family: "#d0509a",
    Savings: "#159a8c",
    Subscriptions: "#9aa02c",
    Other: "#7c8794",
  };
  const ICONS = {
    Housing: "🏠", Utilities: "💡", Food: "🛒", Transport: "🚗", Insurance: "🛡️",
    Debt: "💳", Family: "👪", Savings: "🐖", Subscriptions: "📺", Other: "📦",
  };
  const CATS = Object.keys(CAT_COLORS);
  const colorFor = (c) => CAT_COLORS[c] || CAT_COLORS.Other;
  const iconFor = (c) => ICONS[c] || ICONS.Other;

  // ---------- helpers ----------
  const $ = (s) => document.querySelector(s);
  const state = { month: thisMonth(), salary: 0, items: [], filter: "all" };

  function thisMonth() {
    const d = new Date();
    return d.getFullYear() + "-" + String(d.getMonth() + 1).padStart(2, "0");
  }
  const money = (n) =>
    CURRENCY + " " + (Number(n) || 0).toLocaleString("en-GB", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  const compact = (n) => {
    const a = Math.abs(n);
    if (a >= 1000) return (n / 1000).toFixed(a >= 10000 ? 0 : 1).replace(/\.0$/, "") + "k";
    return String(Math.round(n));
  };
  const num = (v) => {
    const n = parseFloat(String(v).replace(/[^\d.]/g, ""));
    return isNaN(n) ? 0 : n;
  };
  const sum = (arr) => arr.reduce((a, b) => a + b, 0);
  const pct = (a, b) => (b > 0 ? Math.round((a / b) * 1000) / 10 : 0);
  const plural = (n, word) => `${n} ${word}${n === 1 ? "" : "s"}`;
  const monthName = (m) => {
    const [y, mo] = m.split("-").map(Number);
    return new Date(y, mo - 1).toLocaleString("en-GB", { month: "long", year: "numeric" });
  };

  function el(tag, props = {}, ...kids) {
    const e = document.createElement(tag);
    for (const [k, v] of Object.entries(props)) {
      if (k === "class") e.className = v;
      else if (k === "style") e.style.cssText = v;
      else if (k === "value" || k === "checked") e[k] = v;
      else if (k.startsWith("on")) e.addEventListener(k.slice(2), v);
      else if (v === true) e.setAttribute(k, "");
      else if (v !== false && v != null) e.setAttribute(k, v);
    }
    for (const kid of kids.flat()) if (kid != null) e.append(kid);
    return e;
  }

  let statusTimer;
  function say(msg, isError = false) {
    const s = $("#status");
    s.textContent = msg;
    s.classList.toggle("error", isError);
    clearTimeout(statusTimer);
    if (msg && !isError) statusTimer = setTimeout(() => (s.textContent = ""), 3500);
  }

  async function api(url, method = "GET", body) {
    const res = await fetch(url, {
      method,
      headers: { "Content-Type": "application/json" },
      body: body === undefined ? undefined : JSON.stringify(body),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.error || "Something went wrong.");
    return data;
  }

  function chip(cat) {
    return el("span", { class: "chip", "aria-hidden": "true", style: `background:${colorFor(cat)}26` }, iconFor(cat));
  }

  // ---------- load ----------
  async function load() {
    try {
      const data = await api(`/api/month/${state.month}`);
      state.salary = data.salary;
      state.items = data.items;
      $("#sub").textContent = monthName(state.month);
      const salaryInput = $("#salary");
      if (document.activeElement !== salaryInput) salaryInput.value = state.salary ? state.salary.toFixed(2) : "";
      renderRows();
      renderSummary();
      loadHistory();
    } catch (e) {
      say(e.message, true);
    }
  }

  // ---------- summary: KPIs, donut, legend ----------
  function renderSummary() {
    const salary = state.salary;
    const spent = sum(state.items.map((i) => i.amount));
    const paid = sum(state.items.filter((i) => i.paid).map((i) => i.amount));
    const left = salary - spent;

    const byCat = {};
    state.items.forEach((i) => (byCat[i.category] = (byCat[i.category] || 0) + i.amount));
    const cats = Object.entries(byCat).filter(([, v]) => v > 0).sort((a, b) => b[1] - a[1]);

    // KPI cards
    $("#kSpent").textContent = money(spent);
    $("#kSpentSub").textContent =
      plural(state.items.length, "account") + (salary > 0 ? ` · ${pct(spent, salary)}% of salary` : "");
    $("#kPaid").textContent = money(paid);
    $("#kPaidBar").style.width = pct(paid, spent) + "%";
    $("#kPaidSub").textContent = `Still to pay ${money(spent - paid)}`;

    const short = left < 0;
    $("#kLeftCard").classList.toggle("short", short);
    $("#kLeftLabel").textContent = short ? "Short by" : "Left over";
    $("#kLeft").textContent = money(Math.abs(left));
    $("#kLeftSub").textContent =
      salary > 0 ? `${pct(Math.abs(left), salary)}% of your salary` : spent > 0 ? "Enter your salary to see this" : "Add your salary to start";
    $("#kHand").textContent = `In hand right now: ${money(salary - paid)}`;

    renderDonut(cats, salary, spent, left);
    renderLegend(cats, salary, spent, left);
  }

  const SVG_NS = "http://www.w3.org/2000/svg";
  function svg(tag, attrs = {}, ...kids) {
    const n = document.createElementNS(SVG_NS, tag);
    for (const [k, v] of Object.entries(attrs)) n.setAttribute(k, v);
    for (const kid of kids) if (kid != null) n.append(kid);
    return n;
  }
  const svgText = (attrs, text) => {
    const t = svg("text", attrs);
    t.textContent = text;
    return t;
  };

  function renderDonut(cats, salary, spent, left) {
    const host = $("#donut");
    host.replaceChildren();
    const R = 70, SW = 22, C = 2 * Math.PI * R;
    const chart = svg("svg", { viewBox: "0 0 180 180", role: "img", "aria-label": "Share of salary by category" });
    chart.append(svg("circle", { class: "track", cx: 90, cy: 90, r: R, fill: "none", "stroke-width": SW }));

    const base = Math.max(salary, spent);
    if (base > 0) {
      const parts = cats.map(([c, v]) => ({ name: c, v, color: colorFor(c) }));
      if (left > 0) parts.push({ name: "Left over", v: left, color: "var(--primary)" });
      const g = svg("g", { transform: "rotate(-90 90 90)" });
      let offset = 0;
      parts.forEach((p) => {
        const len = (p.v / base) * C;
        const dash = Math.max(len - 2, 0.5);
        const seg = svg("circle", {
          cx: 90, cy: 90, r: R, fill: "none", "stroke-width": SW,
          "stroke-dasharray": `${dash} ${C - dash}`, "stroke-dashoffset": -offset,
          style: `stroke:${p.color}`,
        });
        seg.append(svg("title", {}, document.createTextNode(`${p.name}: ${money(p.v)}`)));
        g.append(seg);
        offset += len;
      });
      chart.append(g);
    }

    const over = salary > 0 && spent > salary;
    chart.append(svgText({ x: 90, y: 92, "text-anchor": "middle", class: "big" + (over ? " bad" : "") }, salary > 0 ? `${Math.round(pct(spent, salary))}%` : "–"));
    chart.append(svgText({ x: 90, y: 110, "text-anchor": "middle", class: "small" }, "of salary spent"));
    host.append(chart);
  }

  function renderLegend(cats, salary, spent, left) {
    const legend = $("#legend");
    legend.replaceChildren();
    if (!cats.length) {
      legend.append(el("li", {}, el("span", { class: "empty muted", style: "grid-column:1/-1" }, "Add accounts to see the split.")));
      return;
    }
    const base = salary > 0 ? Math.max(salary, spent) : spent;
    const entry = (icon, tint, name, value, color) =>
      el("li", {},
        el("span", { class: "chip", "aria-hidden": "true", style: `background:${tint}` }, icon),
        el("div", { class: "meta" }, el("b", {}, name), el("span", {}, `${pct(value, base)}%`)),
        el("span", { class: "amt" }, money(value)),
        el("div", { class: "bar" }, el("i", { style: `width:${pct(value, base)}%;background:${color}` }))
      );
    cats.forEach(([c, v]) => legend.append(entry(iconFor(c), colorFor(c) + "26", c, v, colorFor(c))));
    if (left > 0) legend.append(entry("💵", "var(--primary-soft)", "Left over", left, "var(--primary)"));
  }

  // ---------- accounts table ----------
  function catOptions(selected) {
    const list = CATS.includes(selected) || !selected ? CATS : [...CATS, selected];
    return list.map((c) => el("option", { value: c, selected: c === selected }, c));
  }

  function renderRows() {
    const host = $("#rows");
    host.replaceChildren();
    const all = state.items;
    const shown = all.filter((i) => (state.filter === "paid" ? i.paid : state.filter === "due" ? !i.paid : true));

    if (!all.length) {
      host.append(
        el("div", { class: "empty-rows" },
          el("p", {}, "No accounts yet. Add rent, insurance, groceries, anything that comes out of your salary."),
          el("button", { type: "button", class: "btn primary", onclick: openAdd }, "Add your first account"))
      );
    } else if (!shown.length) {
      host.append(el("div", { class: "empty-rows" }, el("p", {}, state.filter === "paid" ? "Nothing marked as paid yet." : "Everything is paid.")));
    } else {
      shown.forEach((it) => host.append(row(it)));
    }
    $("#tableFoot").textContent = all.length ? `${plural(shown.length, "account")} shown · ${money(sum(shown.map((i) => i.amount)))}` : "";
  }

  function row(it) {
    const rowEl = el("div", { class: "row" + (it.paid ? " is-paid" : "") });
    const chipSlot = el("span", {});
    const paintChip = () => chipSlot.replaceChildren(chip(it.category));
    paintChip();

    const pill = el("button", { type: "button", class: "pill", onclick: () => save({ paid: !it.paid }) });
    const paintPill = () => {
      pill.className = "pill " + (it.paid ? "paid" : "due");
      pill.textContent = it.paid ? "Paid" : "To pay";
      pill.title = it.paid ? "Mark as not paid" : "Mark as paid";
      pill.setAttribute("aria-pressed", String(it.paid));
      rowEl.classList.toggle("is-paid", it.paid);
    };
    paintPill();

    async function save(patch) {
      try {
        await api(`/api/items/${it.id}`, "PATCH", patch);
        Object.assign(it, patch);
        paintChip();
        paintPill();
        if ("paid" in patch && state.filter !== "all") renderRows();
        else footOnly();
        renderSummary();
        loadHistory();
      } catch (e) {
        say(e.message, true);
        renderRows();
      }
    }

    rowEl.append(
      el("div", { class: "name-cell" }, chipSlot,
        el("input", { class: "cell", value: it.name, maxlength: 80, "aria-label": "Account name", onchange: (e) => save({ name: e.target.value.trim() || it.name }) })),
      el("select", { class: "cell", "aria-label": "Category", onchange: (e) => save({ category: e.target.value }) }, catOptions(it.category)),
      el("input", {
        class: "cell amt", value: it.amount.toFixed(2), inputmode: "decimal", "aria-label": "Amount",
        onchange: (e) => { const v = num(e.target.value); e.target.value = v.toFixed(2); save({ amount: v }); },
      }),
      pill,
      el("label", { class: "switch" },
        el("input", { type: "checkbox", checked: it.recurring, "aria-label": "Repeats every month", onchange: (e) => save({ recurring: e.target.checked }) }),
        el("span", { class: "sw-text" }, "Monthly")),
      el("button", {
        type: "button", class: "icon", title: "Delete account", "aria-label": `Delete ${it.name}`,
        onclick: async () => {
          if (!confirm(`Delete "${it.name}"?`)) return;
          try {
            await api(`/api/items/${it.id}`, "DELETE");
            state.items = state.items.filter((x) => x.id !== it.id);
            renderRows();
            renderSummary();
            loadHistory();
          } catch (e) {
            say(e.message, true);
          }
        },
      }, "×")
    );
    return rowEl;
  }

  // footer total for the current filter (used after inline edits, without rebuilding rows)
  function footOnly() {
    const shown = state.items.filter((i) => (state.filter === "paid" ? i.paid : state.filter === "due" ? !i.paid : true));
    $("#tableFoot").textContent = `${plural(shown.length, "account")} shown · ${money(sum(shown.map((i) => i.amount)))}`;
  }

  // ---------- history chart ----------
  async function loadHistory() {
    try {
      renderHistory(await api("/api/history"));
    } catch (e) {
      say(e.message, true);
    }
  }

  function niceStep(max) {
    const raw = max / 4;
    const p = Math.pow(10, Math.floor(Math.log10(raw)));
    return ([1, 2, 2.5, 5, 10].map((m) => m * p).find((s) => s >= raw)) || raw;
  }

  function renderHistory(rows) {
    const host = $("#chart");
    host.replaceChildren();
    const data = rows.filter((r) => r.salary > 0 || r.spent > 0).slice(-12);
    $("#avg").textContent = "";
    if (!data.length) {
      host.append(el("p", { class: "muted" }, "Your monthly history shows up here once a month has a salary and some accounts."));
      return;
    }

    const W = 720, H = 260, padL = 42, padR = 8, padTop = 12, padBottom = 50;
    const rawMax = Math.max(...data.flatMap((r) => [r.salary, r.spent]), 1);
    const step = niceStep(rawMax);
    const top = step * 4;
    const plotH = H - padTop - padBottom;
    const colW = (W - padL - padR) / data.length;
    const bw = Math.min(26, colW / 2 - 4);
    const y = (v) => padTop + plotH - (v / top) * plotH;

    const chart = svg("svg", { viewBox: `0 0 ${W} ${H}`, role: "img", "aria-label": "Salary and accounts for each month" });

    for (let i = 0; i <= 4; i++) {
      const v = step * i;
      chart.append(svg("line", { class: "grid" + (i === 0 ? " base" : ""), x1: padL, x2: W - padR, y1: y(v), y2: y(v) }));
      chart.append(svgText({ x: padL - 8, y: y(v) + 4, "text-anchor": "end" }, compact(v)));
    }

    data.forEach((r, i) => {
      const cx = padL + colW * i + colW / 2;
      const [yr, mo] = r.month.split("-").map(Number);
      const label = new Date(yr, mo - 1).toLocaleString("en-GB", { month: "short" }) + " " + String(yr).slice(2);
      const isCur = r.month === state.month;
      const g = svg("g", { class: "grp" });
      g.append(svg("title", {}, document.createTextNode(`${label}: salary ${money(r.salary)}, accounts ${money(r.spent)}, left ${money(r.left)}`)));
      if (isCur) g.append(svg("rect", { class: "cur-bg", x: cx - colW / 2 + 2, y: padTop - 4, width: colW - 4, height: H - padTop - 6, rx: 8 }));
      else g.append(svg("rect", { x: cx - colW / 2, y: 0, width: colW, height: H, fill: "transparent" }));
      g.append(svg("rect", { class: "b-salary", x: cx - bw - 1, y: y(r.salary), width: bw, height: padTop + plotH - y(r.salary), rx: 3 }));
      g.append(svg("rect", { class: "b-spent", x: cx + 1, y: y(r.spent), width: bw, height: padTop + plotH - y(r.spent), rx: 3 }));
      g.append(svgText({ x: cx, y: H - 27, "text-anchor": "middle", class: isCur ? "cur-label" : "" }, label));
      g.append(svgText({ x: cx, y: H - 11, "text-anchor": "middle", class: r.left >= 0 ? "left-pos" : "left-neg" }, (r.left >= 0 ? "+" : "-") + compact(Math.abs(r.left))));
      g.addEventListener("click", () => setMonth(r.month));
      chart.append(g);
    });
    host.append(chart);

    const withSalary = data.filter((r) => r.salary > 0);
    if (withSalary.length) {
      const avg = sum(withSalary.map((r) => r.left)) / withSalary.length;
      $("#avg").textContent = `Average left over: ${money(avg)} across ${plural(withSalary.length, "month")}. Click a month to open it.`;
    }
  }

  // ---------- actions ----------
  function setMonth(m) {
    if (!m) return;
    state.month = m;
    $("#month").value = m;
    load();
  }
  function shiftMonth(delta) {
    const [y, m] = state.month.split("-").map(Number);
    const d = new Date(y, m - 1 + delta, 1);
    setMonth(d.getFullYear() + "-" + String(d.getMonth() + 1).padStart(2, "0"));
  }

  async function saveSalary() {
    const salaryInput = $("#salary");
    const value = num(salaryInput.value);
    try {
      await api(`/api/month/${state.month}`, "PUT", { salary: value });
      state.salary = value;
      salaryInput.value = value ? value.toFixed(2) : "";
      renderSummary();
      loadHistory();
      say("Salary saved.");
    } catch (e) {
      say(e.message, true);
    }
  }

  function openAdd() {
    const d = $("#addDialog");
    if (typeof d.showModal === "function") d.showModal();
    else d.setAttribute("open", "");
    $("#newName").focus();
  }
  function closeAdd() {
    const d = $("#addDialog");
    if (typeof d.close === "function") d.close();
    else d.removeAttribute("open");
  }

  async function addAccount(e) {
    e.preventDefault();
    const name = $("#newName");
    const amount = $("#newAmount");
    try {
      await api(`/api/month/${state.month}/items`, "POST", {
        name: name.value,
        category: $("#newCategory").value,
        amount: num(amount.value),
        recurring: $("#newRecurring").checked,
      });
      say(`Added ${name.value.trim()}.`);
      name.value = "";
      amount.value = "";
      name.focus();
      await load();
    } catch (err) {
      say(err.message, true);
    }
  }

  async function copyPrevious() {
    try {
      const r = await api(`/api/month/${state.month}/copy`, "POST");
      say(r.copied
        ? `Copied ${plural(r.copied, "monthly account")} from ${monthName(r.source)}.`
        : `Nothing new to copy from ${monthName(r.source)}. Accounts marked Monthly carry over.`);
      await load();
    } catch (e) {
      say(e.message, true);
    }
  }

  // ---------- init ----------
  $("#newCategory").append(...CATS.map((c) => el("option", { value: c }, `${iconFor(c)}  ${c}`)));
  $("#month").value = state.month;
  $("#month").addEventListener("change", (e) => setMonth(e.target.value));
  $("#prevMonth").addEventListener("click", () => shiftMonth(-1));
  $("#nextMonth").addEventListener("click", () => shiftMonth(1));
  $("#salary").addEventListener("change", saveSalary);
  $("#salary").addEventListener("keydown", (e) => e.key === "Enter" && e.target.blur());
  $("#openAdd").addEventListener("click", openAdd);
  $("#closeAdd").addEventListener("click", closeAdd);
  $("#addDialog").addEventListener("click", (e) => e.target === e.currentTarget && closeAdd());
  $("#addForm").addEventListener("submit", addAccount);
  $("#copyPrev").addEventListener("click", copyPrevious);
  document.querySelectorAll(".tabs button").forEach((b) =>
    b.addEventListener("click", () => {
      state.filter = b.dataset.filter;
      document.querySelectorAll(".tabs button").forEach((x) => x.classList.toggle("on", x === b));
      renderRows();
    })
  );
  document.querySelectorAll("#nav a[href^='#']").forEach((a) =>
    a.addEventListener("click", () => {
      document.querySelectorAll("#nav a").forEach((x) => x.classList.toggle("active", x === a));
    })
  );
  load();
})();
