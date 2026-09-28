"""Color palettes and shared design tokens for the DotLingo interface."""

THEME_LABELS = {
    "dark": "Тёмная",
    "light": "Светлая",
}

THEMES = {
    "dark": {
        "bg": "#17171A",
        "sidebar": "#1E1E21",
        "surface": "#252529",
        "surface_raised": "#2C2C31",
        "surface_hover": "#37373D",
        "nav_active": "#333338",
        "border": "#3F3F46",
        "border_soft": "#303035",
        "border_focus": "#F2F2F5",
        "text": "#F5F5F7",
        "muted": "#A4A4AB",
        "accent": "#E8E8EB",
        "accent_on": "#1B1B1E",
        "accent_dim": "#CECED3",
        "warning": "#F0C67A",
        "error": "#F18C8C",
        "success": "#8BC8AA",
        "highlight": "#3A3A40",
    },
    "light": {
        "bg": "#F5F5F7",
        "sidebar": "#ECECF0",
        "surface": "#FFFFFF",
        "surface_raised": "#FAFAFC",
        "surface_hover": "#EBEBEF",
        "nav_active": "#E3E3E8",
        "border": "#D1D1D6",
        "border_soft": "#E2E2E7",
        "border_focus": "#252529",
        "text": "#1D1D1F",
        "muted": "#6E6E73",
        "accent": "#2C2C31",
        "accent_on": "#FFFFFF",
        "accent_dim": "#444449",
        "warning": "#8D6B33",
        "error": "#B23A36",
        "success": "#35745E",
        "highlight": "#DADAE0",
    },
}

TOKENS = {
    **THEMES["dark"],
    "font": "Segoe UI",
    "mono": "Cascadia Mono",
    "font_small": 9,
    "font_body": 10,
    "font_title": 24,
    "font_heading": 15,
    "spacing_xs": 4,
    "spacing_sm": 8,
    "spacing_md": 14,
    "spacing_lg": 20,
    "reduced_motion": False,
}


def set_theme(name: str) -> str:
    """Update shared color tokens and return the selected theme name."""
    selected = name if isinstance(name, str) and name in THEMES else "dark"
    TOKENS.update(THEMES[selected])
    return selected
