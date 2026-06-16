import {
  fileExists,
  readReleaseState,
  readText,
  releaseNotesPlaceholder,
  resolveRepoPath,
  runGit,
  versionFiles,
} from "./release-lib.mjs";

function main() {
  const state = readReleaseState();
  const releaseNotesPath = state.releaseNotesPath;

  if (!fileExists(releaseNotesPath)) {
    throw new Error(`Release notes file is missing: ${releaseNotesPath}`);
  }

  const releaseNotes = readText(releaseNotesPath).trim();
  if (!releaseNotes) {
    throw new Error(`Release notes file is empty: ${releaseNotesPath}`);
  }

  if (releaseNotes.includes(releaseNotesPlaceholder)) {
    throw new Error(
      `Release notes file still contains the placeholder marker: ${releaseNotesPath}`,
    );
  }

  const statusBefore = runGit(["status", "--porcelain"]);
  if (statusBefore.trim().length === 0) {
    throw new Error("There are no changes to finalize.");
  }

  const trackedReleaseFiles = [...versionFiles, releaseNotesPath];
  runGit(["add", "--", ...trackedReleaseFiles]);

  const statusAfterAdd = runGit(["diff", "--cached", "--name-only"]);
  if (!statusAfterAdd.trim()) {
    throw new Error("No staged changes were found after adding release files.");
  }

  runGit([
    "commit",
    "-m",
    `chore(release): ${state.nextVersion}`,
  ]);

  runGit(["tag", "-a", state.nextTag, "-m", `Fracture ${state.nextTag}`]);
  runGit(["push", "origin", "HEAD"]);
  runGit(["push", "origin", state.nextTag]);

  console.log(`Committed release ${state.nextVersion}`);
  console.log(`Tagged ${state.nextTag}`);
  console.log(`Pushed branch and tag to origin`);
  console.log(`Release notes: ${resolveRepoPath(releaseNotesPath)}`);
}

main();
