"""Color palettes and shared design tokens for the DotLingo interface."""

THEME_LABELS = {
    "dark": "Тёмная",
    "light": "Светлая",
}

THEMES = {
    "dark": {
        "bg": "#12181b",
        "surface": "#1b2327",
        "surface_raised": "#222c31",
        "surface_hover": "#2b373c",
        "border": "#354249",
        "border_soft": "#29353a",
        "border_focus": "#9bbcc8",
        "text": "#f0f1ec",
        "muted": "#a8b3b5",
        "accent": "#a8ccd5",
        "accent_on": "#12181b",
        "accent_dim": "#49636b",
        "warning": "#e0a276",
        "error": "#eb908a",
        "success": "#a7cba9",
        "highlight": "#34474d",
    },
    "light": {
        "bg": "#f4f2ec",
        "surface": "#fbfaf6",
        "surface_raised": "#ffffff",
        "surface_hover": "#edf1f1",
        "border": "#d9dcd8",
        "border_soft": "#e8e9e4",
        "border_focus": "#547a8b",
        "text": "#202a30",
        "muted": "#657178",
        "accent": "#315f70",
        "accent_on": "#ffffff",
        "accent_dim": "#274e5d",
        "warning": "#a85f3d",
        "error": "#b34f48",
        "success": "#4f765e",
        "highlight": "#dce9eb",
    },
}

TOKENS = {
    **THEMES["dark"],
    "font": "Segoe UI",
    "mono": "Cascadia Mono",
    "font_small": 10,
    "font_body": 12,
    "font_title": 28,
    "font_heading": 17,
    "spacing_xs": 5,
    "spacing_sm": 9,
    "spacing_md": 14,
    "spacing_lg": 22,
    "reduced_motion": False,
}


def set_theme(name: str) -> str:
    """Update shared color tokens and return the selected theme name."""
    selected = name if isinstance(name, str) and name in THEMES else "dark"
    TOKENS.update(THEMES[selected])
    return selected
