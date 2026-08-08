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

// 出错时把原因摊在页面上——一闪而过的 toast 看不清，
// 而后端已经把异常类型和调用栈尾巴带回来了
function showError(where, e) {
  const msg = (e && e.message) || String(e);
  view.replaceChildren(
    el("div", { class: "card wide" }, [
      el("h3", {}, `${where} 打不开`),
      el("pre", { style: "white-space:pre-wrap;margin:0;line-height:1.6" }, msg),
      el("div", { class: "meta", style: "margin-top:8px" },
        "完整堆栈在 AstrBot 日志里搜 [AstrLover]。"),
      el("button", { class: "ghost", style: "margin-top:8px", onclick: () => location.reload() }, "重试"),
    ]),
  );
}

async function call(fn) {
  try {
    return await fn();
  } catch (e) {
    toast("出错：" + ((e && e.message) || e));
    throw e;
  }
}

/* ================= 总览 ================= */
async function renderOverview() {
  const d = await call(() => bridge.apiGet("overview"));
  // 光一个 ❌ 没法排查，把原因跟在后面
  const healthRow = (h) =>
    el("div", { style: "margin-bottom:4px" }, [
      el("span", { class: h.ok ? "ok" : "bad" }, `${h.ok ? "✅" : "❌"} ${h.name}`),
      el("span", { class: "meta" }, `　${h.why || ""}`),
    ]);
  const sched = (d.schedule || [])
    .map((s) => `${s.start_hm}~${s.end_hm} ${s.activity}（${s.status}）`)
    .join("\n") || "（今天还没生成日程）";
  view.replaceChildren(
    el("div", { class: "cards" }, [
      el("div", { class: "card" }, [
        el("h3", {}, "她"),
        el("div", { class: "big" },
          `人设：${d.persona_ok ? "已读到（在 AstrBot 人格设定里）" : "没读到——先绑定对话并给它设人格"}\n${d.now || ""}`),
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
        el("div", { class: "big" }, (d.health || []).map(healthRow)),
      ]),
      el("div", { class: "card wide" }, [el("h3", {}, "今日日程"), el("div", { class: "big" }, sched)]),
      el("div", { class: "card wide" }, [
        el("h3", {}, "数据主权"),
        el("button", {
          class: "ghost",
          onclick: async () => {
            toast("正在打包…");
            await call(() => bridge.download("export"));
          },
        }, "导出生命参数与记忆包"),
      ]),
    ]),
  );
}

/* ================= 记录 ================= */
async function renderRecords() {
  const kinds = (await call(() => bridge.apiGet("records/kinds"))).kinds || [];
  let current = renderRecords._kind || "f";

  const tabs = el("div", { class: "toolbar" });
  const list = el("div");

  const mutate = async (payload) => {
    const r = await call(() => bridge.apiPost("records/mutate", payload));
    toast(r.message || "已处理");
    await load();
  };

  const card = (row) => {
    const chips = (row.chips || []).filter(Boolean).map((c) => el("span", { class: "tag" }, c));
    const body = row.multiline
      ? el("textarea", { style: "min-height:90px" })
      : el("input", { type: "text", style: "width:100%" });
    body.value = row.body || "";
    body.disabled = !row.editable;

    const ops = [];
    if (row.editable) {
      ops.push(el("button", {
        class: "mini",
        onclick: () => mutate({ op: "edit", rid: row.rid, text: body.value }),
      }, "保存"));
    }
    if (row.deletable) {
      ops.push(el("button", {
        class: "mini",
        onclick: () => {
          if (confirm(`删除 ${row.rid}？`)) mutate({ op: "del", rid: row.rid });
        },
      }, "删除"));
    }
    return el("div", { class: "card", style: "margin-bottom:8px" }, [
      el("div", { class: "meta", style: "margin-bottom:4px" },
        [el("span", { class: "tag" }, row.rid), ...chips]),
      body,
      el("div", { class: "form-row", style: "margin:6px 0 0" },
        [el("span", { class: "meta", style: "flex:1" }, row.meta || ""), ...ops]),
    ]);
  };

  // 能手动加的只有这四类，各自的格式要求也不一样；
  // 其余几类是她自己产生的，加不了，说清楚为什么比藏起来强
  const ADDABLE = {
    f: "他不吃香菜（写 self 开头则记她自己：self 最近在追一部剧）",
    e: "今天下午去了趟花市",
    m: "2026-04-20 认识的日子 since",
    s: "14:00-16:00 和小雅逛街",
  };
  const NOT_ADDABLE = {
    d: "日记是她自己写的，加不了——但可以改和删。",
    p: "排期是她自己排的，加不了——但可以改和删。",
    o: "情绪由聊天自然产生，会自己衰减，加不了。",
    state: "状态只有固定几项，改就行，不新增。",
  };

  const addBar = el("div", { class: "toolbar" });

  const renderAddBar = () => {
    const hint = ADDABLE[current];
    if (!hint) {
      addBar.replaceChildren(
        el("span", { class: "meta" }, NOT_ADDABLE[current] || ""),
      );
      return;
    }
    const label = (kinds.find((k) => k.key === current) || {}).label || "";
    const addText = el("input", {
      type: "text", style: "flex:1;min-width:260px",
      placeholder: `新${label}，例如：${hint}`,
    });
    const submit = async () => {
      if (!addText.value.trim()) return toast("内容是空的");
      await mutate({ op: "add", kind: current, text: addText.value });
      // 不用清空：mutate 成功会重新 load，输入框整个换成新的
    };
    addText.addEventListener("keydown", (e) => {
      if (e.key === "Enter") submit();
    });
    addBar.replaceChildren(
      addText,
      el("button", { class: "action", onclick: submit }, `添加${label}`),
    );
  };

  const load = async () => {
    const d = await call(() => bridge.apiGet("records", { kind: current, limit: 60 }));
    const rows = d.rows || [];
    list.replaceChildren(...rows.map(card));
    if (!rows.length) list.textContent = "（这一类还没有记录）";
    for (const b of tabs.querySelectorAll("button[data-kind]")) {
      b.className = b.dataset.kind === current ? "action" : "ghost";
    }
    renderAddBar();
  };

  for (const k of kinds) {
    tabs.append(el("button", {
      class: "ghost", "data-kind": k.key,
      onclick: () => { current = renderRecords._kind = k.key; load(); },
    }, k.label));
  }

  view.replaceChildren(tabs, addBar, list);
  await load();
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
    if (group === "轻量模型") {
      blocks.push(el("div", { class: "toolbar" }, [
        el("button", {
          class: "ghost",
          onclick: async () => {
            toast("正在测…");
            const r = await call(() => bridge.apiPost("probe", { what: "light" }));
            alert(r.message);
          },
        }, "测一下轻量模型"),
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

/* ================= 路由 ================= */
const routes = {
  overview: renderOverview,
  records: renderRecords,
  settings: renderSettings,
};

document.getElementById("tabs").addEventListener("click", async (e) => {
  const btn = e.target.closest("button[data-tab]");
  if (!btn) return;
  document.querySelectorAll("#tabs button").forEach((b) => b.classList.toggle("active", b === btn));
  view.textContent = "加载中…";
  try {
    await routes[btn.dataset.tab]();
  } catch (err) {
    showError(btn.textContent, err);
  }
});

await bridge.ready();
try {
  await renderOverview();
} catch (err) {
  showError("总览", err);
}
