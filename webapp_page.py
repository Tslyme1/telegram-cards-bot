"""The Mini App page, served as one self-contained document."""

PAGE = """<!doctype html>
<html lang="ru">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<title>Карточки</title>
<script src="https://telegram.org/js/telegram-web-app.js"></script>
<style>
  :root { color-scheme: light dark; }
  body {
    margin: 0;
    padding: 16px;
    font: 16px/1.4 -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
    background: var(--tg-theme-bg-color, #fff);
    color: var(--tg-theme-text-color, #000);
  }
  h1 { font-size: 17px; margin: 0 0 4px; }
  .hint { color: var(--tg-theme-hint-color, #888); font-size: 14px; margin: 0 0 16px; }
  button {
    display: block; width: 100%; margin: 0 0 8px; padding: 13px 14px;
    font: inherit; text-align: left; border: none; border-radius: 10px;
    background: var(--tg-theme-secondary-bg-color, #f1f1f4);
    color: var(--tg-theme-text-color, #000); cursor: pointer;
  }
  button:active { opacity: .6; }
  button.primary {
    text-align: center; font-weight: 600;
    background: var(--tg-theme-button-color, #2481cc);
    color: var(--tg-theme-button-text-color, #fff);
  }
  button:disabled { opacity: .4; }
  input {
    width: 100%; box-sizing: border-box; margin: 0 0 12px; padding: 13px 14px;
    font: inherit; border-radius: 10px; border: none;
    background: var(--tg-theme-secondary-bg-color, #f1f1f4);
    color: var(--tg-theme-text-color, #000);
  }
  .reels {
    display: flex; gap: 8px; justify-content: center;
    font-size: 40px; margin: 20px 0; letter-spacing: 2px;
  }
  .symbols { display: flex; gap: 8px; }
  .symbols button { font-size: 32px; text-align: center; padding: 14px 0; }
  .status { text-align: center; padding: 32px 0; }
</style>
</head>
<body>
<div id="app" class="status">Загрузка…</div>
<script>
const tg = window.Telegram.WebApp;
tg.expand();
tg.ready();

const app = document.getElementById("app");
let session = null;
let target = null;
let picked = [];

function el(tag, props = {}, ...kids) {
  const node = Object.assign(document.createElement(tag), props);
  kids.forEach(k => node.append(k));
  return node;
}

function show(...nodes) {
  app.className = "";
  app.replaceChildren(...nodes);
}

function status(text) {
  app.className = "status";
  app.replaceChildren(text);
}

async function api(path, body = {}) {
  const res = await fetch(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ initData: tg.initData, ...body }),
  });
  return res.json();
}

function done(message) {
  status(message);
  setTimeout(() => tg.close(), 1400);
}

function chooseTarget() {
  const buttons = session.participants.map(p =>
    el("button", { textContent: p.name, onclick: () => { target = p; nextStep(); } })
  );
  show(
    el("h1", { textContent: session.title }),
    el("p", { className: "hint", textContent: session.chat_title }),
    ...buttons
  );
}

function nextStep() {
  return session.kind === "casino" ? spin() : askReason();
}

function askReason() {
  const field = el("input", { placeholder: "Причина (необязательно)", maxLength: 200 });
  const send = el("button", { className: "primary", textContent: "Выдать" });
  send.onclick = async () => {
    send.disabled = true;
    const res = await api("/app/act", { target_id: target.id, reason: field.value.trim() });
    done(res.ok ? res.message : (res.error || "Что-то пошло не так"));
  };
  show(
    el("h1", { textContent: `Кому: ${target.name}` }),
    el("p", { className: "hint", textContent: session.title }),
    field,
    send
  );
}

function spin() {
  const reels = el("div", { className: "reels" });
  reels.textContent = picked.concat(Array(session.slots - picked.length).fill("⬜")).join(" ");

  const row = el("div", { className: "symbols" });
  session.symbols.forEach(symbol => {
    row.append(el("button", {
      textContent: symbol,
      onclick: async () => {
        picked.push(symbol);
        if (picked.length < session.slots) return spin();
        status("Крутим…");
        const res = await api("/app/act", { target_id: target.id, picked });
        done(res.ok ? res.message : (res.error || "Что-то пошло не так"));
      },
    }));
  });

  show(
    el("h1", { textContent: `🎰 Казино на ${target.name}` }),
    el("p", { className: "hint", textContent: `Символ ${picked.length + 1} из ${session.slots}` }),
    reels,
    row
  );
}

(async () => {
  const res = await api("/app/data");
  if (!res.ok) return status(res.error || "Сессия не найдена. Вызовите команду заново.");
  session = res;
  if (session.target) {
    target = session.target;
    nextStep();
  } else {
    chooseTarget();
  }
})();
</script>
</body>
</html>
"""
