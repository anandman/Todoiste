# Future Distribution (High Gain Design)

Once the Apple Developer account is approved for **High Gain Design** (email: `dev@highgain.design` or similar), follow these steps to enable professional distribution:

1.  **Update `project.yml`**:
    - Change `PRODUCT_BUNDLE_IDENTIFIER` to `design.highgain.Todoiste`.
    - Add `DEVELOPMENT_TEAM: <TEAM_ID>` (found in Apple Developer Portal).
    - Set `ENABLE_HARDENED_RUNTIME: YES` (required for Notarization).
    - Set `CODE_SIGN_STYLE: Automatic` or `Manual`.

2.  **Code Signing & Notarization**:
    - Create a **Developer ID Application** certificate in the Apple Portal.
    - Store the certificate (P12) as a GitHub Secret for the release workflow.
    - Update `.github/workflows/release.yml` with signing and `notarytool` steps.
    - Notarization requires an App-Specific Password stored in GitHub Secrets.

3.  **Gatekeeper**:
    - Without signing/notarization, users must **Right-Click -> Open** the app to bypass "unidentified developer" warnings.
