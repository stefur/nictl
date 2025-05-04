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

        chunks = []

        while chunk := client.recv(1024):
            chunks.append(chunk)

        response = b"".join(chunks).decode("utf-8")

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
    focused_window = send_command("FocusedWindow")["app_id"]
    windows = send_command("Windows")

    window_id = [
        window["id"] for window in windows if window["app_id"].lower() in app_id.lower()
    ]
    if window_id:
        if focused_window == app_id:
            send_command({"Action": {"FocusWindowPrevious": {}}})
        else:
            send_command({"Action": {"FocusWindow": {"id": window_id[0]}}})

    else:
        send_command({"Action": {"Spawn": {"command": cmd.split()}}})


def cycle_workspace(direction: Literal["up", "down"]) -> None:
    focused_output = send_command("FocusedOutput")["name"]

    workspaces = send_command("Workspaces")

    current_workspace = [
        workspace["idx"]
        for workspace in workspaces
        if workspace["output"] == focused_output and workspace["is_active"] is True
    ][0]

    max_workspace = max(
        [
            workspace["idx"]
            for workspace in workspaces
            if workspace["output"] == focused_output
        ]
    )

    match direction:
        case "down":
            # If at max workspace, wrap to 1
            new_workspace = (
                1 if current_workspace == max_workspace else current_workspace + 1
            )
        case "up":
            new_workspace = (
                max_workspace if current_workspace == 1 else current_workspace - 1
            )

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

    args = parser.parse_args(args=(sys.argv[1:] or ["--help"]))

    match args.command:
        case "spawn-or-focus":
            spawn_or_focus(args.app_id, args.cmd)
        case "cycle-workspace":
            cycle_workspace(args.direction)
