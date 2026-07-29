const fs = require("fs");
const path = require("path");
const { execFileSync } = require("child_process");

const root = path.resolve(__dirname, "..");
const outDir = path.join(root, "frontend", "public", "assets");
const outFile = path.join(outDir, "config.js");

const normalizeApiBase = (value) => {
  const raw = String(value || "").trim();
  if (!raw) return "";
  return raw.replace(/\/+$/, "");
};

const readText = (filePath) => {
  try {
    return fs.readFileSync(filePath, "utf8").trim();
  } catch (_) {
    return "";
  }
};

const fileMtimeIso = (filePath) => {
  try {
    return fs.statSync(filePath).mtime.toISOString();
  } catch (_) {
    return null;
  }
};

const gitValue = (...args) => {
  try {
    return execFileSync("git", args, {
      cwd: root,
      encoding: "utf8",
      stdio: ["ignore", "pipe", "ignore"],
    }).trim();
  } catch (_) {
    return null;
  }
};

const gitDirty = () => {
  const envValue = String(process.env.FLUENTFLOW_GIT_DIRTY || "").trim().toLowerCase();
  if (["1", "true", "yes", "dirty"].includes(envValue)) return true;
  if (["0", "false", "no", "clean"].includes(envValue)) return false;
  const status = gitValue("status", "--porcelain");
  return status == null ? null : Boolean(status);
};

const appVersion = String(process.env.FLUENTFLOW_VERSION || readText(path.join(root, "VERSION")) || "0.0.0-dev").trim();
const commit = String(process.env.FLUENTFLOW_GIT_COMMIT || gitValue("rev-parse", "HEAD") || "").trim() || null;
const changelogUpdatedAt = String(
  process.env.FLUENTFLOW_CHANGELOG_UPDATED_AT || fileMtimeIso(path.join(root, "docs", "changelog.md")) || ""
).trim() || null;

const config = {
  apiBase: normalizeApiBase(process.env.FLUENTFLOW_API_BASE),
  version: {
    app: "FluentFlow",
    component: "frontend",
    version: appVersion,
    commit,
    shortCommit: commit ? commit.slice(0, 7) : null,
    branch: String(process.env.FLUENTFLOW_GIT_BRANCH || gitValue("rev-parse", "--abbrev-ref", "HEAD") || "").trim() || null,
    dirty: gitDirty(),
    buildTime: String(process.env.FLUENTFLOW_BUILD_TIME || new Date().toISOString()).trim(),
    changelogUpdatedAt,
  },
};

fs.mkdirSync(outDir, { recursive: true });
fs.writeFileSync(
  outFile,
  `window.FLUENTFLOW_CONFIG = ${JSON.stringify(config, null, 2)};\n`,
  "utf8"
);
