Digonto SSH - SSH terminal + SFTP files, your servers on every computer
=======================================================================
First start: type your e-mail and a vault password (min. 8 characters), choose where the vault lives, press
"Create account". On your other computers (Windows, macOS, Ubuntu) use the same e-mail, password and location with
"Sign in" - you see the same servers. Changes appear within 15 seconds.

Vault location
  * My VPN / own server (recommended): enter the server address (e.g. 10.8.0.1), SSH user, password (or key file)
    and the folder name once and press Connect. The app logs in and creates the folder on the server. It stays
    connected until you press Disconnect: next time you start the app it checks the connection by itself and shows
    "Connected" - nothing to type again. Sign in is possible when connected. The kept login is stored on this
    computer only, in a file only your user can read; Disconnect deletes the saved password.
    The server needs only SSH - no other program. The folder (default "digonto-sync") holds one encrypted file. A copy also stays on each computer, so the app works while
    the VPN is down, and if the server is reinstalled the vault is uploaded again automatically.
  * My SyncDrive folder: the same file in your SyncDrive folder (found automatically, or choose it once).
  * Another folder (Dropbox ...): the same file in any synced folder on this computer.
The vault holds your servers, passwords and private keys, encrypted (AES-256-GCM, key from your password with scrypt).
The password cannot be reset - keep it safe.

Use:  Add server -> double-click it. Tabs: "Terminal" and "Files (SFTP)" (drag files from your file manager onto the
server list, or use Upload / Download; folders work too). Ctrl+Shift+C / Ctrl+Shift+V copy and paste
(macOS: Cmd+C / Cmd+V). Ctrl + mouse wheel changes the text size.
The first time you connect, the server's fingerprint is shown; if it ever changes you get a warning.

The installer also installs and switches on the OpenSSH server on this computer (asks first; skip with
--no-sshd), so you can log in to this computer from your other ones.
Mouse: programs such as vim, htop, mc and tmux get clicks, drags and the wheel; hold Shift to select text instead.
Jump host: choose another saved server in the server dialog; the connection goes through it.
Port forwarding: one rule per line in the server dialog - L 8080:localhost:80, R 9000:localhost:3000, D 1080 (SOCKS5).

Known hosts tab: lists every server you trusted (host:port + fingerprint). Select and
"Remove selected" - you are asked to trust it again the next time you connect. Synced to all your computers.
Terminal: "Homebrew" look (green on black), font size 16 (Ctrl/Cmd +/- or Ctrl+wheel to change).

1.3: Known hosts moved to the left list (tab next to Servers). Shortcuts: Ctrl+C copy (when text is selected; otherwise
it stops the running program), Ctrl+V paste - the same on every system; change them in Account > Terminal shortcuts.
If the server restarts or the network drops, the tab shows "reconnecting" and retries every half second until it is back.
