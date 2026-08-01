const fs = require("fs");
const path = require("path");
const vm = require("vm");

const root = path.resolve(__dirname, "..", "design");
const html = fs.readFileSync(path.join(root, "index.html"), "utf8");
const script = html.match(/<script>\n([\s\S]*?)\n<\/script>/)[1];

function runLoader({ origin, hostname, search, initialStorage }) {
  const store = Object.assign({}, initialStorage || {});
  const context = {
    URL,
    URLSearchParams,
    location: { origin, hostname, search },
    localStorage: {
      getItem: (k) => (Object.prototype.hasOwnProperty.call(store, k) ? store[k] : null),
      setItem: (k, v) => { store[k] = String(v); },
      removeItem: (k) => { delete store[k]; },
    },
    fetch: () => Promise.resolve({ text: () => Promise.resolve("<html><head></head><body></body></html>") }),
    document: {
      body: { innerHTML: "" },
      open: () => {},
      write: () => {},
      close: () => {},
    },
  };
  vm.runInNewContext(script, context, { filename: "frontend/design/index.html" });
  return store;
}

function assert(cond, message) {
  if (!cond) throw new Error(message);
  console.log("  ✓ " + message);
}

let storage = runLoader({
  origin: "https://cwwd.mirai-dx-platform.com",
  hostname: "cwwd.mirai-dx-platform.com",
  search: "?api=https://attacker.example",
});
assert(!("cw_api" in storage), "公開ホストでは外部 ?api= を保存しない");

storage = runLoader({
  origin: "https://cwwd.mirai-dx-platform.com",
  hostname: "cwwd.mirai-dx-platform.com",
  search: "?api=https://cwwd.mirai-dx-platform.com",
});
assert(storage.cw_api === "https://cwwd.mirai-dx-platform.com", "公開ホストでは同一オリジン API のみ保存する");

storage = runLoader({
  origin: "http://192.168.0.5:34979",
  hostname: "192.168.0.5",
  search: "?api=http://192.168.0.10:55019",
});
assert(storage.cw_api === "http://192.168.0.10:55019", "開発ホストではプライベートIP API を許可する");

console.log("\nRESULT: 3 passed, 0 failed");
