from inky import InkyWHAT
from PIL import Image, ImageFont, ImageDraw


class InkyHandler:
    def __init__(self):
        self.inky_display = InkyWHAT("red")
        self.inky_display.set_border(self.inky_display.WHITE)

    def clear(self):
        self.img = Image.new("P", (self.inky_display.WIDTH, self.inky_display.HEIGHT))
        self.draw = ImageDraw.Draw(self.img)

    def draw_text(self, message, size=48, position="c", color=None):
        if color is None:
            color = self.inky_display.RED
        font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", size)
        bbox = font.getbbox(message)
        m_w, m_h = bbox[2] - bbox[0], bbox[3] - bbox[1]
        d_w, d_h = self.inky_display.WIDTH, self.inky_display.HEIGHT

        if position == "c":
            x = (d_w - m_w) // 2
            y = (d_h - m_h) // 2
        elif position == "br":
            x = d_w - m_w
            y = d_h - m_h
        elif position == "tc":
            x = (d_w - m_w) // 2
            y = 0
        else:
            x, y = 0, 0

        self.draw.text((x, y), message, color, font)

    def show(self):
        img = self.img.rotate(180)
        self.inky_display.set_image(img)
        self.inky_display.show()

    def hello_world(self):
        self.clear()
        self.draw_text("Hello World!", size=64, position="c")
        self.show()

    def welcome(self, name):
        self.clear()
        self.draw_text("Welcome home,", size=36, position="tc")
        self.draw_text(name + "!", size=52, position="c")
        self.show()

    def idle(self):
        self.clear()
        self.draw_text("Drewtopia", size=56, position="c")
        self.show()
