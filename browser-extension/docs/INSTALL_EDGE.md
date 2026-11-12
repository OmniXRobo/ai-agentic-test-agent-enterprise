# Microsoft Edge

Microsoft Edge 116 or later. Install the companion first (INSTALL_WINDOWS11.md, step 1).

## Load unpacked (Developer mode), current user

1. Open `edge://extensions`.
2. Turn on **Developer mode** (left menu, at the bottom).
3. Press **Load unpacked** and choose `%LOCALAPPDATA%\SkyGuardAgent\extension`
   (type the path into the folder box: `%LOCALAPPDATA%` works there).
4. The card *SkyGuard Adaptive AI Agent 1.0.0* appears with ID `fmiiaecpdgbhhhbbkkfhnjfkbmllladb`.
5. Extensions icon (puzzle piece) > eye icon *Show in toolbar*.
6. Right-click the icon > **Options**, then pair and sign in (INSTALL_WINDOWS11.md, step 4).
7. Click the icon: the side panel opens on the right.

Edge may show *Turn off extensions in developer mode* at start-up. Choose **Keep on** or close the notice. This is
normal for Load unpacked; publishing through the Microsoft Edge Add-ons store or IT policy avoids it (ENTERPRISE_DEPLOYMENT.md).

## Site access

The extension starts with access to no web site. When you register an application in the side panel
(Applications > **Register and grant access**, or **Grant access to this site** for one registered earlier): Edge asks whether the extension may read and change
data on that site. Choose **Allow**. Only that address is granted. You can remove it later on the
extension's *Details* page > *Site access*.

Policy note: if your organisation sets `ExtensionSettings` (Edge policy) > `runtime_blocked_hosts` or blocks the extension, the grant
is refused and the panel says so.

## Optional permissions

* **Trusted typing** (Options > Typing): the `debugger` permission, used only when a field refuses ordinary typing.
  While it is used Edge shows the bar *"SkyGuard Adaptive AI Agent" started debugging this browser*. Without it,
  such a field pauses the run and you type the value yourself.
* **Microphone** (Options > Microphone > Allow the microphone): a page of the extension asks Edge for the microphone
  once. Audio goes only to the local companion.

## Update

Run the new package's INSTALL.cmd, then on `edge://extensions` press the reload arrow on the card (the folder is the
same). Your companion data, pairing and users are kept.

## Edge specifics

* The side panel needs Edge 116 or later. Edge shows it next to its own sidebar; if the toolbar icon opens a small
  window instead of a panel, your Edge build has no extension side panel: the extension then opens the same page in a
  window, which works the same way.
* Chrome and Edge each need their own pairing (each browser is a separate device for the companion). Both can be
  paired with the same companion.
