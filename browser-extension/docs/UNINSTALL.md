# Uninstalling

## The extension

`chrome://extensions` or `edge://extensions` > *SkyGuard Adaptive AI Agent* > **Remove**. This deletes the pairing
and sign-in kept in the browser. The companion's data is not affected.

## The companion

Double-click **UNINSTALL.cmd** in the package, or Start menu > *SkyGuard Agent - Uninstall*. It:

* stops the companion,
* removes the sign-in entry (`HKCU\...\Run\SkyGuardAgent`) and the Start menu folder,
* removes `app\`, `runtime\`, `extension\`, `backups\` and the helper scripts from `%LOCALAPPDATA%\SkyGuardAgent`,
* **keeps** `data\` (knowledge, audit trail, users, encrypted saved passwords) and `models\`.

To delete everything, including saved passwords and learned knowledge:

```
UNINSTALL.cmd -RemoveData
```

No administrator rights are needed; nothing outside `%LOCALAPPDATA%\SkyGuardAgent`, your Start menu and `HKCU\...\Run`
was changed by the installer.

## Reinstalling

Run INSTALL.cmd again. If `data\` was kept, your users, devices and knowledge are still there; browsers that were
paired stay paired as long as the extension was not removed.
