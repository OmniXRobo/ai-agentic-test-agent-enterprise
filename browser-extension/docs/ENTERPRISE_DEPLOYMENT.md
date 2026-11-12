# Deployment options for organisations

Load unpacked (Developer mode) is the documented per-user route and needs no administrator rights. Organisations
that disable Developer mode, or want the extension managed centrally, have three supported alternatives. None of them
bypasses browser or enterprise policy; each is done by whoever administers the browsers.

## 1. Chrome Web Store / Microsoft Edge Add-ons (public or unlisted)

1. Zip the contents of `extension\` (manifest.json at the root of the zip) and upload it in the Chrome Web Store
   developer dashboard or Microsoft Partner Center (Edge Add-ons). Remove the `"key"` line first: the stores assign
   their own key.
2. The store gives the extension a **new ID**. Add it to the companion's `data\config.json` on each PC:
   `"extension_ids": ["fmiiaecpdgbhhhbbkkfhnjfkbmllladb", "<store id>"]`, then restart the companion.
3. Users install it from the store page; no Developer mode, no start-up warning.
4. Store review discloses: the `debugger` permission is optional and requested at use; host access is optional and
   requested per application; the extension talks only to 127.0.0.1.

## 2. Force install by policy (Group Policy, Intune, or registry)

Chrome (`HKLM\Software\Policies\Google\Chrome`) or Edge (`HKLM\Software\Policies\Microsoft\Edge`):

* `ExtensionInstallForcelist`: `<id>;<update URL>`. For a store-published extension the update URL is
  `https://clients2.google.com/service/update2/crx` (Chrome) or
  `https://edge.microsoft.com/extensionwebstorebase/v1/crx` (Edge).
* Self-hosted CRX: sign the extension (`chrome --pack-extension=<folder> --pack-extension-key=<key.pem>`), host the
  `.crx` and an update manifest XML on an internal HTTPS server and use that URL. On Windows, Chrome and Edge accept
  self-hosted force-installs only on domain-joined or MDM-enrolled devices.
* To pre-grant site access to your applications instead of the per-user prompt, use `ExtensionSettings` >
  `<id>` > `runtime_allowed_hosts` with the application addresses.
* Load unpacked is blocked when `DeveloperToolsAvailability` is 2 or, in newer browser versions,
  `ExtensionDeveloperModeSettings` is 1. If `ExtensionInstallBlocklist` is `*`, add the ID to
  `ExtensionInstallAllowlist`. Check the policy documentation of your browser version; the installer only reads
  these settings and warns.

The companion still has to be installed per user (INSTALL.cmd), or deployed by IT as a per-user package that runs
`scripts\Install-SkyGuardAgent.ps1 -NoStart` at first sign-in.

## 3. IT-managed companion installation

`Install-SkyGuardAgent.ps1` writes only to `%LOCALAPPDATA%\SkyGuardAgent` and `HKCU`. It can run from a sign-in
script or a per-user Intune Win32 app (install context: user). Use `-Port` to standardise the port and copy a prepared
`config.json` (for example with your `extension_ids`) into `data\` afterwards.

## What the package does not do

* It never writes browser policies or `HKLM`, never edits a browser profile, and never installs the extension into a
  browser by itself.
* It never needs administrator rights and never contacts the internet.
