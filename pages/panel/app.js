const bridge = window.AstrBotPluginPage;
const view = document.getElementById("view");

function toast(msg) {
  const t = document.getElementById("toast");
  t.textContent = msg;
  t.classList.add("show");
  setTimeout(() => t.classList.remove("show"), 2200);
}

function el(tag, attrs = {}, children = []) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === "class") node.className = v;
    else if (k.startsWith("on")) node.addEventListener(k.slice(2), v);
    else node.setAttribute(k, v);
  }
  for (const c of [].concat(children)) {
    node.append(c instanceof Node ? c : document.createTextNode(String(c)));
  }
  return node;
}

function ts(sec) {
  if (!sec) return "—";
  return new Date(sec * 1000).toLocaleString("zh-CN", { hour12: false });
}

async function call(fn) {
  try {
    return await fn();
  } catch (e) {
    toast("出错：" + e.message);
    throw e;
  }
}

/* ================= 总览 ================= */
async function renderOverview() {
  const d = await call(() => bridge.apiGet("overview"));
  const health = (label, ok) =>
    el("span", { class: ok ? "ok" : "bad" }, `${label}${ok ? "✅" : "❌"}  `);
  const sched = (d.schedule || [])
    .map((s) => `${s.start_hm}~${s.end_hm} ${s.activity}（${s.status}）`)
    .join("\n") || "（今天还没生成日程）";
  view.replaceChildren(
    el("div", { class: "cards" }, [
      el("div", { class: "card" }, [
        el("h3", {}, "她"),
        el("div", { class: "big" }, `${d.name || "未初始化"}\n${d.now || ""}`),
      ]),
      el("div", { class: "card" }, [
        el("h3", {}, "此刻"),
        el("div", { class: "big" }, `${d.activity || "—"}${d.sleeping ? "（睡眠时段）" : ""}\n${d.mood || "心情平静"}`),
      ]),
      el("div", { class: "card" }, [
        el("h3", {}, "关系"),
        el("div", { class: "big" }, `阶段：${d.stage || "—"}\n签名：${d.signature || "—"}\n头像：${d.avatar_desc || "—"}`),
      ]),
      el("div", { class: "card" }, [
        el("h3", {}, "互动"),
        el("div", { class: "big" },
          `绑定对话：${d.linked_umo || "未绑定（导演 bot /link）"}\n距他上次说话：${d.last_user_minutes == null ? "—" : d.last_user_minutes + " 分钟"}\n主动未回：${d.unanswered}`),
      ]),
      el("div", { class: "card wide" }, [
        el("h3", {}, "模块健康"),
        el("div", { class: "big" }, [
          health("向量库", d.vector_ok),
          health("生图", d.imagegen_ok),
          health("语音", d.tts_ok),
          el("span", { class: "meta" }, "　相册与照片能力见控制台 /presence /gallery"),
        ]),
      ]),
      el("div", { class: "card wide" }, [el("h3", {}, "今日日程"), el("div", { class: "big" }, sched)]),
      el("div", { class: "card wide" }, [
        el("h3", {}, "数据主权"),
        el("button", {
          class: "ghost",
          onclick: async () => {
            toast("正在打包…");
            await call(() => bridge.download("export", { gallery: 1 }));
          },
        }, "导出生命参数与记忆包"),
      ]),
    ]),
  );
}

/* ================= 生命参数 ================= */
async function renderPersona() {
  const d = await call(() => bridge.apiGet("profile"));
  const ta = el("textarea", {}, d.profile || "");
  ta.value = d.profile || "";
  const dyn = el("textarea", { readonly: "readonly", style: "min-height:180px" });
  dyn.value = d.dynamic || "";
  view.replaceChildren(
    el("div", { class: "toolbar" }, [
      el("button", {
        class: "action",
        onclick: async () => {
          await call(() => bridge.apiPost("profile/save", { profile: ta.value }));
          toast("已保存并热加载");
        },
      }, "保存生命参数"),
      el("span", { class: "meta" }, "作息、纪念日、外观基准、身世。人设写在 AstrBot 人格设定里。下方为系统演化的动态层（只读）。"),
    ]),
    ta,
    el("h3", {}, "动态层 dynamic.yaml"),
    dyn,
  );
}

/* ================= 日记 ================= */
async function renderDiary() {
  const load = async (type) => {
    const d = await call(() => bridge.apiGet("diaries", { limit: 20, type }));
    list.replaceChildren(
      ...(d.items || []).reverse().map((it) =>
        el("div", { class: "list-item" }, [
          el("div", { class: "meta" }, `${it.date}　${it.mood || ""}`),
          el("div", {}, it.content),
        ]),
      ),
    );
    if (!d.items || !d.items.length) list.textContent = "（还没有日记）";
  };
  const list = el("div");
  view.replaceChildren(
    el("div", { class: "toolbar" }, [
      el("button", { class: "ghost", onclick: () => load("daily") }, "日记"),
      el("button", { class: "ghost", onclick: () => load("weekly") }, "周记"),
    ]),
    list,
  );
  await load("daily");
}

/* ================= 记忆 ================= */
async function renderMemory() {
  const [sheet, facts] = await Promise.all([
    call(() => bridge.apiGet("cheatsheet")),
    call(() => bridge.apiGet("facts")),
  ]);
  const item = sheet.item;
  const factNode = (f) =>
    el("div", { class: "list-item" }, [
      el("span", { class: "tag" }, f.subject),
      f.category ? el("span", { class: "tag" }, f.category) : "",
      f.content,
      el("div", { class: "meta" }, `${f.source}　${ts(f.updated_ts)}`),
    ]);
  view.replaceChildren(
    el("div", { class: "card wide", style: "margin-bottom:12px" }, [
      el("h3", {}, `核心小抄（v${item ? item.version : 0}，她自己修订）`),
      el("div", { class: "big" }, item ? item.content : "（她还没写小抄）"),
    ]),
    el("h3", {}, `结构化事实（${(facts.items || []).length}）`),
    ...(facts.items || []).map(factNode),
  );
}

/* ================= 对话 ================= */
async function renderChat() {
  const d = await call(() => bridge.apiGet("chatlog", { limit: 120 }));
  view.replaceChildren(
    ...(d.items || []).map((c) =>
      el("div", { class: "list-item" }, [
        el("div", { class: "meta" }, `${c.role === "user" ? "他" : "她"}（${c.kind}）　${ts(c.ts)}`),
        el("div", {}, c.content),
      ]),
    ),
  );
  window.scrollTo(0, document.body.scrollHeight);
}

/* ================= 事件 ================= */
async function renderEvents() {
  const d = await call(() => bridge.apiGet("events", { limit: 60 }));
  const mention = { unmentioned: "未提及", told: "已讲过", discovered: "被发现" };
  view.replaceChildren(
    ...(d.items || []).map((e) =>
      el("div", { class: "list-item" }, [
        el("span", { class: "tag" }, e.kind),
        el("span", { class: "tag" }, mention[e.mention_status] || e.mention_status),
        e.description,
        el("div", { class: "meta" }, `${e.motivation ? "动机：" + e.motivation + "　" : ""}${ts(e.ts)}`),
      ]),
    ),
  );
}

/* ================= 排期 ================= */
async function renderPlans() {
  const d = await call(() => bridge.apiGet("pending"));
  const items = d.items || [];
  const nodes = items.map((p) =>
    el("div", { class: "list-item" }, [
      el("span", { class: "tag" }, p.kind),
      "#" + p.id + "　" + ts(p.due_ts) + "　" + ((p.payload && p.payload.cmd) || JSON.stringify(p.payload)),
      el("button", {
        class: "mini",
        style: "margin-left:8px",
        onclick: async () => {
          await call(() => bridge.apiPost("pending/cancel", { id: p.id }));
          renderPlans();
        },
      }, "取消"),
    ]),
  );
  view.replaceChildren(
    el("div", { class: "card wide", style: "margin-bottom:12px" }, [
      el("h3", {}, "排期（在控制台 bot 用 /plan 创建，如 /plan 20:00 /act 提醒他……）"),
    ]),
    ...nodes,
  );
  if (!items.length) view.append("（没有排期）");
}

/* ================= 路由 ================= */
const routes = {
  overview: renderOverview,
  persona: renderPersona,
  diary: renderDiary,
  memory: renderMemory,
  chat: renderChat,
  events: renderEvents,
  plans: renderPlans,
};

document.getElementById("tabs").addEventListener("click", async (e) => {
  const btn = e.target.closest("button[data-tab]");
  if (!btn) return;
  document.querySelectorAll("#tabs button").forEach((b) => b.classList.toggle("active", b === btn));
  view.textContent = "加载中…";
  await routes[btn.dataset.tab]();
});

await bridge.ready();
await renderOverview();
