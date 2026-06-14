"""Interactive prompting. The wizard talks to a Prompter, so tests can drive it with a
scripted fake (see tests) while the real run uses RichPrompter. Mirrors the injectable-
prompter pattern from the archived harness builder.
"""
from __future__ import annotations

from typing import Protocol, Sequence


class Prompter(Protocol):
    def info(self, message: str) -> None: ...
    def warn(self, message: str) -> None: ...
    def ask(self, label: str, default: str | None = None) -> str: ...
    def confirm(self, label: str, default: bool = True) -> bool: ...
    def choice(self, label: str, options: Sequence[str], default: str | None = None) -> str: ...
    def select_many(self, label: str, options: Sequence[str],
                    default: Sequence[str] | None = None) -> list[str]: ...


def _parse_selection(raw: str, options: list[str]) -> list[str] | None:
    """Parse a select_many response. Returns None if nothing valid was given."""
    raw = raw.strip().lower()
    if raw == "all":
        return list(options)
    if raw == "none":
        return []
    picked: list[str] = []
    for part in raw.replace(" ", "").split(","):
        if part.isdigit() and 1 <= int(part) <= len(options):
            value = options[int(part) - 1]
            if value not in picked:
                picked.append(value)
    return picked or None


def _enable_vt() -> bool:
    """Enable ANSI/VT escape processing on the Windows console so the in-place redraw
    works. No-op (and True) on POSIX. Returns False if it definitely couldn't be enabled,
    so the caller can fall back to a non-cursor prompt."""
    import sys
    if sys.platform != "win32":
        return True
    try:
        import ctypes
        kernel32 = ctypes.windll.kernel32
        handle = kernel32.GetStdHandle(-11)  # STD_OUTPUT_HANDLE
        mode = ctypes.c_uint32()
        if not kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
            return False
        ENABLE_VIRTUAL_TERMINAL_PROCESSING = 0x0004
        return bool(kernel32.SetConsoleMode(handle, mode.value | ENABLE_VIRTUAL_TERMINAL_PROCESSING))
    except Exception:
        return False


def _read_key() -> str | None:
    """Read one logical keypress from the console and map it to an action name:
    'up' | 'down' | 'space' | 'enter' | 'all' | 'invert' | 'abort', or None if unmapped.
    Uses msvcrt on Windows and raw termios on POSIX (no third-party deps)."""
    import sys
    if sys.platform == "win32":
        import msvcrt
        ch = msvcrt.getwch()
        if ch in ("\x00", "\xe0"):           # arrow / function key prefix
            ch2 = msvcrt.getwch()
            return {"H": "up", "P": "down"}.get(ch2)
        if ch == " ":
            return "space"
        if ch in ("\r", "\n"):
            return "enter"
        if ch == "\x03":                      # Ctrl-C
            return "abort"
        return {"a": "all", "i": "invert", "j": "down", "k": "up"}.get(ch.lower())
    # POSIX
    import termios
    import tty
    fd = sys.stdin.fileno()
    old = termios.tcgetattr(fd)
    try:
        tty.setraw(fd)
        ch = sys.stdin.read(1)
        if ch == "\x1b":                      # escape sequence (arrows)
            seq = sys.stdin.read(2)
            return {"[A": "up", "[B": "down"}.get(seq)
        if ch == " ":
            return "space"
        if ch in ("\r", "\n"):
            return "enter"
        if ch in ("\x03", "\x04"):            # Ctrl-C / Ctrl-D
            return "abort"
        return {"a": "all", "i": "invert", "j": "down", "k": "up"}.get(ch.lower())
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old)


class RichPrompter:
    """Terminal prompter built on rich. Imported lazily so importing this module never
    requires rich (tests use the fake)."""

    def __init__(self) -> None:
        from rich.console import Console
        self._console = Console()

    def info(self, message: str) -> None:
        self._console.print(message)

    def warn(self, message: str) -> None:
        self._console.print(f"[yellow]{message}[/yellow]")

    def ask(self, label: str, default: str | None = None) -> str:
        from rich.prompt import Prompt
        return Prompt.ask(label, default=default)

    def confirm(self, label: str, default: bool = True) -> bool:
        from rich.prompt import Confirm
        return Confirm.ask(label, default=default)

    def choice(self, label: str, options: Sequence[str], default: str | None = None) -> str:
        options = list(options)
        from rich.prompt import Prompt
        self._console.print(f"[bold]{label}[/bold]")
        for i, opt in enumerate(options, 1):
            marker = "  [dim](default)[/dim]" if opt == default else ""
            self._console.print(f"  {i}. {opt}{marker}")
        default_idx = str(options.index(default) + 1) if default in options else None
        while True:
            raw = (Prompt.ask("  Enter number", default=default_idx) or "").strip()
            if raw.isdigit() and 1 <= int(raw) <= len(options):
                return options[int(raw) - 1]
            if raw in options:
                return raw
            self._console.print("[red]  invalid choice[/red]")

    @staticmethod
    def _interactive() -> bool:
        """True when stdin is a real terminal, so the interactive toggle list can read
        line input. Piped/redirected/CI runs fall back to the one-shot numbered prompt."""
        import sys
        try:
            return sys.stdin.isatty() and sys.stdout.isatty()
        except (ValueError, AttributeError):
            return False

    def select_many(self, label: str, options: Sequence[str],
                    default: Sequence[str] | None = None) -> list[str]:
        options = list(options)
        default = list(default) if default else []
        if self._interactive() and _enable_vt():
            try:
                return self._select_many_interactive(label, options, default)
            except SystemExit:
                raise
            except Exception:
                # Console can't drive the cursor UI (no msvcrt/termios, odd terminal) -
                # fall back to a plain numbered prompt rather than failing.
                self.warn("(interactive selection unavailable; using numbered input)")
        return self._select_many_numbered(label, options, default)

    def _select_many_interactive(self, label: str, options: list[str],
                                 default: list[str]) -> list[str]:
        """In-place cursor selector: arrow keys (or j/k) move, SPACE toggles the pointed
        row, 'a' all, 'i' invert, ENTER confirms (>=1 selected), Ctrl-C aborts. Redraws in
        place via ANSI - no scrolling, no reprinting. Selected rows show a green (*)."""
        import sys
        ESC = "\x1b"
        GREEN, DIM, CYAN, BOLD, RESET = (f"{ESC}[1;32m", f"{ESC}[2m", f"{ESC}[36m",
                                         f"{ESC}[1m", f"{ESC}[0m")
        HELP = "up/down move, SPACE toggle, a all, i invert, ENTER confirm, Ctrl-C abort"
        selected = {o for o in options if o in default}
        cursor = 0
        n = len(options)
        status = HELP
        out = sys.stdout
        out.write(f"\n{BOLD}{label}{RESET}\n")
        drawn = False
        while True:
            if drawn:
                out.write(f"{ESC}[{n + 1}A")          # move up over the list + status line
            for i, opt in enumerate(options):
                on = opt in selected
                mark = f"{GREEN}(*){RESET}" if on else f"{DIM}( ){RESET}"
                ptr = f"{CYAN}>{RESET}" if i == cursor else " "
                name = f"{GREEN}{opt}{RESET}" if on else opt
                out.write(f"{ESC}[2K  {ptr} {mark} {name}\n")   # [2K = clear whole line
            out.write(f"{ESC}[2K{DIM}  {status}{RESET}\n")
            out.flush()
            drawn = True

            try:
                key = _read_key()
            except KeyboardInterrupt:
                key = "abort"

            if key == "up":
                cursor = (cursor - 1) % n
            elif key == "down":
                cursor = (cursor + 1) % n
            elif key == "space":
                selected ^= {options[cursor]}
            elif key == "all":
                selected = set() if len(selected) == n else set(options)
            elif key == "invert":
                selected = {o for o in options if o not in selected}
            elif key == "enter":
                if selected:
                    return [o for o in options if o in selected]
                status = "Select at least one item (SPACE), or Ctrl-C to abort."
                continue
            elif key == "abort":
                out.write("\n")
                raise SystemExit("Aborted - selection cancelled.")
            status = HELP

    def _select_many_numbered(self, label: str, options: list[str],
                              default: list[str]) -> list[str]:
        from rich.prompt import Prompt
        self._console.print(f"[bold]{label}[/bold]  [dim](numbers comma-separated, 'all', or 'none')[/dim]")
        for i, opt in enumerate(options, 1):
            marker = "  [dim]*[/dim]" if opt in default else ""
            self._console.print(f"  {i}. {opt}{marker}")
        if default and len(default) == len(options):
            default_str = "all"
        elif default:
            default_str = ",".join(str(options.index(d) + 1) for d in default)
        else:
            default_str = "none"
        while True:
            raw = Prompt.ask("  Select", default=default_str)
            parsed = _parse_selection(raw, options)
            if parsed is not None:
                return parsed
            self._console.print("[red]  nothing valid selected[/red]")
