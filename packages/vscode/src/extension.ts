import { randomBytes } from "node:crypto";
import * as vscode from "vscode";

const DEFAULT_SERVER_URL = "http://127.0.0.1:8000";
const SETUP_URL =
  "https://github.com/baker-laboratory/atomworks-studio#quick-start";
type ViewerBackend = "patinae" | "molstar";
type HostTheme = "dark" | "light" | "high-contrast" | "high-contrast-light";

function escapeAttribute(value: string): string {
  return value.replaceAll("&", "&amp;").replaceAll('"', "&quot;").replaceAll("<", "&lt;");
}

function activeHostTheme(kind = vscode.window.activeColorTheme.kind): HostTheme {
  switch (kind) {
    case vscode.ColorThemeKind.Light:
      return "light";
    case vscode.ColorThemeKind.HighContrast:
      return "high-contrast";
    case vscode.ColorThemeKind.HighContrastLight:
      return "high-contrast-light";
    default:
      return "dark";
  }
}

async function serviceIsHealthy(baseUrl: URL): Promise<boolean> {
  try {
    const response = await fetch(new URL("/api/health", baseUrl), {
      signal: AbortSignal.timeout(3_000),
    });
    if (!response.ok) return false;
    const health = (await response.json()) as { status?: string };
    return health.status === "ok";
  } catch {
    return false;
  }
}

function parseServerUrl(value: string): URL | undefined {
  try {
    const url = new URL(value);
    if (!["http:", "https:"].includes(url.protocol)) return undefined;
    url.hash = "";
    return url;
  } catch {
    return undefined;
  }
}

function configurationTarget(
  configuration: vscode.WorkspaceConfiguration,
  key: string,
): vscode.ConfigurationTarget {
  const inspection = configuration.inspect(key);
  if (inspection?.workspaceFolderValue !== undefined) return vscode.ConfigurationTarget.WorkspaceFolder;
  if (inspection?.workspaceValue !== undefined) return vscode.ConfigurationTarget.Workspace;
  return vscode.ConfigurationTarget.Global;
}

async function configureServer(): Promise<boolean> {
  const configuration = vscode.workspace.getConfiguration("annotationStudio");
  const current = configuration.get<string>("serverUrl", DEFAULT_SERVER_URL);
  const value = await vscode.window.showInputBox({
    title: "Annotation Studio server",
    prompt: "Enter the HTTPS deployment or local development server URL.",
    value: current,
    ignoreFocusOut: true,
    validateInput: (candidate) =>
      parseServerUrl(candidate) ? undefined : "Enter an http:// or https:// URL.",
  });
  if (value === undefined) return false;
  const parsed = parseServerUrl(value);
  if (!parsed) return false;
  await configuration.update(
    "serverUrl",
    parsed.toString().replace(/\/$/, ""),
    configurationTarget(configuration, "serverUrl"),
  );
  return true;
}

async function selectViewerBackend(
  configuration: vscode.WorkspaceConfiguration,
  force = false,
): Promise<ViewerBackend | undefined> {
  const configured = configuration.get<string>("viewerBackend");
  if (!force && (configured === "patinae" || configured === "molstar")) return configured;
  const selected = await vscode.window.showQuickPick(
    [
      { label: "Patinae", detail: "WebGPU renderer", backend: "patinae" as const },
      { label: "Mol*", detail: "WebGL renderer", backend: "molstar" as const },
    ],
    {
      title: "Choose an Annotation Studio renderer",
      placeHolder: "Both renderers are independent, supported options.",
      ignoreFocusOut: true,
    },
  );
  if (!selected) return undefined;
  await configuration.update(
    "viewerBackend",
    selected.backend,
    configurationTarget(configuration, "viewerBackend"),
  );
  return selected.backend;
}

async function openStudio(): Promise<void> {
  const configuration = vscode.workspace.getConfiguration("annotationStudio");
  let configured = configuration.get<string>("serverUrl", DEFAULT_SERVER_URL);
  const viewerBackend = await selectViewerBackend(configuration);
  if (!viewerBackend) return;

  let serverUrl = parseServerUrl(configured);
  if (!serverUrl) {
    const changed = await configureServer();
    if (!changed) return;
    configured = configuration.get<string>("serverUrl", DEFAULT_SERVER_URL);
    serverUrl = parseServerUrl(configured);
    if (!serverUrl) return;
  }

  if (!(await serviceIsHealthy(serverUrl))) {
    const action = await vscode.window.showErrorMessage(
      `Annotation Studio is not reachable at ${serverUrl.origin}.`,
      "Configure server",
      "Open setup instructions",
    );
    if (action === "Configure server" && (await configureServer())) {
      await openStudio();
    } else if (action === "Open setup instructions") {
      void vscode.env.openExternal(vscode.Uri.parse(SETUP_URL));
    }
    return;
  }
  const externalUri = await vscode.env.asExternalUri(vscode.Uri.parse(serverUrl.toString()));
  const studioUrl = new URL(externalUri.toString());
  studioUrl.searchParams.set("embed", "1");
  studioUrl.searchParams.set("viewer", viewerBackend);
  studioUrl.searchParams.set("host", "vscode");
  studioUrl.searchParams.set("hostTheme", activeHostTheme());

  const panel = vscode.window.createWebviewPanel(
    "atomworksAnnotationStudio",
    "AtomWorks Annotation Studio",
    vscode.ViewColumn.Active,
    {
      enableScripts: true,
      retainContextWhenHidden: true,
    },
  );
  const origin = escapeAttribute(studioUrl.origin);
  const src = escapeAttribute(studioUrl.toString());
  const nonce = randomBytes(16).toString("base64");
  const childOrigin = JSON.stringify(studioUrl.origin);
  const initialTheme = JSON.stringify(activeHostTheme());
  panel.webview.html = `<!doctype html>
<html>
  <head>
    <meta charset="UTF-8">
    <meta http-equiv="Content-Security-Policy"
      content="default-src 'none'; frame-src ${origin}; style-src 'unsafe-inline'; script-src 'nonce-${nonce}';">
    <style>
      :root { color-scheme: light dark; }
      html, body, iframe { width: 100%; height: 100%; margin: 0; border: 0; overflow: hidden; }
      body {
        color: var(--vscode-foreground);
        background: var(--vscode-editor-background);
        font-family: var(--vscode-font-family);
      }
      iframe { display: block; background: var(--vscode-editor-background); }
    </style>
  </head>
  <body>
    <iframe id="studio-frame" src="${src}" title="AtomWorks Annotation Studio"
      allow="clipboard-read; clipboard-write; fullscreen"></iframe>
    <script nonce="${nonce}">
      const frame = document.getElementById("studio-frame");
      const childOrigin = ${childOrigin};
      const sendTheme = (theme) => frame.contentWindow?.postMessage({
        source: "atomworks-vscode-host",
        type: "theme",
        theme,
      }, childOrigin);
      frame.addEventListener("load", () => sendTheme(${initialTheme}));
      window.addEventListener("message", (event) => {
        if (event.data?.source === "atomworks-vscode-host" && event.data?.type === "theme") {
          sendTheme(event.data.theme);
        }
      });
    </script>
  </body>
</html>`;
  const themeSubscription = vscode.window.onDidChangeActiveColorTheme((theme) => {
    void panel.webview.postMessage({
      source: "atomworks-vscode-host",
      type: "theme",
      theme: activeHostTheme(theme.kind),
    });
  });
  panel.onDidDispose(() => themeSubscription.dispose());
}

export function activate(context: vscode.ExtensionContext): void {
  context.subscriptions.push(
    vscode.commands.registerCommand("annotationStudio.open", openStudio),
    vscode.commands.registerCommand("annotationStudio.configureServer", async () => {
      try {
        if (await configureServer()) {
          void vscode.window.showInformationMessage("Annotation Studio server updated.");
        }
      } catch (error) {
        void vscode.window.showErrorMessage(`Could not update Annotation Studio server: ${String(error)}`);
      }
    }),
    vscode.commands.registerCommand("annotationStudio.selectViewer", async () => {
      const selected = await selectViewerBackend(
        vscode.workspace.getConfiguration("annotationStudio"),
        true,
      );
      if (selected) void vscode.window.showInformationMessage(`Annotation Studio renderer: ${selected}.`);
    }),
  );
}

export function deactivate(): void {}
