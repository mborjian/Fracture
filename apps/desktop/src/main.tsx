import React from "react";
import ReactDOM from "react-dom/client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { Toaster } from "sonner";
import { App } from "@/app/App";
import "@/app/globals.css";

function applyTheme(theme: "light" | "dark" | "system") {
  const resolved = theme === "system"
    ? (window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light")
    : theme;
  document.documentElement.setAttribute("data-theme", resolved);
}

try {
  const raw = localStorage.getItem("fracture-ui-theme");
  const theme = raw === "light" || raw === "dark" || raw === "system" ? raw : "system";
  applyTheme(theme);
  window.matchMedia("(prefers-color-scheme: dark)").addEventListener("change", () => {
    const current = localStorage.getItem("fracture-ui-theme");
    if ((current ?? "system") === "system") {
      applyTheme("system");
    }
  });
} catch {
  applyTheme("system");
}

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      refetchOnWindowFocus: false,
      retry: 1,
      staleTime: 5000
    }
  }
});

ReactDOM.createRoot(document.getElementById("root")!).render(
    <React.StrictMode>
    <QueryClientProvider client={queryClient}>
      <App />
      <Toaster theme="dark" position="bottom-right" richColors />
    </QueryClientProvider>
  </React.StrictMode>
);
