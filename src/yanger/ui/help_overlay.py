"""Help overlay widget for YouTube Ranger.

Displays keybindings and commands from the central registry.
"""
# Modified: 2025-08-08

from textual.app import ComposeResult
from textual.containers import Vertical, ScrollableContainer
from textual.screen import ModalScreen
from textual.widgets import Static
from textual import events

from ..keybindings import registry


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
        height: 2;
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
    """
    
    def compose(self) -> ComposeResult:
        """Create help overlay layout."""
        with Vertical():
            # Header
            yield Static(
                "🎮 YouTube Ranger - Help",
                classes="help-header"
            )
            
            # Scrollable content
            with ScrollableContainer(classes="help-content"):
                # Generate help content from registry
                content = self._generate_help_content()
                yield Static(content, markup=True)
            
            # Footer
            yield Static(
                "Press '?' or ESC to close",
                classes="help-footer"
            )
    
    def _generate_help_content(self) -> str:
        """Generate help content from keybinding registry."""
        lines = []
        
        # Group keybindings by category
        categories = registry.get_bindings_by_category()
        
        for category in sorted(categories.keys()):
            # Category header
            lines.append(f"[bold yellow]{category}[/bold yellow]")
            lines.append("")
            
            # Sort bindings by key
            bindings = sorted(categories[category], key=lambda b: b.key)
            
            for binding in bindings:
                # Format key and description
                key_display = binding.key.ljust(12)
                context = ""
                if binding.context.value != "global":
                    context = f" [{binding.context.value}]"
                    
                lines.append(
                    f"  [bold cyan]{key_display}[/bold cyan]  "
                    f"{binding.description}{context}"
                )
            
            lines.append("")
        
        # Add commands section
        lines.append("[bold yellow]Commands[/bold yellow] (access with ':')")
        lines.append("")
        
        for cmd in sorted(registry.get_all_commands(), key=lambda c: c.name):
            lines.append(
                f"  [bold green]:{cmd.name}[/bold green]  "
                f"{cmd.description}"
            )
            lines.append(f"    [dim]{cmd.syntax}[/dim]")
            
            # Show first example
            if cmd.examples:
                lines.append(f"    [dim italic]Example: {cmd.examples[0]}[/dim italic]")
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
        """Focus the scrollable content so arrow/pgup/pgdn scroll it natively."""
        self.query_one(ScrollableContainer).focus()

    async def on_key(self, event: events.Key) -> None:
        """Own the keyboard: scroll on arrow/j/k, dismiss on escape/?/q, and consume EVERY
        key so nothing leaks to the miller view behind the modal (the reported bug)."""
        scroll = self.query_one(ScrollableContainer)
        key = event.key
        if key in ("escape", "question_mark", "q"):
            self.dismiss()
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