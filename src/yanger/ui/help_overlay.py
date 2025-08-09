"""Help overlay widget for YouTube Ranger.

Displays keybindings and commands from the central registry.
"""
# Modified: 2025-08-08

from textual.app import ComposeResult
from textual.containers import Container, Vertical, ScrollableContainer
from textual.widgets import Static
from textual.binding import Binding
from textual import events

from ..keybindings import registry


class HelpOverlay(Container):
    """Overlay widget showing help information."""
    
    DEFAULT_CSS = """
    HelpOverlay {
        layer: overlay;
        width: 80%;
        height: 80%;
        align: center middle;
        display: none;
    }
    
    HelpOverlay.visible {
        display: block;
    }
    
    HelpOverlay > Vertical {
        width: 100%;
        height: 100%;
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
        border-top: solid $border;
        padding-top: 1;
        color: $text-muted;
    }
    
    HelpOverlay .command-section {
        border-top: solid $border;
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
    
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.can_focus = True
        
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
        lines.append("  • Commands support tab completion and history")
        
        return "\n".join(lines)
    
    def show(self) -> None:
        """Show the help overlay."""
        # Regenerate content to catch any dynamic changes
        content_widget = self.query_one(".help-content Static")
        if content_widget:
            content_widget.update(self._generate_help_content())
        
        self.add_class("visible")
        self.focus()
        
    def hide(self) -> None:
        """Hide the help overlay."""
        self.remove_class("visible")
        
    async def on_key(self, event: events.Key) -> None:
        """Handle key events."""
        if event.key in ["escape", "?"]:
            self.hide()
            event.stop()