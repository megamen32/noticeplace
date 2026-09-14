const fs = require("fs");
const http = require("http");

function request(method, path) {
  return new Promise((resolve, reject) => {
    const call = http.request({ host: "127.0.0.1", port: 9222, path, method }, response => {
      let body = "";
      response.on("data", chunk => body += chunk);
      response.on("end", () => resolve(JSON.parse(body)));
    });
    call.on("error", reject);
    call.end();
  });
}

async function main() {
  const cookie = fs.readFileSync(0, "utf8").trim();
  if (!cookie) throw new Error("missing acceptance cookie");
  const target = await request("PUT", "/json/new?" + encodeURIComponent("https://notify.bezrabotnyi.com/admin/"));
  const socket = new WebSocket(target.webSocketDebuggerUrl);
  const pending = new Map();
  let sequence = 0;
  socket.onmessage = async event => {
    const raw = typeof event.data === "string" ? event.data : await event.data.text();
    const message = JSON.parse(raw);
    if (message.id && pending.has(message.id)) {
      pending.get(message.id)(message);
      pending.delete(message.id);
    }
  };
  await Promise.race([
    new Promise((resolve, reject) => { socket.onopen = resolve; socket.onerror = reject; }),
    new Promise((_, reject) => setTimeout(() => reject(new Error("CDP websocket timeout")), 5000)),
  ]);
  const call = (method, params = {}) => Promise.race([new Promise(resolve => {
    const id = ++sequence;
    pending.set(id, resolve);
    socket.send(JSON.stringify({ id, method, params }));
  }), new Promise((_, reject) => setTimeout(() => reject(new Error(`CDP ${method} timeout`)), 5000))]);
  await call("Network.enable");
  await call("Network.setCookie", {
    name: "gptadmin_auth", value: cookie, domain: ".bezrabotnyi.com", path: "/",
    secure: true, httpOnly: true, sameSite: "Lax", url: "https://notify.bezrabotnyi.com/",
  });
  await call("Page.enable");
  await call("Page.navigate", { url: "https://notify.bezrabotnyi.com/admin/" });
  await new Promise(resolve => setTimeout(resolve, 2500));
  const result = await call("Runtime.evaluate", {
    expression: `JSON.stringify({
      url: location.href,
      title: document.title,
      hasHealth: document.body.innerText.includes("Health dashboard"),
      targets: document.querySelectorAll("#health-dashboard table:first-of-type tr").length - 1,
      incidentRows: document.querySelectorAll("#health-dashboard table:nth-of-type(2) tr").length - 1
    })`,
    returnByValue: true,
  });
  const evidence = JSON.parse(result.result.result.value);
  if (!evidence.hasHealth || evidence.targets !== 7) throw new Error(`unexpected dashboard: ${JSON.stringify(evidence)}`);
  const screenshot = await call("Page.captureScreenshot", { format: "png", captureBeyondViewport: false });
  fs.writeFileSync("/Users/user/health-dashboard-live.png", Buffer.from(screenshot.result.data, "base64"));
  console.log(JSON.stringify({ ...evidence, screenshot: "/Users/user/health-dashboard-live.png" }));
  socket.close();
}

main().catch(error => { console.error(error.message); process.exit(1); });
