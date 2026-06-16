import path from "node:path";
import {
  buildAiInputFile,
  buildReleaseNotesTemplate,
  bumpVersion,
  ensureCleanWorktree,
  getCombinedDiff,
  getCommitRange,
  getCommitsInRange,
  getDiffStat,
  getLastVersionTag,
  inferBump,
  readJson,
  tempDir,
  updateVersionFiles,
  versionFiles,
  writeReleaseState,
  writeText,
} from "./release-lib.mjs";

function main() {
  ensureCleanWorktree();

  const rootPackage = readJson("package.json");
  const currentVersion = rootPackage.version;
  const lastTag = getLastVersionTag();
  const previousVersion = lastTag.replace(/^v/, "");

  if (currentVersion !== previousVersion) {
    throw new Error(
      `Root package version (${currentVersion}) does not match the latest tag (${previousVersion}).`,
    );
  }

  const commitRange = getCommitRange(lastTag);
  const commits = getCommitsInRange(commitRange);
  const { level: bumpLevel, reason: bumpReason } = inferBump(commits);
  const nextVersion = bumpVersion(previousVersion, bumpLevel);
  const nextTag = `v${nextVersion}`;
  const diffStat = getDiffStat(commitRange);
  const combinedDiff = getCombinedDiff(commitRange);

  updateVersionFiles(nextVersion);

  const releaseNotesRelativePath = `.github/releases/${nextVersion}.md`;
  const aiInputRelativePath = path.join(".release-tmp", `release-notes-input-${nextVersion}.md`);
  const summaryRelativePath = path.join(".release-tmp", `release-summary-${nextVersion}.md`);

  writeText(
    releaseNotesRelativePath,
    buildReleaseNotesTemplate(nextVersion, lastTag, bumpLevel, bumpReason),
  );

  writeText(
    aiInputRelativePath,
    buildAiInputFile({
      version: nextVersion,
      previousTag: lastTag,
      bumpLevel,
      bumpReason,
      commitRange,
      commits,
      diffStat,
      combinedDiff,
    }),
  );

  writeText(
    summaryRelativePath,
    [
      `Prepared Fracture ${nextVersion}`,
      "",
      `- Previous tag: ${lastTag}`,
      `- New tag: ${nextTag}`,
      `- Semantic bump: ${bumpLevel}`,
      `- Reason: ${bumpReason}`,
      `- Release notes file: ${releaseNotesRelativePath}`,
      `- AI input file: ${aiInputRelativePath}`,
      "",
      "Next steps:",
      `1. Open ${aiInputRelativePath} and send it to your AI tool.`,
      `2. Paste the generated release notes into ${releaseNotesRelativePath}.`,
      "3. Review the version diffs and commit the intended app changes if needed.",
      "4. Run npm run release:finalize",
      "",
      "Updated version files:",
      ...versionFiles.map((file) => `- ${file}`),
      "",
    ].join("\n"),
  );

  writeReleaseState({
    previousTag: lastTag,
    previousVersion,
    nextVersion,
    nextTag,
    bumpLevel,
    bumpReason,
    commitRange,
    releaseNotesPath: releaseNotesRelativePath,
    aiInputPath: aiInputRelativePath,
    summaryPath: summaryRelativePath,
    preparedAt: new Date().toISOString(),
  });

  console.log(`Prepared ${nextTag}`);
  console.log(`Release notes template: ${releaseNotesRelativePath}`);
  console.log(`AI input file: ${aiInputRelativePath}`);
  console.log(`Summary: ${summaryRelativePath}`);
  console.log(`Working directory: ${tempDir}`);
}

main();
