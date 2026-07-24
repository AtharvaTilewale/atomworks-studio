# AtomWorks Annotation Studio for VS Code

The extension opens the same AtomWorks Studio service used by the website. It
does not copy CIF, session, credential, or model state into the extension host.

![AtomWorks Studio embedded in an editor webview](../../docs/images/web-embedded.png)

## Install

Download the `.vsix` from the matching `v*` GitHub release, then install it in
VS Code or Cursor:

```bash
code --install-extension atomworks-annotation-studio-<version>.vsix
# or
cursor --install-extension atomworks-annotation-studio-<version>.vsix
```

Registry installation can be added when Visual Studio Marketplace or Open VSX
publishing is enabled for this package.

## Use

1. Start a local Studio service with `make run`, or obtain the URL of an HTTPS
   deployment.
2. Run **AtomWorks: Configure Annotation Studio Server** from the Command
   Palette and enter the service URL.
3. Run **AtomWorks: Open Annotation Studio**.
4. Choose Patinae/WebGPU or Mol*/WebGL when prompted.

Change the explicit renderer later with **AtomWorks: Select Annotation Studio
Renderer**. Neither renderer silently falls back to the other.

Remote and Codespaces sessions use VS Code's external-URI forwarding. The
configured service must allow `vscode-webview:` in
`ANNOTATION_STUDIO_FRAME_ANCESTORS`.

For browser workflows, see the [website guide](../../docs/WEB.md). For
compiling, packaging, reinstalling, and smoke testing the extension, see the
[development guide](../../docs/DEVELOPMENT.md#vs-code-and-cursor).

## Troubleshooting

- **Service not reachable** — verify `/api/health` on the configured URL and
  use the configuration command to correct it.
- **Webview refused to load** — approve `vscode-webview:` in the service frame
  ancestors policy.
- **Old extension still loaded** — uninstall `nvidia.atomworks-annotation-studio`,
  reinstall the matching `.vsix` with `--force`, and reload the editor window.
