"""Color palettes and shared design tokens for the DotLingo interface."""

THEME_LABELS = {
    "dark": "Тёмная",
    "light": "Светлая",
}

THEMES = {
    "dark": {
        "bg": "#0A0A0C",
        "sidebar": "#111114",
        "surface": "#19191C",
        "surface_raised": "#202024",
        "surface_hover": "#2A2A2F",
        "nav_active": "#29292E",
        "border": "#39393F",
        "border_soft": "#2B2B30",
        "border_focus": "#E7E7EB",
        "text": "#F5F5F7",
        "muted": "#A4A4AB",
        "accent": "#E7E7EB",
        "accent_on": "#161619",
        "accent_dim": "#C8C8CE",
        "warning": "#F0C67A",
        "error": "#F18C8C",
        "success": "#8BC8AA",
        "highlight": "#36363C",
    },
    "light": {
        "bg": "#F2F2F4",
        "sidebar": "#E9E9ED",
        "surface": "#FFFFFF",
        "surface_raised": "#F8F8F9",
        "surface_hover": "#E9E9ED",
        "nav_active": "#DEDEE3",
        "border": "#C9C9D0",
        "border_soft": "#DFDFE4",
        "border_focus": "#29292D",
        "text": "#1B1B1F",
        "muted": "#686870",
        "accent": "#29292D",
        "accent_on": "#FFFFFF",
        "accent_dim": "#414146",
        "warning": "#8D6B33",
        "error": "#B23A36",
        "success": "#35745E",
        "highlight": "#D7D7DD",
    },
}

TOKENS = {
    **THEMES["dark"],
    "font": "Segoe UI",
    "mono": "Cascadia Mono",
    "font_small": 10,
    "font_body": 11,
    "font_title": 30,
    "font_heading": 17,
    "spacing_xs": 6,
    "spacing_sm": 10,
    "spacing_md": 20,
    "spacing_lg": 28,
    "reduced_motion": False,
}


def set_theme(name: str) -> str:
    """Update shared color tokens and return the selected theme name."""
    selected = name if isinstance(name, str) and name in THEMES else "dark"
    TOKENS.update(THEMES[selected])
    return selected
