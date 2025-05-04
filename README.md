# nictl
nictl communicates with `niri` through its socket and brings some opinionated commands. This could be shell scripts, but it's not.

## Commands
| Command | Arguments | Description | Example |
| --- | --- | --- | --- |
| `cycle-workspace` | direction: `up` or `down`. | Move focus to the next workspace, up or down. Will wrap if at first or last workspace. | `nictl cycle-workspace up` |
| `spawn-or-focus` | app_id: ID of the app, i.e. "firefox". cmd: the command to run if no window is found. | If an application is already running, focus the window, otherwise spawn it. If already focused, focus previous window. | `nictl spawn-or-focus Signal signal-desktop` |

## Installation

### pipx
`pipx install git+https://github.com/stefur/nictl`
