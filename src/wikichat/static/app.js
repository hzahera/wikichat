// WikiChat front end: streaming chat, citations, saved conversations, themes, branding.
(() => {
  const $ = (id) => document.getElementById(id);
  const els = {
    messages: $("messages"), welcome: $("welcome"), chat: $("chat"), input: $("input"),
    form: $("composer"), send: $("sendBtn"), history: $("history"), title: $("chatTitle"),
    sidebar: $("sidebar"),
  };
  // Saved chats stay in this browser only (localStorage). Nothing is stored on the server.
  const store = {
    get(k, d) { try { return JSON.parse(localStorage.getItem(k)) ?? d; } catch { return d; } },
    set(k, v) { try { localStorage.setItem(k, JSON.stringify(v)); } catch { /* storage off */ } },
  };
  const ICON_SEND = `<svg viewBox="0 0 24 24" width="18" height="18" aria-hidden="true"><path d="M5 12h14M13 6l6 6-6 6" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"/></svg>`;
  const ICON_STOP = `<svg viewBox="0 0 24 24" width="16" height="16" aria-hidden="true"><rect x="6" y="6" width="12" height="12" rx="2" fill="currentColor"/></svg>`;

  let chats = store.get("wikichat.chats", []);
  let current = null;      // {id, title, messages:[{role, content, sources}]}
  let controller = null;   // AbortController for the running request
  let botName = "Wiki Assistant";

  // -------------------------------------------------------------- helpers
  const escapeHtml = (s) => String(s ?? "").replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  // Only http(s) links are ever rendered, so a strange URL can never run code.
  const safeUrl = (u) => {
    try { const url = new URL(u, location.href); return /^https?:$/.test(url.protocol) ? url.href : "#"; }
    catch { return "#"; }
  };
  const isColor = (c) => /^#[0-9a-f]{3,8}$/i.test(c || "");

  // ---------------------------------------------------------------- theme
  const applyTheme = (t) => { document.documentElement.dataset.theme = t; store.set("wikichat.theme", t); };
  applyTheme(store.get("wikichat.theme",
    matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light"));
  $("themeBtn").onclick = () =>
    applyTheme(document.documentElement.dataset.theme === "dark" ? "light" : "dark");
  $("menuBtn").onclick = () => els.sidebar.classList.toggle("open");

  // ------------------------------------------------- branding and status
  function applyBranding(s) {
    botName = s.bot_name || botName;
    $("botName").textContent = botName;
    document.title = botName;
    $("brandSub").textContent = s.org_name ? `${s.org_name} wiki` : "Ask the wiki";
    if (s.wiki_url) $("wikiLink").href = safeUrl(s.wiki_url);
    if (isColor(s.accent_color)) document.documentElement.style.setProperty("--accent", s.accent_color);
    if (s.welcome_title) $("welcomeTitle").textContent = s.welcome_title;
    if (s.welcome_text) $("welcomeText").textContent = s.welcome_text;

    const org = $("orgLink");
    if (s.org_url || s.org_name) {
      org.hidden = false;
      if (s.org_url) org.href = safeUrl(s.org_url); else org.removeAttribute("href");
      org.title = s.org_name ? `${s.org_name} website` : "Website";
      if (s.org_logo) {
        const img = $("orgLogo");
        img.src = s.org_logo; img.alt = s.org_name || ""; img.hidden = false;
        $("orgName").textContent = "";
      } else {
        const host = s.org_url ? new URL(safeUrl(s.org_url)).hostname.replace(/^www\./, "") : "";
        $("orgName").textContent = [s.org_name, host].filter(Boolean).join(" · ");
      }
    }

    const box = $("suggestions");
    box.innerHTML = "";
    for (const q of s.suggestions || []) {
      const b = document.createElement("button");
      b.type = "button";
      b.textContent = q;
      b.onclick = () => ask(q);
      box.appendChild(b);
    }
  }

  fetch("/api/status").then((r) => r.json()).then((s) => {
    applyBranding(s);
    if (s.no_index) {
      $("statusDot").className = "dot err";
      $("statusText").textContent = "No index yet";
      $("statusMeta").textContent = "Run: wikichat crawl";
      return;
    }
    $("statusDot").className = "dot ok";
    $("statusText").textContent = s.llm ? `Online · ${s.llm}` : "Online · search only";
    $("statusMeta").textContent =
      `${(s.pages ?? 0).toLocaleString()} pages` + (s.built_at ? ` · updated ${s.built_at}` : "");
  }).catch(() => {
    $("statusDot").className = "dot err";
    $("statusText").textContent = "Server not reachable";
  });

  // ------------------------------------------------------------ rendering
  function renderMarkdown(text, sources) {
    let html = window.marked ? marked.parse(text, { breaks: true }) : `<p>${escapeHtml(text)}</p>`;
    // [1] or [1][2] -> clickable citation chips
    html = html.replace(/\[(\d{1,2})\](?![(:])/g, (m, n) => {
      const s = (sources || []).find((x) => x.n === Number(n));
      return s ? `<a class="cite" href="${escapeHtml(safeUrl(s.url))}" target="_blank" rel="noopener noreferrer" data-n="${n}" title="${escapeHtml(s.title)}">${n}</a>` : m;
    });
    if (!window.DOMPurify) return `<p>${escapeHtml(text)}</p>`;  // never render unsanitized HTML
    return DOMPurify.sanitize(html, { ADD_ATTR: ["target", "data-n"], FORBID_TAGS: ["style", "img", "form", "input"] });
  }

  function addUser(text) {
    const el = document.createElement("div");
    el.className = "msg user";
    el.innerHTML = `<div class="bubble"></div>`;
    el.querySelector(".bubble").textContent = text;
    els.messages.appendChild(el);
  }

  function addBot() {
    const el = document.createElement("div");
    el.className = "msg bot";
    el.innerHTML = `
      <img class="avatar" src="/static/brand/logo.svg" alt="">
      <div class="body">
        <div class="bubble"><div class="typing" aria-label="Thinking"><span></span><span></span><span></span></div></div>
        <div class="sources" hidden></div>
        <div class="actions" hidden><button type="button" data-a="copy">Copy</button><button type="button" data-a="retry">Retry</button></div>
      </div>`;
    els.messages.appendChild(el);
    return el;
  }

  function renderSources(el, sources) {
    const box = el.querySelector(".sources");
    if (!sources?.length) return;
    box.hidden = false;
    box.innerHTML = `
      <button type="button" class="sources-toggle" aria-expanded="true">
        <svg width="14" height="14" viewBox="0 0 24 24" aria-hidden="true"><path d="M4 6h16M4 12h16M4 18h10" stroke="currentColor" stroke-width="2" stroke-linecap="round"/></svg>
        ${sources.length} ${sources.length === 1 ? "source" : "sources"} from the wiki
      </button>
      <div class="source-list">${sources.map((s) => `
        <a class="source" href="${escapeHtml(safeUrl(s.url))}" target="_blank" rel="noopener noreferrer" data-n="${Number(s.n)}">
          <div class="source-head"><span class="n">${Number(s.n)}</span>${escapeHtml(s.title)}</div>
          ${s.section ? `<div class="source-sec">${escapeHtml(s.section)}</div>` : ""}
          <div class="source-snippet">${escapeHtml(s.snippet || "")}</div>
        </a>`).join("")}
      </div>`;
    const toggle = box.querySelector(".sources-toggle");
    toggle.onclick = () => {
      const list = box.querySelector(".source-list");
      list.hidden = !list.hidden;
      toggle.setAttribute("aria-expanded", String(!list.hidden));
    };
  }

  function wireBot(el, msg, question) {
    const actions = el.querySelector(".actions");
    actions.hidden = false;
    actions.querySelector('[data-a="copy"]').onclick = (e) => {
      navigator.clipboard?.writeText(msg.content);
      e.target.textContent = "Copied";
      setTimeout(() => (e.target.textContent = "Copy"), 1200);
    };
    actions.querySelector('[data-a="retry"]').onclick = () => {
      current.messages.splice(-2, 2);
      el.previousElementSibling?.remove();
      el.remove();
      ask(question);
    };
    // hovering a citation highlights the matching source card
    el.querySelectorAll(".cite").forEach((c) => {
      const card = el.querySelector(`.source[data-n="${c.dataset.n}"]`);
      c.onmouseenter = () => card?.classList.add("flash");
      c.onmouseleave = () => card?.classList.remove("flash");
    });
  }

  const scrollDown = () => { els.chat.scrollTop = els.chat.scrollHeight; };

  // ----------------------------------------------------------------- chat
  async function ask(question) {
    if (controller) return;
    if (!current) newChat(false);
    els.welcome.hidden = true;
    addUser(question);
    const history = current.messages.slice(-20).map(({ role, content }) => ({ role, content: content.slice(0, 8000) }));
    current.messages.push({ role: "user", content: question });
    if (current.messages.length === 1) current.title = question.slice(0, 60);
    els.title.textContent = current.title;

    const el = addBot();
    const bubble = el.querySelector(".bubble");
    const msg = { role: "assistant", content: "", sources: [] };
    scrollDown();
    setBusy(true);

    try {
      controller = new AbortController();
      const res = await fetch("/api/chat", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ message: question, history }),
        signal: controller.signal,
      });
      if (!res.ok) {
        const detail = (await res.json().catch(() => ({}))).detail;
        throw new Error(typeof detail === "string" ? detail : `Request failed (HTTP ${res.status})`);
      }

      const reader = res.body.getReader();
      const decoder = new TextDecoder();
      let buf = "";
      while (true) {
        const { value, done } = await reader.read();
        if (done) break;
        buf += decoder.decode(value, { stream: true });
        const events = buf.split("\n\n");
        buf = events.pop();
        for (const block of events) {
          const ev = /^event: (.*)$/m.exec(block)?.[1];
          const raw = /^data: (.*)$/m.exec(block)?.[1];
          if (!ev || raw === undefined) continue;
          const data = JSON.parse(raw);
          if (ev === "sources") { msg.sources = data; renderSources(el, data); }
          else if (ev === "token") { msg.content += data; bubble.innerHTML = renderMarkdown(msg.content, msg.sources); scrollDown(); }
          else if (ev === "error") { msg.content += `\n\n**Error:** ${data}`; bubble.innerHTML = renderMarkdown(msg.content, msg.sources); }
        }
      }
    } catch (err) {
      if (err.name !== "AbortError") msg.content += `\n\n**Error:** ${err.message}`;
    } finally {
      if (!msg.content) msg.content = "_Stopped._";
      bubble.innerHTML = renderMarkdown(msg.content, msg.sources);
      current.messages.push(msg);
      wireBot(el, msg, question);
      saveChats();
      setBusy(false);
      scrollDown();
    }
  }

  function setBusy(busy) {
    els.send.classList.toggle("stop", busy);
    els.send.innerHTML = busy ? ICON_STOP : ICON_SEND;
    els.send.setAttribute("aria-label", busy ? "Stop" : "Send");
    if (!busy) controller = null;
  }

  // ---------------------------------------------------------- saved chats
  function saveChats() {
    if (current && !chats.includes(current)) chats.unshift(current);
    chats = chats.slice(0, 30);
    store.set("wikichat.chats", chats);
    renderHistory();
  }

  function renderHistory() {
    els.history.innerHTML = chats.length ? "" : `<li class="empty">No conversations yet</li>`;
    for (const c of chats) {
      const li = document.createElement("li");
      li.className = c === current ? "active" : "";
      li.innerHTML = `<span></span><button type="button" class="del" title="Delete" aria-label="Delete conversation">✕</button>`;
      li.querySelector("span").textContent = c.title || "Untitled";
      li.onclick = () => openChat(c);
      li.querySelector(".del").onclick = (e) => {
        e.stopPropagation();
        chats = chats.filter((x) => x !== c);
        store.set("wikichat.chats", chats);
        if (c === current) newChat();
        renderHistory();
      };
      els.history.appendChild(li);
    }
  }

  function openChat(c) {
    current = c;
    els.messages.innerHTML = "";
    els.welcome.hidden = c.messages.length > 0;
    els.title.textContent = c.title || "New conversation";
    let lastQ = "";
    for (const m of c.messages) {
      if (m.role === "user") { addUser(m.content); lastQ = m.content; continue; }
      const el = addBot();
      el.querySelector(".bubble").innerHTML = renderMarkdown(m.content, m.sources);
      renderSources(el, m.sources);
      wireBot(el, m, lastQ);
    }
    renderHistory();
    els.sidebar.classList.remove("open");
    scrollDown();
  }

  function newChat(render = true) {
    current = { id: Date.now(), title: "", messages: [] };
    if (render) openChat(current);
  }

  // --------------------------------------------------------------- inputs
  els.form.onsubmit = (e) => {
    e.preventDefault();
    if (controller) { controller.abort(); return; }
    const q = els.input.value.trim();
    if (!q) return;
    els.input.value = "";
    autosize();
    ask(q);
  };
  els.input.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey && !e.isComposing) { e.preventDefault(); els.form.requestSubmit(); }
  });
  const autosize = () => { els.input.style.height = "auto"; els.input.style.height = els.input.scrollHeight + "px"; };
  els.input.addEventListener("input", autosize);
  $("newChat").onclick = () => newChat();

  setBusy(false);
  newChat();
  renderHistory();
  els.input.focus();
})();
