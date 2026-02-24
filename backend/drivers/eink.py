import io
import os
from PIL import Image, ImageFont, ImageDraw

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

def render_welcome(name):
    """Welcome screen: 'Welcome to Drewtopia, [Name]!' auto-sized to fill display."""
    img, draw = _new_image()
    usable_w = DISPLAY_WIDTH - PADDING * 2

    # Line 1: "Welcome to"
    line1 = "Welcome to"
    f1, s1 = _fit_font_bold(line1, usable_w, max_size=40)
    w1, h1 = _text_size(f1, line1)

    # Line 2: "Drewtopia,"
    line2 = "Drewtopia,"
    f2, s2 = _fit_font_bold(line2, usable_w, max_size=56)
    w2, h2 = _text_size(f2, line2)

    # Line 3: Name!
    line3 = name + "!"
    f3, s3 = _fit_font_bold(line3, usable_w, max_size=56)
    w3, h3 = _text_size(f3, line3)

    # Vertical layout — center the block
    spacing = 10
    total_h = h1 + spacing + h2 + spacing + h3
    y_start = (DISPLAY_HEIGHT - total_h) // 2

    y = y_start
    draw.text((_center_x(w1), y), line1, BLACK, f1)
    y += h1 + spacing
    draw.text((_center_x(w2), y), line2, RED, f2)
    y += h2 + spacing
    draw.text((_center_x(w3), y), line3, RED, f3)

    return img


def render_dashboard(home_users):
    """Dashboard: 'Drewtopia' header with a list of who's home."""
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
    content_y = line_y + 15

    if not home_users:
        # Nobody home
        msg_font = _regular(20)
        mw, _ = _text_size(msg_font, "No one is home")
        draw.text((_center_x(mw), content_y + 30), "No one is home", BLACK, msg_font)
    else:
        # "Home" label
        label_font = _regular(18)
        draw.text((PADDING, content_y), "Home", BLACK, label_font)
        content_y += 30

        # List each user — auto-size to fit available space
        available_h = DISPLAY_HEIGHT - content_y - PADDING
        max_name_size = min(36, available_h // max(len(home_users), 1) - 8)
        max_name_size = max(max_name_size, 14)

        for user_name in home_users:
            name_font, _ = _fit_font_bold(user_name, usable_w - 30, max_size=max_name_size)
            nw, nh = _text_size(name_font, user_name)

            # Bullet point
            bullet_y = content_y + nh // 2
            draw.ellipse(
                [PADDING + 5, bullet_y - 3, PADDING + 11, bullet_y + 3],
                fill=RED,
            )

            draw.text((PADDING + 22, content_y), user_name, BLACK, name_font)
            content_y += nh + 10

            if content_y > DISPLAY_HEIGHT - PADDING:
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
    rgb = Image.new("RGB", img.size, (255, 255, 255))
    pixels = img.load()
    rgb_pixels = rgb.load()
    for y in range(img.height):
        for x in range(img.width):
            p = pixels[x, y]
            if p in COLOR_MAP:
                rgb_pixels[x, y] = COLOR_MAP[p]
    buf = io.BytesIO()
    rgb.save(buf, format="PNG")
    return buf.getvalue()


# ---------------------------------------------------------------------------
# Shared display state
# ---------------------------------------------------------------------------

current_img = None


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

    def _show(self, img):
        set_current(img)
        rotated = img.rotate(180)
        self.inky_display.set_image(rotated)
        self.inky_display.show()

    def show_image(self, img):
        """Public interface to display any pre-rendered image."""
        self._show(img)
