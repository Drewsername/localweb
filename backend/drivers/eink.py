import io
from PIL import Image, ImageFont, ImageDraw

# Display dimensions (Inky wHAT)
DISPLAY_WIDTH = 400
DISPLAY_HEIGHT = 300
FONT_PATH = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"

# Color constants matching Inky wHAT palette
WHITE = 0
BLACK = 1
RED = 2


class InkyHandler:
    def __init__(self):
        from inky import InkyWHAT
        self.inky_display = InkyWHAT("red")
        self.inky_display.set_border(self.inky_display.WHITE)
        self.current_img = None

    def _new_image(self):
        img = Image.new("P", (DISPLAY_WIDTH, DISPLAY_HEIGHT), WHITE)
        draw = ImageDraw.Draw(img)
        return img, draw

    def _center_text(self, draw, text, font, y, color):
        """Draw text horizontally centered at a given y position."""
        bb = font.getbbox(text)
        w = bb[2] - bb[0]
        x = (DISPLAY_WIDTH - w) // 2
        draw.text((x, y), text, color, font)

    def _show(self, img):
        self.current_img = img
        rotated = img.rotate(180)
        self.inky_display.set_image(rotated)
        self.inky_display.show()

    def get_display_png(self):
        """Return current display image as PNG bytes for the web preview."""
        if self.current_img is None:
            return None
        # Convert palette image to RGB for PNG export with correct colors
        rgb = Image.new("RGB", self.current_img.size, (255, 255, 255))
        pixels = self.current_img.load()
        rgb_pixels = rgb.load()
        for y in range(self.current_img.height):
            for x in range(self.current_img.width):
                p = pixels[x, y]
                if p == BLACK:
                    rgb_pixels[x, y] = (0, 0, 0)
                elif p == RED:
                    rgb_pixels[x, y] = (200, 0, 0)
                # WHITE stays (255, 255, 255)
        buf = io.BytesIO()
        rgb.save(buf, format="PNG")
        return buf.getvalue()

    def hello_world(self):
        img, draw = self._new_image()
        font = ImageFont.truetype(FONT_PATH, 36)
        self._center_text(draw, "Hello World!", font, 130, BLACK)
        self._show(img)

    def welcome(self, name):
        img, draw = self._new_image()
        line1_font = ImageFont.truetype(FONT_PATH, 24)
        name_font = ImageFont.truetype(FONT_PATH, 36)
        # "Welcome home," in black, positioned above center
        self._center_text(draw, "Welcome home,", line1_font, 110, BLACK)
        # Name in red, below
        self._center_text(draw, name + "!", name_font, 145, RED)
        self._show(img)

    def idle(self):
        img, draw = self._new_image()
        font = ImageFont.truetype(FONT_PATH, 36)
        self._center_text(draw, "Drewtopia", font, 130, RED)
        self._show(img)
