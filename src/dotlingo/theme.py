"""Color palettes and shared design tokens for the DotLingo interface."""

THEME_LABELS = {
    "dark": "Тёмная",
    "light": "Светлая",
}

THEMES = {
    "dark": {
        "bg": "#17181A",
        "sidebar": "#1C1D20",
        "surface": "#202124",
        "surface_raised": "#292A2D",
        "surface_hover": "#34363A",
        "nav_active": "#2B2D31",
        "border": "#3A3B3F",
        "border_soft": "#2D2E32",
        "border_focus": "#70B5FF",
        "text": "#F5F5F7",
        "muted": "#A1A1A6",
        "accent": "#0A84FF",
        "accent_on": "#FFFFFF",
        "accent_dim": "#0066CC",
        "warning": "#FFB454",
        "error": "#FF6961",
        "success": "#30D158",
        "highlight": "#303A48",
    },
    "light": {
        "bg": "#F5F5F7",
        "sidebar": "#FBFBFD",
        "surface": "#FFFFFF",
        "surface_raised": "#FFFFFF",
        "surface_hover": "#ECECF0",
        "nav_active": "#E8F2FF",
        "border": "#D2D2D7",
        "border_soft": "#E5E5EA",
        "border_focus": "#007AFF",
        "text": "#1D1D1F",
        "muted": "#6E6E73",
        "accent": "#007AFF",
        "accent_on": "#FFFFFF",
        "accent_dim": "#0063CE",
        "warning": "#A85D00",
        "error": "#D70015",
        "success": "#248A3D",
        "highlight": "#D6E8FF",
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
