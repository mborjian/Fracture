import { execFileSync } from "node:child_process";
import { mkdirSync, readFileSync, writeFileSync, existsSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);

export const repoRoot = path.resolve(__dirname, "..");
export const tempDir = path.join(repoRoot, ".release-tmp");
export const statePath = path.join(tempDir, "release-state.json");
export const releaseNotesPlaceholder = "<!-- RELEASE_NOTES_TODO -->";

export const versionFiles = [
  "package.json",
  "apps/desktop/package.json",
  "apps/desktop/package-lock.json",
  "apps/desktop/src-tauri/tauri.conf.json",
  "apps/desktop/src-tauri/Cargo.toml",
  "apps/backend/pyproject.toml",
  "apps/backend/app/core/config.py",
];

export function runGit(args, options = {}) {
  const { allowFailure = false } = options;

  try {
    return execFileSync("git", args, {
      cwd: repoRoot,
      encoding: "utf8",
      stdio: ["ignore", "pipe", "pipe"],
    }).trimEnd();
  } catch (error) {
    if (allowFailure) {
      return null;
    }

    const stderr = error.stderr?.toString().trim();
    const stdout = error.stdout?.toString().trim();
    const details = stderr || stdout || error.message;
    throw new Error(`git ${args.join(" ")} failed: ${details}`);
  }
}

export function ensureCleanWorktree() {
  const status = runGit(["status", "--porcelain"]);
  if (status.trim().length > 0) {
    throw new Error(
      "Release commands require a clean working tree. Commit or stash existing changes first.",
    );
  }
}

export function ensureDirectory(dirPath) {
  mkdirSync(dirPath, { recursive: true });
}

export function readJson(relativePath) {
  return JSON.parse(readText(relativePath));
}

export function writeJson(relativePath, value) {
  writeText(relativePath, `${JSON.stringify(value, null, 2)}\n`);
}

export function readText(relativePath) {
  return readFileSync(resolveRepoPath(relativePath), "utf8");
}

export function writeText(relativePath, value) {
  ensureDirectory(path.dirname(resolveRepoPath(relativePath)));
  writeFileSync(resolveRepoPath(relativePath), value, "utf8");
}

export function resolveRepoPath(relativePath) {
  return path.join(repoRoot, relativePath);
}

export function fileExists(relativePath) {
  return existsSync(resolveRepoPath(relativePath));
}

export function getLastVersionTag() {
  const tag = runGit(["describe", "--tags", "--abbrev=0", "--match", "v*"], {
    allowFailure: true,
  });

  if (!tag) {
    throw new Error("No existing version tag matching 'v*' was found.");
  }

  return tag.trim();
}

export function getCommitRange(fromTag, toRef = "HEAD") {
  return `${fromTag}..${toRef}`;
}

export function parseVersion(version) {
  const match = /^(\d+)\.(\d+)\.(\d+)$/.exec(version);
  if (!match) {
    throw new Error(`Unsupported version format '${version}'. Expected x.y.z.`);
  }

  return {
    major: Number(match[1]),
    minor: Number(match[2]),
    patch: Number(match[3]),
  };
}

export function bumpVersion(currentVersion, bumpLevel) {
  const parsed = parseVersion(currentVersion);

  if (bumpLevel === "major") {
    return `${parsed.major + 1}.0.0`;
  }

  if (bumpLevel === "minor") {
    return `${parsed.major}.${parsed.minor + 1}.0`;
  }

  return `${parsed.major}.${parsed.minor}.${parsed.patch + 1}`;
}

function normalizeMessage(text) {
  return text.replace(/\r\n/g, "\n").trim();
}

function isMetaOnlyFile(filePath) {
  const normalized = filePath.replace(/\\/g, "/");

  if (
    normalized.startsWith(".github/") ||
    normalized.startsWith("docs/") ||
    normalized.startsWith(".idea/") ||
    normalized.startsWith(".release-tmp/")
  ) {
    return true;
  }

  if (
    normalized === ".gitignore" ||
    normalized === "LICENSE" ||
    normalized === "todo.md" ||
    normalized === "README.md" ||
    normalized === "README.fa.md"
  ) {
    return true;
  }

  if (
    normalized.endsWith(".md") ||
    normalized.endsWith(".lock") ||
    normalized.endsWith(".log")
  ) {
    return true;
  }

  return false;
}

function classifyCommitLevel(commit) {
  const text = `${commit.subject}\n${commit.body}`.trim();
  const codeFiles = commit.files.filter((file) => !isMetaOnlyFile(file));

  if (/(^|\n)BREAKING CHANGE\b/i.test(text) || /^[a-z]+(\(.+?\))?!:/im.test(text)) {
    return {
      level: "major",
      reason: `breaking change marker in ${commit.hash.slice(0, 7)}`,
    };
  }

  if (
    codeFiles.length > 0 &&
    (/^(feat|feature)(\(.+?\))?:/i.test(commit.subject) ||
      /^(add|introduce|implement|create|enable|support|ship)\b/i.test(commit.subject))
  ) {
    return {
      level: "minor",
      reason: `feature-like commit ${commit.hash.slice(0, 7)}: ${commit.subject}`,
    };
  }

  return {
    level: "patch",
    reason: `patch-level change ${commit.hash.slice(0, 7)}: ${commit.subject}`,
  };
}

export function inferBump(commits) {
  let winning = {
    level: "patch",
    reason: "defaulted to patch because no breaking or feature-level changes were detected",
  };

  for (const commit of commits) {
    const current = classifyCommitLevel(commit);
    if (compareBumpLevel(current.level, winning.level) > 0) {
      winning = current;
    }
  }

  return winning;
}

function compareBumpLevel(left, right) {
  const order = { patch: 0, minor: 1, major: 2 };
  return order[left] - order[right];
}

export function getCommitsInRange(range) {
  const raw = runGit([
    "log",
    "--reverse",
    "--format=%H%x1f%s%x1f%b%x1e",
    range,
  ]);

  const commits = raw
    .split("\u001e")
    .map((entry) => entry.trim())
    .filter(Boolean)
    .map((entry) => {
      const [hash, subject, body = ""] = entry.split("\u001f");
      const filesOutput = runGit(["show", "--pretty=format:", "--name-only", hash]);
      const files = filesOutput
        .split(/\r?\n/)
        .map((line) => line.trim())
        .filter(Boolean);

      return {
        hash,
        subject: normalizeMessage(subject),
        body: normalizeMessage(body),
        files,
      };
    });

  if (commits.length === 0) {
    throw new Error(
      `No commits were found in range '${range}'. The latest version tag is already at HEAD, so there is nothing new to release yet.`,
    );
  }

  return commits;
}

export function getCombinedDiff(range) {
  return runGit(["diff", "--find-renames", range]);
}

export function getDiffStat(range) {
  return runGit(["diff", "--stat", "--summary", "--find-renames", range]);
}

export function buildAiPrompt({
  version,
  previousTag,
  bumpLevel,
  bumpReason,
  commitCount,
}) {
  return [
    "You are writing release notes for Fracture.",
    "",
    "Use the supplied commit messages and diffs as the only source of truth.",
    "Write concise, polished Markdown for end users and contributors.",
    "",
    `Release target: Fracture ${version}`,
    `Previous tag: ${previousTag}`,
    `Semantic version bump: ${bumpLevel}`,
    `Bump rationale: ${bumpReason}`,
    `Included commits: ${commitCount}`,
    "",
    "Output format:",
    `# Fracture ${version}`,
    "",
    "One short intro paragraph.",
    "",
    "## Highlights",
    "- 3 to 6 bullets focused on meaningful user-facing changes.",
    "",
    "## Fixes And Improvements",
    "- concise bullets for important fixes, refinements, and infra work.",
    "",
    "## Notes",
    "- optional bullets only when needed for release process, compatibility, or packaging details.",
    "",
    "Rules:",
    "- Stay accurate to the provided context.",
    "- Merge repetitive commits into clear themes.",
    "- Prefer product language over raw implementation details unless the detail matters.",
    "- Do not mention commit hashes, branch names, or that an AI wrote the notes.",
    "- Do not invent features or guarantees.",
  ].join("\n");
}

export function buildAiInputFile({
  version,
  previousTag,
  bumpLevel,
  bumpReason,
  commitRange,
  commits,
  diffStat,
  combinedDiff,
}) {
  const commitLines = commits
    .map((commit) => {
      const lines = [
        `- ${commit.subject} (${commit.hash.slice(0, 7)})`,
      ];

      if (commit.body) {
        lines.push(`  ${commit.body.replace(/\n/g, "\n  ")}`);
      }

      if (commit.files.length > 0) {
        lines.push(`  Files: ${commit.files.join(", ")}`);
      }

      return lines.join("\n");
    })
    .join("\n");

  return [
    "# AI Release Notes Prompt",
    "",
    "Copy the prompt below into your AI tool together with the rest of this file.",
    "",
    "```text",
    buildAiPrompt({
      version,
      previousTag,
      bumpLevel,
      bumpReason,
      commitCount: commits.length,
    }),
    "```",
    "",
    "# Release Facts",
    "",
    `- Previous tag: ${previousTag}`,
    `- New version: ${version}`,
    `- Semantic bump: ${bumpLevel}`,
    `- Bump reason: ${bumpReason}`,
    `- Commit range: ${commitRange}`,
    `- Commit count: ${commits.length}`,
    "",
    "# Commits",
    "",
    commitLines,
    "",
    "# Diff Stat",
    "",
    "```text",
    diffStat || "(no diff stat available)",
    "```",
    "",
    "# Full Diff",
    "",
    "```diff",
    combinedDiff || "",
    "```",
    "",
  ].join("\n");
}

export function buildReleaseNotesTemplate(version, previousTag, bumpLevel, bumpReason) {
  return [
    `# Fracture ${version}`,
    "",
    releaseNotesPlaceholder,
    "",
    "<!-- Replace this template with the final release notes before running release:finalize. -->",
    `<!-- Based on ${previousTag} -> v${version} (${bumpLevel}: ${bumpReason}) -->`,
    "",
    "Short release summary.",
    "",
    "## Highlights",
    "- ",
    "",
    "## Fixes And Improvements",
    "- ",
    "",
    "## Notes",
    "- ",
    "",
  ].join("\n");
}

export function updateVersionFiles(nextVersion) {
  const rootPackage = readJson("package.json");
  rootPackage.version = nextVersion;
  writeJson("package.json", rootPackage);

  const desktopPackage = readJson("apps/desktop/package.json");
  desktopPackage.version = nextVersion;
  writeJson("apps/desktop/package.json", desktopPackage);

  const desktopLock = readJson("apps/desktop/package-lock.json");
  desktopLock.version = nextVersion;
  if (desktopLock.packages?.[""]) {
    desktopLock.packages[""].version = nextVersion;
  }
  writeJson("apps/desktop/package-lock.json", desktopLock);

  const tauriConfig = readJson("apps/desktop/src-tauri/tauri.conf.json");
  tauriConfig.version = nextVersion;
  writeJson("apps/desktop/src-tauri/tauri.conf.json", tauriConfig);

  replaceSingleVersionLine(
    "apps/desktop/src-tauri/Cargo.toml",
    /^version = "([^"]+)"$/m,
    nextVersion,
  );
  replaceSingleVersionLine(
    "apps/backend/pyproject.toml",
    /^version = "([^"]+)"$/m,
    nextVersion,
  );
  replaceSingleVersionLine(
    "apps/backend/app/core/config.py",
    /version: str = "([^"]+)"/,
    nextVersion,
  );
}

function replaceSingleVersionLine(relativePath, pattern, nextVersion) {
  const current = readText(relativePath);
  if (!pattern.test(current)) {
    throw new Error(`Could not find a replaceable version entry in '${relativePath}'.`);
  }

  const updated = current.replace(pattern, (fullMatch) =>
    fullMatch.replace(/"([^"]+)"/, `"${nextVersion}"`),
  );
  writeText(relativePath, updated);
}

export function writeReleaseState(state) {
  ensureDirectory(tempDir);
  writeText(path.relative(repoRoot, statePath), `${JSON.stringify(state, null, 2)}\n`);
}

export function readReleaseState() {
  if (!existsSync(statePath)) {
    throw new Error(
      "No prepared release state was found. Run 'npm run release:prepare' first.",
    );
  }

  return JSON.parse(readFileSync(statePath, "utf8"));
}
