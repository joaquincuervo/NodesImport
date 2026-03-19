import typing

# Global instance to prevent the window from being garbage collected
restore_instance = None

def start_restore_tool() -> None:
    """
    Scope-safe launch function for the NodesImport tool.
    Uses local imports to avoid issues when Nuke starts up.
    """
    global restore_instance
    
    # Local imports for safety
    try:
        from NodesImport.ui.main_window import NodesImportWindow # type: ignore
        try:
            from PySide6 import QtWidgets # type: ignore
        except ImportError:
            from PySide2 import QtWidgets # type: ignore
    except ImportError as e:
        print(f"[NodesImport] Error importing modules: {e}")
        return

    # Create and show window
    # Note: We check if it's already open and just bring it to front if so
    if restore_instance is None:
        restore_instance = NodesImportWindow()

    restore_instance.show()
    restore_instance.raise_()
    restore_instance.activateWindow()

# Integration with Nuke UI
try:
    import nuke # type: ignore
    
    # Add to Nuke's native File menu, just above "Insert Comp Nodes..."
    menubar   = nuke.menu("Nuke")
    file_menu = menubar.findItem("File")

    # Nuke's addCommand supports an 'index' keyword to position the item.
    # We find "Insert Comp Nodes..." and insert at its position.
    # If it can't be found we fall back to appending at the end.
    try:
        items = file_menu.items()
        idx   = next(
            (i for i, item in enumerate(items) if item.name() == "Insert Comp Nodes..."),
            None,
        )
        if idx is not None:
            file_menu.addCommand(
                "Nodes Import",
                "from NodesImport import menu; menu.start_restore_tool()",
                index=idx, shortcut="shift+i",
            )
        else:
            file_menu.addCommand(
                "Nodes Import",
                "from NodesImport import menu; menu.start_restore_tool()",
                shortcut="shift+i",
            )
    except Exception:
        file_menu.addCommand(
            "Nodes Import",
            "from NodesImport import menu; menu.start_restore_tool()",
            shortcut="shift+i",
        )
except ImportError:
    # Not running inside Nuke
    pass
