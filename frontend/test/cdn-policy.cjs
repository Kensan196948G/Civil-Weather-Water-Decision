/* Verify generated frontend shell does not depend on public JS/CSS/font CDNs or default map tiles. */
const fs = require("fs");
const path = require("path");

const ROOT = path.join(__dirname, "..", "design");
const files = [
  "気象河川施工判断支援.dc.html",
  "support.js",
  "index.html",
  "data-adapter.js",
];
const cssFiles = [
  "vendor/leaflet/leaflet.css",
];
const forbidden = [
  "unpkg.com",
  "fonts.googleapis.com",
  "fonts.gstatic.com",
  "cdn.jsdelivr.net",
  "cdnjs.cloudflare.com",
  "tile.openstreetmap.org",
];

function stripCssComments(body) {
  return body.replace(/\/\*[\s\S]*?\*\//g, "");
}

let fail = 0;
for (const file of files) {
  const body = fs.readFileSync(path.join(ROOT, file), "utf8");
  for (const needle of forbidden) {
    if (body.includes(needle)) {
      console.error("  ✗ " + file + " contains " + needle);
      fail++;
    }
  }
}
for (const file of cssFiles) {
  const body = stripCssComments(fs.readFileSync(path.join(ROOT, file), "utf8"));
  for (const needle of forbidden) {
    if (body.includes(needle)) {
      console.error("  ✗ " + file + " contains " + needle);
      fail++;
    }
  }
  for (const match of body.matchAll(/@import\s+(?:url\()?["']?([^"')\s;]+)["']?\)?/gi)) {
    const url = match[1].trim();
    if (/^https?:\/\//i.test(url) || /^\/\//.test(url)) {
      console.error("  ✗ " + file + " contains external @import " + url);
      fail++;
    }
  }
  for (const match of body.matchAll(/url\(([^)]+)\)/gi)) {
    const url = match[1].trim().replace(/^["']|["']$/g, "");
    if (!url || url.startsWith("#") || url.startsWith("data:")) continue;
    if (/^https?:\/\//i.test(url) || /^\/\//.test(url)) {
      console.error("  ✗ " + file + " contains external url() " + url);
      fail++;
    }
  }
}
if (fail) {
  console.error("\nRESULT: cdn policy failed (" + fail + " findings)");
  process.exit(1);
}
console.log("RESULT: cdn policy passed");
