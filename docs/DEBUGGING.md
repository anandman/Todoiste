# Debugging Todoiste

## Common Issues

- **Scripts not loading?** — Check Xcode console for `[Todoiste] Failed to load`
  messages. Ensure JS files are in the app bundle (Build Phases → Copy Bundle
  Resources).
- **Notifications not appearing?** — Verify macOS notification settings for
  Todoiste are enabled. Notification delivery is native (`UNUserNotificationCenter`)
  via the JS bridge in `WebView.swift`.
- **Shortcuts not working?** — Open Safari → Develop menu → select the app's
  webview to use Web Inspector. Check the console for errors from
  todoist-shortcuts.js.
- **Login issues?** — Check that the OAuth provider's domain is in the
  `allowedDomains` list in `WebView.swift`.

## Known Limitations

- **OAuth popups** — handled by loading inline in the main webview. If a
  provider changes its OAuth flow, this may need adjustment.
- **Notification click-through context** — notifications currently open/focus
  the app, but do not deep-link back to a specific Todoist task yet.
