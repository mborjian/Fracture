import { execSync, spawn } from "node:child_process";

const DEV_PORT = 5173;

function listListeningPidsOnWindows(port) {
  try {
    const output = execSync(
      `powershell -NoProfile -Command "(Get-NetTCPConnection -State Listen -LocalPort ${port} -ErrorAction SilentlyContinue | Select-Object -ExpandProperty OwningProcess -Unique) -join ' '"`,
      {
        encoding: "utf8",
        stdio: ["ignore", "pipe", "ignore"],
      },
    );

    const pids = new Set();
    for (const token of output.trim().split(/\s+/)) {
      const pid = Number(token);
      if (!Number.isInteger(pid) || pid <= 0 || pid === process.pid) continue;
      pids.add(pid);
    }
    return [...pids];
  } catch {
    return [];
  }
}

function killPidTreeOnWindows(pid) {
  try {
    execSync(`taskkill /PID ${pid} /T /F`, {
      encoding: "utf8",
      stdio: ["ignore", "ignore", "ignore"],
    });
    console.log(`[tauri-dev] terminated stale process on :${DEV_PORT} (pid ${pid})`);
  } catch {
    // Ignore failures (process already gone, permission edge cases, etc.).
  }
}

function clearStaleDevPort(port) {
  if (process.platform !== "win32") return;
  for (const pid of listListeningPidsOnWindows(port)) {
    killPidTreeOnWindows(pid);
  }
}

clearStaleDevPort(DEV_PORT);

const vite =
  process.platform === "win32"
    ? spawn(process.env.ComSpec || "cmd.exe", ["/d", "/s", "/c", "npm run dev"], {
        stdio: "inherit",
        shell: false,
      })
    : spawn("npm", ["run", "dev"], {
        stdio: "inherit",
        shell: false,
      });

const forwardSignal = (signal) => {
  if (!vite.killed) {
    vite.kill(signal);
  }
};

process.on("SIGINT", () => forwardSignal("SIGINT"));
process.on("SIGTERM", () => forwardSignal("SIGTERM"));

vite.on("exit", (code, signal) => {
  if (signal) {
    process.kill(process.pid, signal);
    return;
  }
  process.exit(code ?? 0);
});
