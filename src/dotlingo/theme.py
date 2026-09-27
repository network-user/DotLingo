"""Color palettes and shared design tokens for the DotLingo interface."""

THEME_LABELS = {
    "dark": "Тёмная",
    "light": "Светлая",
}

THEMES = {
    "dark": {
        "bg": "#0C1215",
        "sidebar": "#10191C",
        "surface": "#162125",
        "surface_raised": "#1D2B2F",
        "surface_hover": "#28383B",
        "nav_active": "#23332E",
        "border": "#385052",
        "border_soft": "#26383B",
        "border_focus": "#B9EF7A",
        "text": "#F1F5EF",
        "muted": "#A5B9B5",
        "accent": "#B9EF7A",
        "accent_on": "#172216",
        "accent_dim": "#98D45D",
        "warning": "#F4BD72",
        "error": "#FF8982",
        "success": "#82D7B8",
        "highlight": "#334B41",
    },
    "light": {
        "bg": "#F1F4EF",
        "sidebar": "#E7EEE8",
        "surface": "#FFFFFF",
        "surface_raised": "#F3F7F3",
        "surface_hover": "#DCE9DD",
        "nav_active": "#D6E8CF",
        "border": "#BCD0C4",
        "border_soft": "#D8E4DA",
        "border_focus": "#286A45",
        "text": "#172522",
        "muted": "#50675F",
        "accent": "#245F3E",
        "accent_on": "#FFFFFF",
        "accent_dim": "#174B2F",
        "warning": "#8D560F",
        "error": "#B33B34",
        "success": "#247858",
        "highlight": "#CCE6D3",
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
    "spacing_md": 18,
    "spacing_lg": 26,
    "reduced_motion": False,
}


def set_theme(name: str) -> str:
    """Update shared color tokens and return the selected theme name."""
    selected = name if isinstance(name, str) and name in THEMES else "dark"
    TOKENS.update(THEMES[selected])
    return selected
