import argparse
import json
import os
import sys
from contextlib import contextmanager
from socket import AF_UNIX, SOCK_STREAM, socket
from typing import Iterator, Literal


@contextmanager
def niri_socket_connection() -> Iterator[socket]:
    """Connect to the niri socket."""

    if not (address := os.environ.get("NIRI_SOCKET")):
        raise Exception("Could not find socket address. Is niri running?")

    with socket(AF_UNIX, SOCK_STREAM) as client:
        client.connect(address)
        yield client


def read_lines(client: socket) -> Iterator[bytearray]:
    """Read from niri socket until newline."""
    buffer = bytearray()

    # Waiting until the buffer ends with a newline.
    while not buffer.endswith(b"\n"):
        if chunk := client.recv(1024):
            buffer.extend(chunk)
        else:
            break

    # Splitting messages captured in the buffer, each separated by \n,
    # therefore splitting and yielding line by line
    for line in buffer.split(b"\n"):
        if line:
            yield line


@contextmanager
def event_stream() -> Iterator[dict]:
    """Getting the event stream from niri"""
    with niri_socket_connection() as client:
        client.sendall(b'"EventStream"\n')
        yield (json.loads(line) for line in read_lines(client))


def send_command(cmd: str | dict) -> dict:
    """Send the command over the socket and return the response"""

    with niri_socket_connection() as client:
        if isinstance(cmd, str):
            msg = f'"{cmd}"\n'.encode("utf-8")
        elif isinstance(cmd, dict):
            msg = (json.dumps(cmd) + "\n").encode("utf-8")

        client.sendall(msg)

        # Expecting a single line in return
        response = next(read_lines(client))

        json_response = json.loads(response)

        # All responses are returned "Ok" or "Err", try to unpack the response
        if ok_result := json_response.get("Ok"):
            # Not pretty but unpack the toplevel key if command is a msg (str), otherwise its assumed to be an action (dict) and will be returned as is
            return ok_result[cmd] if isinstance(cmd, str) else ok_result
        else:
            error = json_response.get("Err")
            raise Exception(f"Something went wrong:\n{error}")


def spawn_or_focus(app_id: str, cmd: str) -> None:
    """Check if the app is already running, then focus it, otherwise run the supplied command."""

    focused_window = send_command("FocusedWindow")
    focused_window_app_id = focused_window.get("app_id") if focused_window else None

    windows = send_command("Windows")

    window_id = [
        window["id"] for window in windows if window["app_id"].lower() in app_id.lower()
    ]
    if window_id:
        if focused_window_app_id == app_id:
            send_command({"Action": {"FocusWindowPrevious": {}}})
        else:
            send_command({"Action": {"FocusWindow": {"id": window_id[0]}}})

    else:
        send_command({"Action": {"Spawn": {"command": cmd.split()}}})


def occupied_workspaces() -> list[int]:
    windows = send_command("Windows")
    windows_workspace_id = list(set([window.get("workspace_id") for window in windows]))
    workspaces = send_command("Workspaces")
    return sorted([ws["idx"] for ws in workspaces if ws["id"] in windows_workspace_id])


def focused_workspace() -> int:
    focused_output = send_command("FocusedOutput")["name"]

    workspaces = send_command("Workspaces")

    current_workspace = next(
        workspace["idx"]
        for workspace in workspaces
        if workspace["output"] == focused_output and workspace["is_active"]
    )

    return current_workspace


def cycle_workspace(direction: Literal["up", "down"], skip_next_empty: bool) -> None:
    focused_output = send_command("FocusedOutput")["name"]

    workspaces = send_command("Workspaces")

    current_workspace = focused_workspace()

    max_workspace = max(
        [
            workspace["idx"]
            for workspace in workspaces
            if workspace["output"] == focused_output
        ]
    )

    occupied = occupied_workspaces() if skip_next_empty else None

    def next_workspace(current: int, step: int) -> int:
        if occupied is None or current in occupied:
            target = current + step
        elif (current + step) not in occupied:
            # Find nearest occupied workspace to target
            target = (
                min([w for w in occupied if w > current], default=occupied[0])
                if step > 0
                else max([w for w in occupied if w < current], default=occupied[-1])
            )
        else:
            target = current + step

        # Do the wrap around to first/last
        if target < 1:
            target = max_workspace
        elif target > max_workspace:
            target = 1

        return target

    match direction:
        case "down":
            new_workspace = next_workspace(current_workspace, 1)

        case "up":
            new_workspace = next_workspace(current_workspace, -1)

    send_command(
        {"Action": {"FocusWorkspace": {"reference": {"Index": new_workspace}}}}
    )


def calculate_pixel_widths(usable_width: int, n_columns: int) -> list[int]:
    base = usable_width // n_columns
    remainder = usable_width % n_columns

    # Give each window the base, distribute remainder to first N windows in columns
    return [base + (1 if i < remainder else 0) for i in range(n_columns)]


def fit_all_windows(gaps: int) -> None:
    output_width = send_command("FocusedOutput")["logical"]["width"]

    current_workspace = focused_workspace()
    windows = send_command("Windows")

    focused_window_id = send_command("FocusedWindow")["id"]

    # The windows on the current workspace, sorted in the order
    windows_on_current_workspace = sorted(
        [window for window in windows if window["workspace_id"] == current_workspace],
        key=lambda x: x["layout"]["pos_in_scrolling_layout"],
    )

    # Get the number of columns on the workspace, this is what we split the width on
    n_columns = len(
        {
            window["layout"]["pos_in_scrolling_layout"][0]
            for window in windows_on_current_workspace
        }
    )

    # Calculate the usable width based on the gaps
    usable_width = output_width - (gaps * (n_columns + 1))

    # The window widths to use based on the number of columns on the workspace and the usable width
    widths = calculate_pixel_widths(usable_width, n_columns)

    # Keep track of the window ids that are actually resized
    resized_window_ids = set()

    for window, width in zip(windows_on_current_workspace, widths):
        current_width = window["layout"]["window_size"][0]

        # If this window is already at the expected width we pass it
        if current_width == width:
            continue

        # Else adjust the width
        send_command(
            {
                "Action": {
                    "SetWindowWidth": {
                        "id": window["id"],
                        "change": {"SetFixed": width},
                    }
                }
            }
        )

        # All resized windows go to the set
        resized_window_ids.add(window["id"])

    with event_stream() as events:
        # Now check the event streams for all layout changes, this is to make sure niri has finished handling our requests before we finish up
        seen = set()
        for event in events:
            # If we have not performed any resizes, just bail
            if not resized_window_ids:
                break
            # We want to check for when layout is changed for our windows
            if "WindowLayoutsChanged" in event:
                for window_id, _ in event["WindowLayoutsChanged"]["changes"]:
                    if window_id in resized_window_ids:
                        seen.add(window_id)
                # When we've seen all expected window ID events we are finished
                if seen == resized_window_ids:
                    break

    # A second pass after all windows are adjusted to ensure they are all visible
    for window in windows_on_current_workspace:
        send_command(
            {
                "Action": {
                    "FocusWindow": {
                        "id": window["id"],
                    }
                }
            }
        )

    # Then go back to the originally focused window
    send_command(
        {
            "Action": {
                "FocusWindow": {
                    "id": focused_window_id,
                }
            }
        }
    )


def main():
    parser = argparse.ArgumentParser(
        description="Send commands to niri over its socket."
    )

    sub_parsers = parser.add_subparsers(title="Commands", dest="command", required=True)

    # Spawn or focus
    parser_spawn_or_focus = sub_parsers.add_parser(
        "spawn-or-focus",
        help="Spawn a program or focus the window if it's already running. If already focus, focus previous window.",
    )
    parser_spawn_or_focus.add_argument(
        "app_id", type=str, help="The app_id to look for."
    )

    parser_spawn_or_focus.add_argument(
        "cmd",
        type=str,
        help="The command to run if no window is found for the app_id.",
    )

    # Cycle workspaces
    parser_cycle_workspace = sub_parsers.add_parser(
        "cycle-workspace",
        help="Cycle the workspaces up or down. Will wrap if at first or last workspace.",
    )
    parser_cycle_workspace.add_argument(
        "direction",
        type=str,
        help="The direction to move (up/down).",
    )
    parser_cycle_workspace.add_argument(
        "--skip-next-empty",
        action="store_true",
        help="Skip next workspace if both next and  current workspace is empty.",
    )

    # Fit all windows
    parser_fit_all_windows = sub_parsers.add_parser(
        "fit-all-windows",
        help="Resize all windows' widths on the current worksapce so that they all fit on the screen.",
    )

    parser_fit_all_windows.add_argument(
        "gaps",
        type=int,
        help="The gaps from the niri config so that the window sizes are correctly adjusted.",
    )

    args = parser.parse_args(args=(sys.argv[1:] or ["--help"]))

    match args.command:
        case "spawn-or-focus":
            spawn_or_focus(args.app_id, args.cmd)
        case "cycle-workspace":
            cycle_workspace(args.direction, args.skip_next_empty)
        case "fit-all-windows":
            fit_all_windows(args.gaps)
