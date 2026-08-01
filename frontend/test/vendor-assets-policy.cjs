/* Verify vendored frontend assets are complete and integrity-pinned where used. */
const crypto = require("crypto");
const fs = require("fs");
const path = require("path");

const DESIGN = path.resolve(__dirname, "..", "design");
const VENDOR = path.join(DESIGN, "vendor");

const requiredFiles = [
  "README.md",
  "react/react.production.min.js",
  "react/react-dom.production.min.js",
  "react/LICENSE.react.txt",
  "react/LICENSE.react-dom.txt",
  "babel/babel.min.js",
  "babel/LICENSE.babel.txt",
  "leaflet/leaflet.css",
  "leaflet/leaflet.js",
  "leaflet/LICENSE.leaflet.txt",
  "leaflet/images/marker-icon.png",
  "leaflet/images/marker-icon-2x.png",
  "leaflet/images/marker-shadow.png",
  "leaflet/images/layers.png",
  "leaflet/images/layers-2x.png",
];

function fail(message) {
  console.error("  ✗ " + message);
  failures++;
}

function ok(message) {
  console.log("  ✓ " + message);
}

function read(file) {
  return fs.readFileSync(path.join(DESIGN, file), "utf8");
}

function assertExisting(relativePath, source) {
  const full = path.join(DESIGN, relativePath.replace(/^\.\//, ""));
  if (!full.startsWith(DESIGN + path.sep)) {
    fail(source + " points outside design/: " + relativePath);
    return;
  }
  if (!fs.existsSync(full)) {
    fail(source + " references missing asset: " + relativePath);
    return;
  }
  if (fs.statSync(full).size <= 0) {
    fail(source + " references empty asset: " + relativePath);
    return;
  }
  ok(source + " asset exists: " + relativePath);
}

function sriSha384(relativePath) {
  const body = fs.readFileSync(path.join(DESIGN, relativePath.replace(/^\.\//, "")));
  return "sha384-" + crypto.createHash("sha384").update(body).digest("base64");
}

let failures = 0;

for (const file of requiredFiles) {
  assertExisting("./vendor/" + file, "vendor manifest");
}

const htmlFiles = ["気象河川施工判断支援.dc.html", "index.html"];
for (const file of htmlFiles) {
  const body = read(file);
  for (const match of body.matchAll(/\b(?:src|href)=["'](\.\/vendor\/[^"']+)["']/g)) {
    assertExisting(match[1], file);
  }
}

const support = read("support.js");
for (const match of support.matchAll(/["'](\.\/vendor\/[^"']+)["']/g)) {
  assertExisting(match[1], "support.js");
}

for (const match of support.matchAll(/var\s+(REACT(?:_DOM)?_URL)\s*=\s*"([^"]+)";\s*var\s+\w+\s*=\s*"([^"]+)";/g)) {
  const [, name, url, expected] = match;
  const actual = sriSha384(url);
  if (actual !== expected) {
    fail(name + " SRI mismatch: expected " + expected + " actual " + actual);
  } else {
    ok(name + " SRI matches vendored file");
  }
}

const leafletCss = fs.readFileSync(path.join(VENDOR, "leaflet", "leaflet.css"), "utf8");
for (const match of leafletCss.matchAll(/url\(([^)]+)\)/g)) {
  const raw = match[1].trim().replace(/^["']|["']$/g, "");
  if (!raw || raw.startsWith("#") || raw.startsWith("data:") || /^[a-z][a-z0-9+.-]*:/i.test(raw)) continue;
  const cssRelative = "./vendor/leaflet/" + raw;
  assertExisting(cssRelative, "leaflet.css");
}

const vendorReadme = fs.readFileSync(path.join(VENDOR, "README.md"), "utf8");
for (const needle of ["React", "ReactDOM", "Babel", "Leaflet", "License"]) {
  if (!vendorReadme.includes(needle)) fail("vendor README missing " + needle);
}

if (failures) {
  console.error("\nRESULT: vendor asset policy failed (" + failures + " findings)");
  process.exit(1);
}

console.log("RESULT: vendor asset policy passed");
