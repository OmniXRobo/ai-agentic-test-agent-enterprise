# Google Chrome

Chrome 116 or later. Install the companion first (INSTALL_WINDOWS11.md, step 1).

## Load unpacked (Developer mode), current user

1. Open `chrome://extensions`.
2. Turn on **Developer mode** (top right).
3. Press **Load unpacked** and choose `%LOCALAPPDATA%\SkyGuardAgent\extension`
   (type the path into the folder box: `%LOCALAPPDATA%` works there).
4. The card *SkyGuard Adaptive AI Agent 1.0.0* appears with ID `fmiiaecpdgbhhhbbkkfhnjfkbmllladb`.
5. Puzzle-piece icon > pin *SkyGuard Adaptive AI Agent*.
6. Right-click the icon > **Options**, then pair and sign in (INSTALL_WINDOWS11.md, step 4).
7. Click the icon: the side panel opens on the right.

Chrome may show *Disable developer mode extensions* at start-up. Choose **Keep** (or close the notice). This is
normal for Load unpacked; publishing through the Chrome Web Store or IT policy avoids it (ENTERPRISE_DEPLOYMENT.md).

## Site access

The extension starts with access to no web site. When you register an application in the side panel
(Applications > **Register and grant access**, or **Grant access to this site** for one registered earlier): Chrome asks whether the extension may read and change
data on that site. Choose **Allow**. Only that address is granted. You can remove it later on the
extension's *Details* page > *Site access*.

Policy note: if your organisation sets `ExtensionSettings` > `runtime_blocked_hosts` or blocks the extension, the grant
is refused and the panel says so.

## Optional permissions

* **Trusted typing** (Options > Typing): the `debugger` permission, used only when a field refuses ordinary typing.
  While it is used Chrome shows the bar *"SkyGuard Adaptive AI Agent" started debugging this browser*. Without it,
  such a field pauses the run and you type the value yourself.
* **Microphone** (Options > Microphone > Allow the microphone): a page of the extension asks Chrome for the microphone
  once. Audio goes only to the local companion.

## Update

Run the new package's INSTALL.cmd, then on `chrome://extensions` press the reload arrow on the card (the folder is the
same). Your companion data, pairing and users are kept.
