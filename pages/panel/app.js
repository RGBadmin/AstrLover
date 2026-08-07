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

/* ================= 设置 ================= */
async function renderSettings() {
  const d = await call(() => bridge.apiGet("settings"));
  const items = d.items || [];
  const dirty = new Map();

  const control = (it) => {
    if (it.type === "bool") {
      const box = el("input", { type: "checkbox" });
      box.checked = !!it.value;
      box.addEventListener("change", () => dirty.set(it.key, box.checked));
      return box;
    }
    if (it.options && it.options.length) {
      const sel = el("select", {});
      for (const o of it.options) sel.append(el("option", { value: o }, o));
      sel.value = String(it.value ?? "");
      sel.addEventListener("change", () => dirty.set(it.key, sel.value));
      return sel;
    }
    if (it.type === "text") {
      const ta = el("textarea", { style: "min-height:120px" });
      ta.value = String(it.value ?? "");
      ta.addEventListener("input", () => dirty.set(it.key, ta.value));
      return ta;
    }
    const inp = el("input", {
      type: it.type === "int" ? "number" : "text",
      style: it.type === "int" ? "min-width:110px" : "",
    });
    inp.value = Array.isArray(it.value) ? it.value.join(", ") : String(it.value ?? "");
    inp.addEventListener("input", () => dirty.set(it.key, inp.value));
    return inp;
  };

  const save = async () => {
    if (!dirty.size) return toast("没有改动");
    const values = Object.fromEntries(dirty);
    const r = await call(() => bridge.apiPost("settings/save", { values }));
    dirty.clear();
    toast(r.message || "已保存");
    renderSettings();
  };

  const blocks = [];
  for (const group of d.groups || []) {
    const rows = items.filter((it) => it.group === group);
    if (!rows.length) continue;
    blocks.push(el("h3", { style: "margin:18px 0 6px" }, group));
    for (const it of rows) {
      blocks.push(el("div", { class: "card", style: "margin-bottom:8px" }, [
        el("div", { class: "form-row" }, [
          el("label", { style: "min-width:170px" },
            it.label + (it.modified ? " ●" : "")),
          control(it),
          el("button", {
            class: "mini",
            onclick: async () => {
              await call(() => bridge.apiPost("settings/save", { reset: it.key }));
              renderSettings();
            },
          }, "恢复默认"),
        ]),
        it.hint ? el("div", { class: "meta" }, it.hint) : "",
      ]));
    }
    if (group === "视觉解析") {
      blocks.push(el("div", { class: "toolbar" }, [
        el("button", {
          class: "ghost",
          onclick: async () => {
            toast("正在测…");
            const r = await call(() => bridge.apiPost("probe", { what: "vision" }));
            alert(r.message);
          },
        }, "测一下视觉 API"),
      ]));
    }
    if (group === "相册") {
      blocks.push(el("div", { class: "toolbar" }, [
        el("button", {
          class: "ghost",
          onclick: async () => {
            toast("正在测…");
            const r = await call(() => bridge.apiPost("probe", { what: "embed" }));
            alert(r.message);
          },
        }, "测一下向量区分度"),
      ]));
    }
  }

  view.replaceChildren(
    el("div", { class: "toolbar", style: "position:sticky;top:52px;background:var(--bg);z-index:3;padding:8px 0" }, [
      el("button", { class: "action", onclick: save }, "保存"),
      el("span", { class: "meta" }, "改完即时生效，不用重载插件。● = 已改过默认值。接线（恋人 id / 控制台 / Provider）在 AstrBot 插件配置页。"),
    ]),
    ...blocks,
  );
}

/* ================= 记录 ================= */
const REC_KINDS = [
  ["f", "事实"], ["e", "事件"], ["s", "日程"], ["m", "纪念日"],
  ["p", "排期"], ["o", "情绪"], ["d", "日记"],
];

async function renderRecords() {
  const sel = el("select", {});
  for (const [v, label] of REC_KINDS) sel.append(el("option", { value: v }, label));
  const body = el("pre", { class: "list-item", style: "white-space:pre-wrap" });
  const addKind = el("select", {});
  for (const [v, label] of [["f", "事实"], ["e", "事件"], ["m", "纪念日"], ["s", "日程"]]) {
    addKind.append(el("option", { value: v }, label));
  }
  const addText = el("input", { type: "text", placeholder: "内容（纪念日：2026-04-20 认识的日子 since）" });
  const ridInput = el("input", { type: "text", placeholder: "编号 如 f12", style: "min-width:110px" });
  const editText = el("input", { type: "text", placeholder: "改成什么（留空则删除）" });

  const load = async () => {
    const d = await call(() => bridge.apiGet("records", { kind: sel.value, limit: 60 }));
    body.textContent = d.text || "（空）";
  };
  const mutate = async (payload) => {
    const r = await call(() => bridge.apiPost("records/mutate", payload));
    toast(r.message || "已处理");
    load();
  };

  sel.addEventListener("change", load);
  view.replaceChildren(
    el("div", { class: "toolbar" }, [
      sel,
      el("button", { class: "ghost", onclick: load }, "刷新"),
      el("span", { class: "meta" }, "她是谁写在 AstrBot 人格里；这里是随时间生长的记录。"),
    ]),
    el("div", { class: "toolbar" }, [
      addKind, addText,
      el("button", {
        class: "action",
        onclick: () => mutate({ op: "add", kind: addKind.value, text: addText.value }),
      }, "添加"),
    ]),
    el("div", { class: "toolbar" }, [
      ridInput, editText,
      el("button", {
        class: "ghost",
        onclick: () => mutate(
          editText.value.trim()
            ? { op: "edit", rid: ridInput.value, text: editText.value }
            : { op: "del", rid: ridInput.value },
        ),
      }, "改 / 删"),
    ]),
    body,
  );
  await load();
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
  records: renderRecords,
  settings: renderSettings,
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
