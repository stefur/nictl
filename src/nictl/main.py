import argparse
import json
import os
import sys
from socket import AF_UNIX, SOCK_STREAM, socket
from typing import Literal


def send_command(cmd: str | dict) -> dict:
    """Send the command over the socket and returns the JSON response"""
    if not (address := os.environ.get("NIRI_SOCKET")):
        raise Exception("Could not find socket address. Is niri running?")

    with socket(AF_UNIX, SOCK_STREAM) as client:
        client.connect(address)

        if isinstance(cmd, str):
            msg = f'"{cmd}"\n'.encode("utf-8")
        elif isinstance(cmd, dict):
            msg = (json.dumps(cmd) + "\n").encode("utf-8")

        client.sendall(msg)

        buffer = bytearray()

        while not buffer.endswith(b"\n"):
            if chunk := client.recv(1024):
                buffer.extend(chunk)
            else:
                # For any unexpected behaviour
                break

        json_response = json.loads(buffer)

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


def cycle_workspace(direction: Literal["up", "down"], skip_next_empty: bool) -> None:
    focused_output = send_command("FocusedOutput")["name"]

    workspaces = send_command("Workspaces")

    current_workspace = next(
        workspace["idx"]
        for workspace in workspaces
        if workspace["output"] == focused_output and workspace["is_active"]
    )

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

    args = parser.parse_args(args=(sys.argv[1:] or ["--help"]))

    match args.command:
        case "spawn-or-focus":
            spawn_or_focus(args.app_id, args.cmd)
        case "cycle-workspace":
            cycle_workspace(args.direction, args.skip_next_empty)
