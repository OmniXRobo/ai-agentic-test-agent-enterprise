# 23.3.34: sign-in with roles, and Network access for colleagues

Full package `SGQA-23.3.34.zip`, the same as 23.3.33 with these changes.

## Sign-in and accounts

* SkyGuard QA now asks everyone to sign in, also on the PC where it is installed.
* First sign-in: user name **admin**, password **admin**. SkyGuard QA then asks for a new password straight away
  (at least 8 characters, not the user name); nothing else opens until it is changed.
* The admin creates accounts on **Settings > Users & Network Access**: user name, full name, password and role. The
  admin can change a role, reset a password, disable or delete an account. The only admin account can never be
  deleted, disabled or made non-admin, and no one can change their own role or delete themselves.
* Anyone can change their own password from their name at the top right, and sign out there.
* Passwords are stored only as salted PBKDF2-SHA256 hashes. Five wrong passwords lock an account for five minutes.
  A sign-in ends when the browser closes, after 8 hours without activity, when SkyGuard QA restarts, or when the admin
  resets that person's password, changes their role or disables them.

## Roles

| Role | What it can do |
|---|---|
| Admin | Everything, plus Users & Network Access and Exit (stopping SkyGuard QA for everyone). |
| Tester | Full access to the work: create, edit, delete, run, record, schedules, settings, voice and AI. Not Users. |
| Developer | View only: every page, result and report; runs (plays) existing tests and schedules, stops them, exports report PDFs, uses voice and AI commands that look things up or run tests. Cannot create, change or delete anything. The Settings pages are hidden. |
| Manager | The same as Developer. |

The server checks the role on every request, so a view-only account cannot change anything even outside the
dashboard. Programs on the SkyGuard QA PC itself (the installer, the desktop icon, PowerShell checks) work as before.

## Network access: colleagues use SkyGuard QA from their own browser

1. Sign in as admin and open **Settings > Users & Network Access**.
2. Create an account for each colleague.
3. Tick **Let colleagues on the office network open SkyGuard QA in their browser**, then press Exit (top right) and
   open SkyGuard QA again from its icon.
4. Allow it through Windows Firewall once: press **Allow through Windows Firewall** (Windows asks for administrator
   permission), run `ALLOW_NETWORK_ACCESS.cmd` in the app folder as administrator, or ask IT to allow incoming TCP
   8765 on this PC. The rule is for office (domain and private) networks only. `ALLOW_NETWORK_ACCESS.cmd remove`
   takes it away again.
5. Send colleagues the link shown on the page, for example `http://QA-PC01:8765/` (this PC's name) or
   `http://10.20.30.40:8765/` (its address). They install nothing; they open the link in Edge or Chrome and sign in.

While Network access is on, closing the SkyGuard QA window keeps it running for colleagues; only Exit stops it. The
PC must stay on and signed in to Windows. Visible test runs and recordings open their browser on this PC's screen.

Colleagues' browsers: typed AI commands and spoken replies work. Chrome and Edge allow the microphone only on the PC
itself (or over HTTPS), so colleagues type their commands. The local AI and speech server are shared by everyone, so
several commands at the same time wait for each other.

Network access is off by default, and nothing listens on the office network until it is turned on.
