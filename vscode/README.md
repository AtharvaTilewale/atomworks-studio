# AtomWorks Annotation Studio for VS Code

Install from the Visual Studio Marketplace/Open VSX when those registries are
enabled, or download the versioned `.vsix` from the matching
`annotation-studio-v*` GitHub release:

```bash
code --install-extension atomworks-annotation-studio-0.1.0.vsix
```

Run the Annotation Studio service, then invoke **AtomWorks: Open Annotation
Studio** from the Command Palette. Use **AtomWorks: Configure Annotation Studio
Server** to select a permanent HTTPS deployment or local
`http://127.0.0.1:8000` service. Remote/Codespaces sessions use VS Code's
external-URI forwarding. The first launch asks for Patinae or Mol*; change the
explicit choice later with **AtomWorks: Select Annotation Studio Renderer**.
Neither renderer silently falls back to the other.

This extension embeds the same Patinae/AtomWorks application as the browser
version, so CIF behavior and validation remain identical rather than being
reimplemented in the extension host.

The configured server must allow `vscode-webview:` in
`ANNOTATION_STUDIO_FRAME_ANCESTORS`. Session and model data stay in the
configured service; the extension contains no NGC credentials or inference
runtime.
