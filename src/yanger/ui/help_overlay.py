"""Help overlay widget for YouTube Ranger.

Displays keybindings and commands from the central registry, with a `less`-style incremental
search: press `/`, type a query, Enter to jump to the first match (all matches highlighted),
then `n`/`N` to cycle forward/back. Esc cancels the search; Esc/?/q close the overlay.
"""

import re

from rich.text import Text
from rich.markup import escape

from textual.app import ComposeResult
from textual.containers import Vertical, ScrollableContainer
from textual.screen import ModalScreen
from textual.widgets import Static, Input
from textual import events

from ..keybindings import registry

SEARCH_HIGHLIGHT = "black on yellow"


class HelpOverlay(ModalScreen):
    """Modal help screen showing keybindings and commands.

    A ModalScreen (not an embedded Container) so it OWNS the keyboard while open — arrow/j/k
    scroll the help, not the miller view behind it, which was the bug when this was a
    display-toggled Container sharing the screen with the app's on_key handler.
    """

    DEFAULT_CSS = """
    HelpOverlay {
        align: center middle;
    }

    HelpOverlay > Vertical {
        width: 80%;
        height: 80%;
        background: $surface;
        border: double $primary;
        padding: 1;
    }
    
    HelpOverlay .help-header {
        text-style: bold;
        text-align: center;
        margin-bottom: 1;
        background: $primary;
        color: $text;
        padding: 0 1;
        height: 3;
        content-align: center middle;
    }
    
    HelpOverlay .help-content {
        height: 1fr;
        padding: 0 2;
    }
    
    HelpOverlay .help-category {
        text-style: bold;
        color: $primary;
        margin-top: 1;
    }
    
    HelpOverlay .help-item {
        margin-left: 2;
    }
    
    HelpOverlay .help-key {
        color: $warning;
        text-style: bold;
        width: 15;
    }
    
    HelpOverlay .help-description {
        color: $text;
    }
    
    HelpOverlay .help-footer {
        /* height 3 = border-top(1) + padding-top(1) + ONE content row. At height:2 the text row
           was squeezed to zero, so the footer (and the search match-count) never painted. */
        height: 3;
        text-align: center;
        border-top: solid $primary;
        padding-top: 1;
        color: $text-muted;
    }

    HelpOverlay .command-section {
        border-top: solid $primary;
        margin-top: 2;
        padding-top: 1;
    }
    
    HelpOverlay .command-item {
        margin-left: 2;
        margin-bottom: 1;
    }
    
    HelpOverlay .command-name {
        color: $success;
        text-style: bold;
    }
    
    HelpOverlay .command-syntax {
        color: $text-muted;
        margin-left: 4;
    }

    /* less-style search box: docked above the footer, hidden (display:none) until '/'. Height 3
       (bordered) so there is a full content row — never the height:1+border clip. */
    HelpOverlay #help-search {
        height: 3;
        border: solid $accent;
        margin: 0 1;
    }
    """
    
    def compose(self) -> ComposeResult:
        """Create help overlay layout."""
        # Parse the markup ONCE; searching highlights spans on this and reads .plain for matches.
        self._raw_markup = self._generate_help_content()
        self._matches: list[int] = []     # line indices (in .plain) containing the current query
        self._match_idx = 0
        self._query = ""
        with Vertical():
            # Header
            yield Static(
                "🎮 YouTube Ranger - Help",
                classes="help-header"
            )

            # Scrollable content
            with ScrollableContainer(classes="help-content"):
                yield Static(Text.from_markup(self._raw_markup), id="help-body")

            # less-style search box (hidden until '/')
            yield Input(placeholder="Search help… (Enter to find, Esc to cancel)", id="help-search")

            # Footer (dynamic: shows match count during search)
            yield Static(self._footer_text(), classes="help-footer", id="help-footer", markup=True)
    
    def _generate_help_content(self) -> str:
        """Generate help content from keybinding registry."""
        lines = []
        
        # Group keybindings by category
        categories = registry.get_bindings_by_category()
        
        # NB: every DYNAMIC value is rich.markup.escape()d — descriptions, command syntax
        # (e.g. ":move <id> [playlist]") and the context annotation ("[video]") all contain '['
        # that Rich would otherwise parse as a style tag (MissingStyle at render). Only the
        # literal [bold ...] tags below are real markup.
        for category in sorted(categories.keys()):
            # Category header
            lines.append(f"[bold yellow]{escape(category)}[/bold yellow]")
            lines.append("")

            # Sort bindings by key
            bindings = sorted(categories[category], key=lambda b: b.key)

            for binding in bindings:
                # Format key and description
                key_display = escape(binding.key).ljust(12)
                context = ""
                if binding.context.value != "global":
                    context = f" \\[{escape(binding.context.value)}]"   # literal "[context]"

                lines.append(
                    f"  [bold cyan]{key_display}[/bold cyan]  "
                    f"{escape(binding.description)}{context}"
                )

            lines.append("")

        # Add commands section
        lines.append("[bold yellow]Commands[/bold yellow] (access with ':')")
        lines.append("")

        for cmd in sorted(registry.get_all_commands(), key=lambda c: c.name):
            lines.append(
                f"  [bold green]:{escape(cmd.name)}[/bold green]  "
                f"{escape(cmd.description)}"
            )
            lines.append(f"    [dim]{escape(cmd.syntax)}[/dim]")

            # Show first example
            if cmd.examples:
                lines.append(f"    [dim italic]Example: {escape(cmd.examples[0])}[/dim italic]")
            lines.append("")
        
        # Add tips section
        lines.append("[bold yellow]Tips[/bold yellow]")
        lines.append("")
        lines.append("  • Use [bold]Space[/bold] to mark videos, then [bold]dd[/bold]/[bold]yy[/bold] to cut/copy")
        lines.append("  • [bold]V[/bold] enters visual mode for range selection")
        lines.append("  • [bold]v[/bold] inverts selection (marked ↔ unmarked)")
        lines.append("  • Search with [bold]/[/bold], navigate matches with [bold]n[/bold]/[bold]N[/bold]")
        lines.append("  • [bold]gn[/bold] creates a new playlist")
        lines.append("  • [bold]cw[/bold] renames current playlist or video")
        lines.append("  • [bold]u[/bold] undoes last operation, [bold]U[/bold] redoes")
        lines.append("  • [bold]r[/bold] opens video/playlist in browser")
        lines.append("  • Commands support tab completion and history")
        
        return "\n".join(lines)
    
    def on_mount(self) -> None:
        """Hide the search box and focus the scrollable content (arrow/pgup/pgdn scroll natively)."""
        self.query_one("#help-search", Input).display = False
        self.query_one(ScrollableContainer).focus()

    # ----- less-style search --------------------------------------------------------

    def _footer_text(self, extra: str = "") -> str:
        base = "[b]/[/b] search · [b]n[/b]/[b]N[/b] next/prev · [b]?[/b]/[b]ESC[/b] close"
        return f"{extra}   ·   {base}" if extra else base

    def _open_search(self) -> None:
        inp = self.query_one("#help-search", Input)
        inp.value = ""
        inp.display = True
        inp.focus()

    def _close_search(self, *, clear: bool) -> None:
        """Hide the box and return focus to the pager. clear=True also drops highlights/matches."""
        self.query_one("#help-search", Input).display = False
        if clear:
            self._query = ""
            self._matches = []
            self._apply_highlight("")
            self._update_footer()
        self.query_one(ScrollableContainer).focus()

    def _apply_highlight(self, query: str) -> None:
        """Re-render the body from the raw markup, overlaying match highlights (spans, not string
        edits, so they compose with the existing Rich styling and can't straddle a markup tag)."""
        text = Text.from_markup(self._raw_markup)
        if query:
            text.highlight_regex(re.compile(re.escape(query), re.IGNORECASE), style=SEARCH_HIGHLIGHT)
        self.query_one("#help-body", Static).update(text)

    def _run_search(self, query: str) -> None:
        query = query.strip()
        if not query:
            self._close_search(clear=True)
            return
        self._query = query
        self._apply_highlight(query)
        pattern = re.compile(re.escape(query), re.IGNORECASE)
        plain_lines = Text.from_markup(self._raw_markup).plain.split("\n")
        self._matches = [i for i, line in enumerate(plain_lines) if pattern.search(line)]
        self._match_idx = 0
        self._close_search(clear=False)     # hide box, keep highlights, refocus the pager
        if self._matches:
            self._scroll_to_match()
        self._update_footer()

    def _scroll_to_match(self) -> None:
        # y is a row offset; help lines are short/unwrapped, so the line index is the scroll target.
        self.query_one(ScrollableContainer).scroll_to(y=self._matches[self._match_idx], animate=False)

    def _goto_match(self, delta: int) -> None:
        if not self._matches:
            return
        self._match_idx = (self._match_idx + delta) % len(self._matches)
        self._scroll_to_match()
        self._update_footer()

    def _update_footer(self) -> None:
        if self._query and self._matches:
            extra = f"[b]{self._match_idx + 1}/{len(self._matches)}[/b] '{self._query}'"
        elif self._query:
            extra = f"[b red]no matches[/b red] '{self._query}'"
        else:
            extra = ""
        self.query_one("#help-footer", Static).update(self._footer_text(extra))

    async def on_key(self, event: events.Key) -> None:
        """Own the keyboard: `/` search, n/N cycle matches, scroll on arrow/j/k, dismiss on
        escape/?/q — and consume EVERY key so nothing leaks to the app behind the modal."""
        inp = self.query_one("#help-search", Input)
        if inp.display and inp.has_focus:
            # The Input (focused) already inserted printable keys via its own _on_key before this
            # bubbles up. We run the search on Enter ourselves — Input.Submitted fires from Enter's
            # *binding*, which is checked AFTER on_key handlers, so stopping the event here would
            # kill it. Reading inp.value directly avoids that ordering trap. Always stop() so a
            # typed key can't leak to the app's global bindings behind the modal.
            if event.key == "enter":
                self._run_search(inp.value)
            elif event.key == "escape":
                self._close_search(clear=True)
            event.stop()
            return

        scroll = self.query_one(ScrollableContainer)
        key = event.key
        if key == "slash":
            self._open_search()
        elif key in ("escape", "question_mark", "q"):
            self.dismiss()
        elif key == "n":
            self._goto_match(1)
        elif key in ("N", "shift+n"):
            self._goto_match(-1)
        elif key in ("down", "j"):
            scroll.scroll_down()
        elif key in ("up", "k"):
            scroll.scroll_up()
        elif key == "pagedown":
            scroll.scroll_page_down()
        elif key == "pageup":
            scroll.scroll_page_up()
        elif key == "home":
            scroll.scroll_home()
        elif key == "end":
            scroll.scroll_end()
        event.stop()