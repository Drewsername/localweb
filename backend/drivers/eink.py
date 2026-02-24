import io
import os
from PIL import Image, ImageFont, ImageDraw

# Display dimensions (Inky wHAT)
DISPLAY_WIDTH = 400
DISPLAY_HEIGHT = 300

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

# Font path — Pi has DejaVu, fall back to default on other systems
_FONT_PATHS = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "C:/Windows/Fonts/arialbd.ttf",
]


def _get_font(size):
    for path in _FONT_PATHS:
        if os.path.exists(path):
            return ImageFont.truetype(path, size)
    return ImageFont.load_default()


def _new_image():
    img = Image.new("P", (DISPLAY_WIDTH, DISPLAY_HEIGHT), WHITE)
    draw = ImageDraw.Draw(img)
    return img, draw


def _center_text(draw, text, font, y, color):
    """Draw text horizontally centered at a given y position."""
    bb = font.getbbox(text)
    w = bb[2] - bb[0]
    x = (DISPLAY_WIDTH - w) // 2
    draw.text((x, y), text, color, font)


def render_idle():
    img, draw = _new_image()
    font = _get_font(36)
    _center_text(draw, "Drewtopia", font, 130, RED)
    return img


def render_welcome(name):
    img, draw = _new_image()
    line1_font = _get_font(24)
    name_font = _get_font(36)
    _center_text(draw, "Welcome home,", line1_font, 110, BLACK)
    _center_text(draw, name + "!", name_font, 145, RED)
    return img


def render_hello():
    img, draw = _new_image()
    font = _get_font(36)
    _center_text(draw, "Hello World!", font, 130, BLACK)
    return img


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


# Shared current image — always has a value after first render
current_img = None


def set_current(img):
    global current_img
    current_img = img


def get_display_png():
    """Return current display image as PNG bytes."""
    if current_img is None:
        # Generate idle image as default
        set_current(render_idle())
    return image_to_png(current_img)


class InkyHandler:
    """Hardware driver — sends images to the physical Inky wHAT display."""

    def __init__(self):
        from inky import InkyWHAT
        self.inky_display = InkyWHAT("red")
        self.inky_display.set_border(self.inky_display.WHITE)

    def _show(self, img):
        set_current(img)
        rotated = img.rotate(180)
        self.inky_display.set_image(rotated)
        self.inky_display.show()

    def hello_world(self):
        self._show(render_hello())

    def welcome(self, name):
        self._show(render_welcome(name))

    def idle(self):
        self._show(render_idle())
