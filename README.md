**English** | [한국어](README.ko.md)

# MDirNet

A terminal file manager for moving files and folders between PCs on the same router, in the style of MDir — the DOS-era Korean file manager.

> **Note on the interface language.** The program's own screens, menus, and messages are in Korean. This README is in English, but the UI is not localized.

> **Relationship to MDir.** The layout and the way you drive it — dual panes, a function-key bar along the bottom, a built-in file viewer, mask selection — follow MDir and its ancestor Norton Commander. But **MDir itself had no PC-to-PC networking.** Back then you moved files between two machines with a comms program such as 이야기 or 새롬데이타맨 over ZMODEM. This tool bolts LAN transfer, which MDir never had, onto MDir's feel; the function-key assignments follow Norton Commander convention rather than MDir's actual key map.

```
 MDirNet  node-alpha                                                           * node-beta 연결됨
내 PC  ~/Documents                              |node-beta  /project/src
================================================+=================================================
 ..                   [ Up-Dir ]                | ..                    [ Up-Dir ]
 FONT                 [ SubDir ] 26-07-28  3:41p| assets                [ SubDir ] 26-07-28  3:41p
*shared               [ SubDir ] 26-07-28  3:41p| README            MD       412 B 26-07-28  3:41p
 backup           SH        10 B 26-07-28  3:41p| main              PY     12.6 KB 26-07-28  3:41p
 report           TXT     2.0 KB 26-07-28  3:41p|
 song             MP3     3.3 MB 26-07-28  3:41p|
================================================+=================================================
1개 선택 . 0 B                                  |2 File  1 Dir  13,301 Byte  . 이름순
 node-beta 와 연결되었습니다
1도움    2정렬    3보기    4폴더    5복사    6이름    7만들    8삭제    9메뉴    0끝내
```

Like MDir, there are **no outer walls.** A single `=` rule runs above and below the listing, and one vertical divider splits the panes. Dropping the walls hands two more columns back to content, and both panes start flush against their edge. The focused pane has its **title row inverted.**

Name and extension sit in separate columns; directories read `[ SubDir ]` and the parent `[ Up-Dir ]`.

**The extension, size, date, and time columns are fixed width, and the name column takes whatever is left.** Widen the window and you see more of long filenames while the right-hand columns stay put. Sizes are shown as `3.3 MB`.

Below **79 columns** there is no room for every column, so the time, then the date, then the extension fold away in that order.

## Install

Python 3.9 or newer, plus one package.

```bash
pip install -r requirements.txt
```

That package is `cryptography`, used to make the certificate for TLS. The Python standard
library has no way to create one. Everything else is standard library.

**macOS · Linux**
```bash
./run.sh
```

**Windows**
```bat
run.bat
```

The launchers locate their own directory and put it on the module path, so you can call them from anywhere and arguments pass straight through (`./run.sh --name 거실PC --share ~/Downloads`).

To skip the launcher, run from inside the project directory:
```bash
python3 -m mdirnet
```

Start it on both PCs and they find each other automatically.

## Shared folders

You can register **several** folders to open up to the other side. The list persists across runs, and the peer sees its entries as folders in a **virtual root.**

```
what I manage                         what the peer sees
  Downloads  -> ~/Downloads          ⌜-beta  / ----------------⌝
  프로젝트    -> ~/works/proj_03      |  Downloads/            |
  photos     -> /Volumes/USB/photo   |  photos/               |
                                     |  프로젝트/               |
                                     ⌞-3 items ---------------⌟
```

The peer cannot climb above that list, and a folder that is not on it is refused even if named directly. **Your host paths are never sent** — every path on the wire is relative and starts with a share name, like `프로젝트/src/main.py`.

### Managing the list (F4)

```
⌜- 공유 폴더 관리 --------------------------------⌝
| 상대에게는 이 목록이 최상위 폴더로 보입니다.    |
| ---------------------------------------------- |
|   Downloads          ~/Downloads               |
|   프로젝트            ~/works/proj_03           |
|   photos      (없음)  /Volumes/USB/photo        |
|                                                |
| ---------------------------------------------- |
| a 추가   d 삭제   Esc 닫기                      |
⌞------------------------------------------------⌟
```

- **`a`** or `Insert` — open the folder picker and add to the list
- **`d`** or `Delete` — drop from the list. **The folder and its files stay where they are**
- `(없음)` means "missing", as when an external disk has been unplugged. It stays on the list but is hidden from the peer

On first run, if the list is empty, a folder picker opens to take the first entry. **Nothing is exposed to the network until at least one folder is settled.**

The list can be edited **while connected.** The session reads the live list, so an addition or removal takes effect on the peer's very next request. The peer's screen keeps showing the old listing until it refreshes, but entering a share you just removed is refused.

### Where it is stored

`~/.mdirnet/shares.json`

```json
[
  { "name": "Downloads", "path": "/Users/you/Downloads" }
]
```

Names come from the folder name, with a number appended on collision (`Downloads (2)`).

### Passing folders as arguments

```bash
./run.sh --share ~/Downloads --share ~/works/proj_03
```

Folders given with `--share` **apply to that run only and never touch the saved list** — safe for testing or one-off use.

## How you use it

1. Run `./run.sh` on both PCs and register a folder to share (F4 adds more).
2. On one side press **`n`** for the list of discovered PCs, pick the peer, press **Enter**.
3. An approval dialog appears on the peer's screen. **Y** accepts, **N** refuses; you can also pick with `< >` and confirm with **Enter**.
4. The right pane shows the peer's shared folders. **Enter** goes inside.
5. **Tab** switches panes, **Space** picks files, **5** copies them to the other side.

Files cannot live in the share list itself. Go **inside** a shared folder to copy.

**The left pane is always my PC.** The right one shows either the peer or **my PC**, toggled with `=`. With both sides local, copying is a plain local copy that never touches the network. The left pane is not confined to the shared folders — you can walk anywhere on your own machine with `..`. The folders you share only define **what the peer may see.**

Sessions are symmetric: the side that approved can browse and fetch from the requester just the same.

## Menu bar (`9`, `F9`, `/`)

A Norton Commander style pull-down menu. `<` `>` moves between menus, `Enter` runs an item. Any of `9`, `F9`, `/` opens it, and the same key closes it.

| Menu | Contents |
|---|---|
| **Left** | Left pane (my PC) — sort, refresh, folder tree, drives |
| **Files** | View file, copy, move, rename, make directory, delete, help, quit |
| **Commands** | Find a PC to connect to, disconnect, manage shared folders |
| **Options** | Show / hide dotfiles |
| **Right** | Right pane (peer) — sort, refresh |

As in NC, `Left` and `Right` configure **their own pane.** Only the left has a drives item, because the peer side is confined to shared folders and has no notion of drives.

The top of the `Right` menu toggles **my PC / peer** (`=`). When the right pane is showing my PC, it gets a drives item too.

**Connect and disconnect are not on the bottom bar.** To keep NC's ten slots intact they live in the `Commands` menu and on the letter keys `n` / `d`. Refresh (`r`) and drives (`\`) are in the `Left`/`Right` menus for the same reason. Slot 6 is NC's `RenMov` position, so it renames.

## Key bindings

**Every function key can be pressed as the digit printed on the bottom bar.** macOS needs `fn` for F-keys and their escape sequences differ between terminals, but a digit arrives as one unchanged ASCII byte on every OS and terminal, and it survives a Korean input method. (Top-row digits only — the numeric keypad sends escape sequences on some terminals.)

| Key | Action |
|---|---|
| `Tab` | Switch pane |
| `↑` `↓` / `k` `j` | Move cursor |
| `PgUp` `PgDn` | Move by page |
| `Home` `End` | First / last entry |
| `Enter` | Open directory; run the file if it is executable, otherwise view it |
| `←` / `h` | Parent directory |
| `→` / `l` | Into the folder (nothing on a file) |
| `Space` | Toggle selection (multiple allowed) |
| `Insert` | Toggle selection (same as Space) |
| `+` `-` | Select / deselect by mask (`*.txt`) |
| `1` / `F1` / `?` | Help |
| `2` / `F2` / `s` | Change sort order |
| `3` / `F3` / `v` | View file (text or hex) |
| `4` / `F4` / `o` | Manage shared folders |
| `5` / `F5` / `c` | Copy to the other pane — directories recursively |
| `x` | **Move** to the other pane — the source is removed after the move |
| `t` | **Folder tree** — see the whole disk as a tree and jump there (MDir's MCD) |
| `6` / `F6` | Rename |
| `7` / `F7` / `m` | Make directory |
| `8` / `F8` / `Delete` | Delete (cannot be undone) |
| `9` / `F9` / `/` | Menu bar |
| `\` | Switch drive / volume (my PC side only) |
| `r` | Refresh |
| `n` | Find a PC to connect to |
| `d` | Disconnect |
| `.` | Show / hide dotfiles |
| `=` | Toggle the right pane between my PC and the peer |
| `Esc` (during a transfer) | Cancel the transfer |
| `Backspace` | Parent directory (the `delete` key on a Mac) |
| `0` / `F10` / `q` | Quit |

Because every action has a digit or letter alternative, nothing is out of reach on a terminal that swallows F-keys. In dialogs that take text — a folder name, a mask — digits are just characters, so `2025` and `*.mp3` type through normally.

**Shortcuts work with a Korean input method active.** On a 두벌식 keyboard, pressing `y` delivers `ㅛ` to the terminal; where a keystroke is read as a command it is mapped back. Text fields do not map it back, so Korean folder names type through. Confirmation dialogs also accept `← →` plus `Enter`, which works whatever the input method is doing.

Sort order cycles name → extension → size → date, and directories group at the top under every order. `+` and `-` take patterns like `*.txt` to select or deselect many entries at once.

## Running a file with Enter

If the file under the cursor is executable — the `x` bit on POSIX, a `PATHEXT` extension on Windows — **Enter runs it** in the background, and the status line records it. Anything else opens in the viewer, as before. F3 always views, even for an executable.

Output is discarded so a child process cannot scribble over the TUI, and the child is detached from us so quitting MDirNet does not take it down. Remote files are never run: the directory listing carries no permission bits, so there is no way to tell whether a peer's file is executable. Copy it over first.

## Drives / volumes

On Windows the parent of `C:\` is itself, so `..` **cannot reach D:.** The `\` key picks a drive instead.

```
⌜- 드라이브 선택 --------------------⌝
|   C:   C:\   . 고정               |
|   D:   D:\   . 이동식             |
|   Z:   Z:\   . 네트워크           |
|                                   |
| Enter 이동   Esc 취소              |
⌞-----------------------------------⌟
```

**The share picker (F4) has the same door** — press `\` or open the `[드라이브]` entry at the top. Without it there would be no way to share a folder on D:.

On macOS and Linux the same screen lists `/` and mounted volumes (`/Volumes`, `/media`, `/mnt`), for jumping straight to an external disk.

It does not apply to the right pane when that pane shows the peer: they are confined to shared folders, so moving to another drive would be refused immediately.

## Options

```bash
python3 -m mdirnet --help
```

| Option | Meaning | Default |
|---|---|---|
| `--name` | Name shown to the peer | host name |
| `--share` | Folder to open up (repeatable, not saved) | the saved list |
| `--root` | Same as one `--share` (compatibility) | — |
| `--port` | Session TCP port | 9600 |
| `--udp-port` | Discovery UDP port | 9601 |
| `--no-discovery` | Turn off automatic discovery | off |
| `--theme` | Color set, `black` or `blue` (not saved) | the saved choice |

```bash
./run.sh --share ~/Downloads --name 거실PC
```

## Only characters of settled width reach the screen

Borders are drawn with the corner glyphs `⌜⌝⌞⌟` (U+231C–231F) and ASCII `- | =`; everything else on screen is ASCII (`~ . * o _ #`). The test is not "is it ASCII" but **"is its width settled."**

All **112 box-drawing characters** (U+2500–257F) such as `─│┌┐` have East Asian Width `A` (Ambiguous). Terminals in a Korean or CJK environment draw them **two columns** wide, so counting them as one shifts or overflows the line. That single cause produced three separate symptoms here: an ellipsis (`…`) pushed lines one column over, two `·` in the header truncated two digits of the port number, and boxes failed to close in the Windows console.

MDir's angular borders were not ASCII either — they came from the upper half of **CP437**, the IBM extended character set. The 95 printable ASCII characters have no corner shapes at all, so `+` is the only substitute; the dedicated corner glyphs `⌜⌝⌞⌟` have EAW `N`, which guarantees one column, so they are safe.

Filenames containing ambiguous characters cannot be avoided, so **anything ambiguous is counted as two columns.** A gap is better than an overflow that breaks the screen. `tests/test_width_safety.py` keeps ambiguous characters from creeping back into the source.

## How it works

- **Discovery** — a UDP broadcast every 2 seconds announces this node; a peer unheard from for 6 seconds drops off the list. The announcement goes out **once per interface** (see below).
- **Connection** — an incoming request raises an approval dialog on the peer's screen. No answer within 30 seconds counts as a refusal.
- **Transfer** — data lands in a `.part` file that is renamed on completion, so a broken transfer never leaves a half file behind.

## Android

The Android client lives in a **separate repository**. It speaks the same protocol, so a
phone is a full peer: it can browse, download from and upload to a PC, and a PC can do the
same to the phone.

- Shared folders are picked with the system folder picker (Storage Access Framework).
  Only the folders you pick are opened, and nothing outside them can be reached.
- A foreground service keeps the listener alive so a PC can fetch files while the app is
  in the background. Approval is asked with a notification when the app is not on screen.
- The cross-check vectors the two implementations share live in the Android repository.
  When the wire format changes, keep both checkouts side by side, regenerate with
  `python3 tools/gen-golden.py --out <android>/app/src/test/resources/golden`, and run
  both test suites.

The wire format both implementations follow is written down in [PROTOCOL.md](PROTOCOL.md).

## Security scope

This was built for the inside of one router. There is no protection beyond that line.

- A connection requires the **peer's explicit approval.** There is no PIN or password.
- What the peer can reach is **inside the registered shared folders.** A name not on the list, `../..`, and symlinks pointing outward are all refused.
- The peer cannot delete a shared folder itself. Removing it from the list happens only on my side.
- **Transfers are encrypted with TLS 1.3.** Capturing packets on the same network reveals nothing. The certificate is generated at start-up and never stored.
- **But the peer is not authenticated.** The certificate is self-signed, so there is no chain to verify and an active man-in-the-middle (ARP spoofing and the like) is not stopped. To check, compare the **device fingerprint** shown on both screens. Do not use this on a network you do not trust (a café, public Wi-Fi).
- **F8 delete applies remotely too.** An approved peer can delete files **inside** my shared folders. The shared folder itself, and anything outside it, cannot be deleted.

## Firewall

If the two sides cannot find each other, check the firewall.

- **UDP 9601** — discovery. Blocked, and the `n` list stays empty.
- **TCP 9600** — sessions. Blocked, and connection requests fail.

macOS asks on first run whether to accept incoming connections. On a network that blocks broadcast, such as many corporate networks, automatic discovery will not work.

### Opening the ports — on Windows the app offers

On Windows, if the allow rules are missing at startup, the app asks once. Agree and a Windows consent (UAC) prompt appears; once the rules are in, it stops asking. Changing firewall rules is administrator-only by Windows design, so there is no way to open them silently.

If a **block rule** for `python.exe` exists, the app lists it and removes it only after you agree. Windows evaluates block rules ahead of allow rules, so while one remains, opening the ports has no effect. That rule is what gets created when you press Cancel on the "Windows Defender Firewall has blocked some features of this app" prompt.

### Opening the ports by hand

```powershell
# Windows — run PowerShell as administrator
.\tools\allow-firewall.ps1          # allow (undo with -Remove)
```

```sh
# macOS
sh tools/allow-firewall.sh          # allow (undo with --remove)
```

On Windows this opens inbound TCP 9600–9609 (the session port climbs by one if it is taken) and UDP 9601, for the **private and domain** profiles only. Transfers are encrypted but the peer is not authenticated, so the public profile is deliberately left out.

The macOS firewall decides per executable rather than per port, so the script adds the python binary that runs the app to the allow list. Run it again if you switch to a different python.

### When only one side finds the other

Discovery announcements are sent per interface. A single `sendto("255.255.255.255")` leaves through **one** interface chosen by the routing table, and on a Windows PC with Hyper-V, WSL, VirtualBox, or VPN adapters that interface is often not the LAN — while receiving works on every interface, which is exactly why the traffic looked one-way. If one side still cannot see the other:

1. **Check inbound UDP 9601 on the side that cannot see** — inbound rules in Windows Defender Firewall; on macOS, System Settings > Network > Firewall.
2. **Check that both PCs are on the same subnet** — guest Wi-Fi and range extenders sit behind routers that do not carry broadcast across.
3. Let the side that *can* see start the connection. Sessions are symmetric, so it does not matter who asks. Connecting by typing an IP directly is not supported yet.

## Tests

```bash
python3 -m unittest discover -s tests -t . -v
```

597 tests cover frame round-trips, path-escape refusals, the approval handshake, simultaneous transfers in both directions, per-interface discovery announcements, and column-width arithmetic. `tests/test_integration.py` stands up two nodes over real TCP and UDP and drives them from discovery through a recursive copy.

## Verified environments

On macOS (Apple Silicon, Python 3.14) two processes were run for real: discovery → approval → recursive copy of nested directories with Korean names. Linux takes the same POSIX paths and behaves the same.

**Windows has the code paths but has not been run.** Keys are read through `msvcrt` and ANSI handling is enabled with `ctypes`; it needs Windows Terminal or a Windows 10-or-later console. The firewall helper and the UAC prompt are likewise untested on real hardware.
