import customtkinter as ctk


ORANGE_PRIMARY = ["#f97316", "#ea580c"]
ORANGE_HOVER = ["#ea580c", "#c2410c"]
ORANGE_DARK = ["#c2410c", "#9a3412"]
ORANGE_MUTED = ["#fed7aa", "#7c2d12"]


def apply_orange_theme():
    """Apply a small orange accent layer over CustomTkinter's base theme."""
    ctk.set_appearance_mode("system")
    ctk.set_default_color_theme("blue")

    theme = ctk.ThemeManager.theme
    overrides = {
        "CTkButton": {"fg_color": ORANGE_PRIMARY, "hover_color": ORANGE_HOVER},
        "CTkCheckBox": {"fg_color": ORANGE_PRIMARY, "hover_color": ORANGE_HOVER},
        "CTkRadioButton": {"fg_color": ORANGE_PRIMARY, "hover_color": ORANGE_HOVER},
        "CTkSwitch": {
            "progress_color": ORANGE_PRIMARY,
            "button_hover_color": ORANGE_HOVER,
        },
        "CTkSlider": {
            "button_color": ORANGE_PRIMARY,
            "button_hover_color": ORANGE_HOVER,
            "progress_color": ORANGE_PRIMARY,
        },
        "CTkProgressBar": {"progress_color": ORANGE_PRIMARY},
        "CTkOptionMenu": {
            "fg_color": ORANGE_PRIMARY,
            "button_color": ORANGE_DARK,
            "button_hover_color": ORANGE_HOVER,
        },
        "CTkComboBox": {
            "button_color": ORANGE_PRIMARY,
            "button_hover_color": ORANGE_HOVER,
        },
        "CTkSegmentedButton": {
            "selected_color": ORANGE_PRIMARY,
            "selected_hover_color": ORANGE_HOVER,
            "unselected_hover_color": ORANGE_MUTED,
        },
        "CTkScrollbar": {
            "button_color": ORANGE_PRIMARY,
            "button_hover_color": ORANGE_HOVER,
        },
    }

    for widget_name, values in overrides.items():
        widget_theme = theme.get(widget_name)
        if widget_theme is not None:
            widget_theme.update(values)
