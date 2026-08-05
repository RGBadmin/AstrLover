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
          el("span", { class: "meta" }, `　能力：${(d.capabilities || []).join("、") || "无"}　待打标：${d.pending_tags}`),
        ]),
      ]),
      el("div", { class: "card wide" }, [el("h3", {}, "今日日程"), el("div", { class: "big" }, sched)]),
      el("div", { class: "card wide" }, [
        el("h3", {}, "数据主权"),
        el("button", {
          class: "ghost",
          onclick: async () => {
            toast("正在打包（含图库，可能较大）…");
            await call(() => bridge.download("export", { gallery: 1 }));
          },
        }, "导出档案与记忆包"),
      ]),
    ]),
  );
}

/* ================= 档案 ================= */
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
      }, "保存生命档案"),
      el("span", { class: "meta" }, "静态基线（她是谁）。下方为系统演化的动态层（只读）。"),
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

/* ================= 图库 ================= */
async function renderGallery() {
  const catSel = el("select", {}, []);
  for (const [v, label] of [["", "全部分类"], ["selfie", "自拍"], ["life", "生活照"], ["scene", "场景图"], ["sticker", "表情包"]]) {
    catSel.append(el("option", { value: v }, label));
  }
  const stSel = el("select", {}, []);
  for (const [v, label] of [["", "全部状态"], ["ok", "已打标"], ["pending", "待打标"], ["failed", "失败"]]) {
    stSel.append(el("option", { value: v }, label));
  }
  const grid = el("div", { class: "grid" });
  const fileInput = el("input", { type: "file", accept: "image/*", multiple: "multiple", style: "display:none" });

  const load = async () => {
    const d = await call(() =>
      bridge.apiGet("gallery/list", { category: catSel.value, status: stSel.value, limit: 60 }),
    );
    grid.replaceChildren(
      ...(d.items || []).map((it) => {
        const ops = el("div", { class: "ops" }, [
          el("button", {
            class: "mini",
            onclick: async () => {
              await call(() => bridge.apiPost("gallery/update", { id: it.id, op: "anchor", value: !it.is_anchor }));
              load();
            },
          }, it.is_anchor ? "取消锚点" : "设为锚点"),
          el("button", {
            class: "mini",
            onclick: async () => {
              await call(() => bridge.apiPost("gallery/update", { id: it.id, op: "retag" }));
              toast("已加入重打标队列");
            },
          }, "重打标"),
          el("button", {
            class: "mini",
            onclick: async () => {
              if (!confirm("删除这张图？")) return;
              await call(() => bridge.apiPost("gallery/update", { id: it.id, op: "delete" }));
              load();
            },
          }, "删除"),
        ]);
        return el("div", { class: "thumb" + (it.is_anchor ? " anchor" : "") }, [
          it.thumb ? el("img", { src: it.thumb }) : el("div", { class: "ph" }, it.status),
          el("div", { class: "info" }, `#${it.id} ${it.category}/${it.status}\n${(it.desc || "").slice(0, 40)}`),
          ops,
        ]);
      }),
    );
    if (!d.items || !d.items.length) grid.textContent = "（没有图片）";
  };

  fileInput.addEventListener("change", async () => {
    for (const f of fileInput.files) {
      await call(() => bridge.upload("gallery/upload", f));
    }
    toast(`已上传 ${fileInput.files.length} 张，等待打标`);
    fileInput.value = "";
    load();
  });

  view.replaceChildren(
    el("div", { class: "toolbar" }, [
      catSel, stSel,
      el("button", { class: "ghost", onclick: load }, "刷新"),
      el("button", { class: "ghost", onclick: () => fileInput.click() }, "上传图片"),
      el("button", {
        class: "ghost",
        onclick: async () => {
          const r = await call(() => bridge.apiPost("gallery/scan", {}));
          toast(`扫描完成，新增 ${r.added} 张`);
          load();
        },
      }, "扫描目录"),
      el("button", {
        class: "action",
        onclick: async () => {
          await call(() => bridge.apiPost("gallery/tagall", {}));
          toast("全量打标已在后台开始");
        },
      }, "全量打标"),
      fileInput,
    ]),
    grid,
  );
  catSel.addEventListener("change", load);
  stSel.addEventListener("change", load);
  await load();
}

/* ================= 编排 ================= */
async function renderActions() {
  const kindSel = el("select", {}, []);
  for (const [v, label] of [
    ["say", "说：让她转达一件事"],
    ["voice", "语音：让她发条语音"],
    ["post", "动态：让她发条频道动态"],
    ["avatar", "头像：让她换头像"],
    ["signature", "签名：让她改签名"],
  ]) {
    kindSel.append(el("option", { value: v }, label));
  }
  const textInput = el("input", { type: "text", placeholder: "内容 / 主题 / 提示（可留空的动作可不填）" });
  const timeInput = el("input", { type: "datetime-local" });

  const pendingList = el("div");
  const loadPending = async () => {
    const d = await call(() => bridge.apiGet("pending"));
    pendingList.replaceChildren(
      ...(d.items || []).map((p) =>
        el("div", { class: "list-item" }, [
          el("span", { class: "tag" }, p.kind),
          `#${p.id}　${ts(p.due_ts)}　${JSON.stringify(p.payload)}`,
          el("button", {
            class: "mini",
            style: "margin-left:8px",
            onclick: async () => {
              await call(() => bridge.apiPost("pending/cancel", { id: p.id }));
              loadPending();
            },
          }, "取消"),
        ]),
      ),
    );
    if (!d.items || !d.items.length) pendingList.textContent = "（待办队列为空）";
  };

  view.replaceChildren(
    el("div", { class: "card wide", style: "margin-bottom:14px" }, [
      el("h3", {}, "行为编排（与她的自主行为共用同一条执行通道）"),
      el("div", { class: "form-row" }, [el("label", {}, "动作"), kindSel]),
      el("div", { class: "form-row" }, [el("label", {}, "内容"), textInput]),
      el("div", { class: "form-row" }, [el("label", {}, "定时"), timeInput, el("span", { class: "meta" }, "留空 = 立即执行")]),
      el("button", {
        class: "action",
        onclick: async () => {
          const kind = kindSel.value;
          const text = textInput.value.trim();
          const payload =
            kind === "say" || kind === "voice"
              ? { instruction: text }
              : kind === "post"
                ? { topic: text }
                : { hint: text };
          const body = { kind, payload };
          if (timeInput.value) body.due_ts = Math.floor(new Date(timeInput.value).getTime() / 1000);
          const r = await call(() => bridge.apiPost("action", body));
          toast(r.scheduled ? `已排程 #${r.scheduled}` : r.ok ? "已执行" : "执行失败（查日志）");
          textInput.value = "";
          loadPending();
        },
      }, "执行 / 排程"),
    ]),
    el("h3", {}, "待办队列"),
    pendingList,
  );
  await loadPending();
}

/* ================= 路由 ================= */
const routes = {
  overview: renderOverview,
  persona: renderPersona,
  diary: renderDiary,
  memory: renderMemory,
  chat: renderChat,
  events: renderEvents,
  gallery: renderGallery,
  actions: renderActions,
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
