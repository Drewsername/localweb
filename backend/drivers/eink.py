import io
import os
from PIL import Image, ImageFont, ImageDraw
import qrcode

# Display dimensions (Inky wHAT)
DISPLAY_WIDTH = 400
DISPLAY_HEIGHT = 300
PADDING = 15

# Color constants matching Inky wHAT palette
WHITE = 0
BLACK = 1
RED = 2

# RGB values for web preview
COLOR_MAP = {
    WHITE: (255, 255, 255),
    BLACK: (0, 0, 0),
    RED: (200, 0, 0),
}

# Font paths — Pi has DejaVu, fall back on other systems
_FONT_PATHS = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "C:/Windows/Fonts/arialbd.ttf",
    "C:/Windows/Fonts/arial.ttf",
]
_BOLD_FONT_PATHS = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "C:/Windows/Fonts/arialbd.ttf",
]
_REGULAR_FONT_PATHS = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "C:/Windows/Fonts/arial.ttf",
    "C:/Windows/Fonts/arialbd.ttf",
]


def _load_font(paths, size):
    for path in paths:
        if os.path.exists(path):
            return ImageFont.truetype(path, size)
    return ImageFont.load_default()


def _bold(size):
    return _load_font(_BOLD_FONT_PATHS, size)


def _regular(size):
    return _load_font(_REGULAR_FONT_PATHS, size)


def _text_size(font, text):
    bb = font.getbbox(text)
    return bb[2] - bb[0], bb[3] - bb[1]


def _fit_font_bold(text, max_width, max_size=72, min_size=12):
    """Find the largest bold font size where text fits within max_width."""
    for size in range(max_size, min_size - 1, -1):
        font = _bold(size)
        w, _ = _text_size(font, text)
        if w <= max_width:
            return font, size
    return _bold(min_size), min_size


def _center_x(text_width):
    return (DISPLAY_WIDTH - text_width) // 2


def _new_image():
    img = Image.new("P", (DISPLAY_WIDTH, DISPLAY_HEIGHT), WHITE)
    draw = ImageDraw.Draw(img)
    return img, draw


# ---------------------------------------------------------------------------
# Render functions
# ---------------------------------------------------------------------------

def render_welcome(names):
    """Welcome screen: 'Welcome home, [Names]!' auto-sized to fill display.

    names can be a string (single name) or a list of strings.
    """
    img, draw = _new_image()
    usable_w = DISPLAY_WIDTH - PADDING * 2

    # Normalize to list
    if isinstance(names, str):
        names = [names]

    # Format names string: "Drew!", "Drew and Annabelle!", "Drew, Annabelle, and Steve!"
    if len(names) == 1:
        names_str = names[0] + "!"
    elif len(names) == 2:
        names_str = f"{names[0]} and {names[1]}!"
    else:
        names_str = ", ".join(names[:-1]) + ", and " + names[-1] + "!"

    # Line 1: "Welcome home"
    line1 = "Welcome home"
    f1, s1 = _fit_font_bold(line1, usable_w, max_size=44)
    w1, h1 = _text_size(f1, line1)

    # Line 2: Names
    f2, s2 = _fit_font_bold(names_str, usable_w, max_size=56)
    w2, h2 = _text_size(f2, names_str)

    # Vertical layout — center the block
    spacing = 12
    total_h = h1 + spacing + h2
    y_start = (DISPLAY_HEIGHT - total_h) // 2

    y = y_start
    draw.text((_center_x(w1), y), line1, BLACK, f1)
    y += h1 + spacing
    draw.text((_center_x(w2), y), names_str, RED, f2)

    return img


def _generate_wifi_qr(size):
    """Generate a WiFi join QR code as a palette-mode PIL image."""
    ssid = os.environ.get("WIFI_SSID", "")
    password = os.environ.get("WIFI_PASSWORD", "")
    if not ssid:
        return None
    wifi_string = f"WIFI:T:WPA;S:{ssid};P:{password};;"
    qr = qrcode.QRCode(
        error_correction=qrcode.constants.ERROR_CORRECT_L,
        box_size=1,
        border=1,
    )
    qr.add_data(wifi_string)
    qr.make(fit=True)
    qr_img = qr.make_image(fill_color="black", back_color="white").convert("L")
    # Resize to target size using nearest-neighbor to keep sharp pixels
    qr_img = qr_img.resize((size, size), Image.NEAREST)
    # Convert to palette mode: white=0, black=1 (matching our palette)
    palette_qr = Image.new("P", (size, size), WHITE)
    qr_pixels = qr_img.load()
    p_pixels = palette_qr.load()
    for y in range(size):
        for x in range(size):
            if qr_pixels[x, y] < 128:
                p_pixels[x, y] = BLACK
    return palette_qr


def render_dashboard(home_users):
    """Dashboard: 'Drewtopia' header, who's home on left, WiFi QR on right."""
    img, draw = _new_image()
    usable_w = DISPLAY_WIDTH - PADDING * 2

    # Header: "Drewtopia"
    header_font = _bold(36)
    hw, hh = _text_size(header_font, "Drewtopia")
    draw.text((_center_x(hw), PADDING), "Drewtopia", RED, header_font)

    # Divider line
    line_y = PADDING + hh + 10
    draw.line([(PADDING, line_y), (DISPLAY_WIDTH - PADDING, line_y)], fill=BLACK, width=2)

    # Content area
    content_top = line_y + 15
    content_bottom = DISPLAY_HEIGHT - PADDING
    content_h = content_bottom - content_top

    # QR code on the right side — as large as possible
    qr_label_font = _regular(12)
    label_text = "drew.local"
    lw, lh = _text_size(qr_label_font, label_text)
    qr_size = min(content_h - lh - 4, usable_w // 2)
    qr_img = _generate_wifi_qr(qr_size)

    if qr_img:
        # Position QR: right-aligned, vertically centered in content area
        total_qr_h = qr_size + lh + 4
        qr_x = DISPLAY_WIDTH - PADDING - qr_size
        qr_y = content_top + (content_h - total_qr_h) // 2
        img.paste(qr_img, (qr_x, qr_y))

        # "drew.com" label centered below QR
        label_x = qr_x + (qr_size - lw) // 2
        label_y = qr_y + qr_size + 4
        draw.text((label_x, label_y), label_text, BLACK, qr_label_font)

        # Left column width for names
        left_col_w = qr_x - PADDING - 10
    else:
        left_col_w = usable_w

    # Names on the left
    content_y = content_top

    if not home_users:
        msg_font = _regular(20)
        msg = "No one is home"
        mw, _ = _text_size(msg_font, msg)
        msg_x = PADDING + (left_col_w - mw) // 2 if left_col_w < usable_w else _center_x(mw)
        draw.text((msg_x, content_y + 30), msg, BLACK, msg_font)
    else:
        # "Home" label
        label_font = _regular(18)
        draw.text((PADDING, content_y), "Home", BLACK, label_font)
        content_y += 30

        # List each user — auto-size to fit available space
        available_h = content_bottom - content_y
        max_name_size = min(36, available_h // max(len(home_users), 1) - 8)
        max_name_size = max(max_name_size, 14)

        for user_name in home_users:
            name_font, _ = _fit_font_bold(user_name, left_col_w - 30, max_size=max_name_size)
            nw, nh = _text_size(name_font, user_name)

            # Bullet point
            bullet_y = content_y + nh // 2
            draw.ellipse(
                [PADDING + 5, bullet_y - 3, PADDING + 11, bullet_y + 3],
                fill=RED,
            )

            draw.text((PADDING + 22, content_y), user_name, BLACK, name_font)
            content_y += nh + 10

            if content_y > content_bottom:
                break

    return img


def render_idle():
    """Idle screen when nobody is home."""
    return render_dashboard([])


def render_hello():
    """Test screen."""
    img, draw = _new_image()
    font = _bold(36)
    w, h = _text_size(font, "Hello World!")
    draw.text((_center_x(w), (DISPLAY_HEIGHT - h) // 2), "Hello World!", BLACK, font)
    return img


# ---------------------------------------------------------------------------
# PNG export
# ---------------------------------------------------------------------------

def image_to_png(img):
    """Convert a palette-mode display image to PNG bytes."""
    cmap = DARK_COLOR_MAP if dark_mode else COLOR_MAP
    bg = cmap[WHITE]
    rgb = Image.new("RGB", img.size, bg)
    pixels = img.load()
    rgb_pixels = rgb.load()
    for y in range(img.height):
        for x in range(img.width):
            p = pixels[x, y]
            if p in cmap:
                rgb_pixels[x, y] = cmap[p]
    buf = io.BytesIO()
    rgb.save(buf, format="PNG")
    return buf.getvalue()


# ---------------------------------------------------------------------------
# Shared display state
# ---------------------------------------------------------------------------

current_img = None
dark_mode = False

# Dark mode color map: black bg, everything else white (red doesn't pop)
DARK_COLOR_MAP = {
    WHITE: (0, 0, 0),
    BLACK: (255, 255, 255),
    RED: (255, 255, 255),
}

# Lookup table: map WHITE→BLACK(1), BLACK→WHITE(0), RED→WHITE(0)
_DARK_LUT = [1, 0, 0] + list(range(3, 256))


def set_dark_mode(enabled):
    global dark_mode
    dark_mode = bool(enabled)


def is_dark_mode():
    return dark_mode


def set_current(img):
    global current_img
    current_img = img


def get_display_png():
    """Return current display image as PNG bytes."""
    if current_img is None:
        set_current(render_idle())
    return image_to_png(current_img)


# ---------------------------------------------------------------------------
# Hardware driver
# ---------------------------------------------------------------------------

class InkyHandler:
    """Sends images to the physical Inky wHAT display."""

    def __init__(self):
        from inky import InkyWHAT
        self.inky_display = InkyWHAT("red")
        self.inky_display.set_border(self.inky_display.WHITE)
        self._last_bytes = None

    def _show(self, img):
        set_current(img)
        display_img = img.point(_DARK_LUT) if dark_mode else img
        img_bytes = display_img.tobytes()
        if img_bytes == self._last_bytes:
            return
        self._last_bytes = img_bytes
        rotated = display_img.rotate(180)
        self.inky_display.set_image(rotated)
        self.inky_display.show()

    def show_image(self, img):
        """Public interface to display any pre-rendered image."""
        self._show(img)
