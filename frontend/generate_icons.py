from PIL import Image, ImageDraw

def create_transparent_icon(size, output_path):
    # Solid background (RGB) #0a0e17
    img = Image.new('RGB', (size, size), (10, 14, 23))
    draw = ImageDraw.Draw(img)
    
    # Scale coordinates based on size, with some padding (multiply by 0.7 to shrink, offset by 15% to center)
    padding_scale = 0.7
    offset = size * 0.15
    s = (size / 32.0) * padding_scale
    
    # Helper to calculate scaled x, y
    def scale_pt(x, y):
        return (x * s + offset, y * s + offset)

    # Graph line points scaled
    points = [
        scale_pt(4, 24),
        scale_pt(9, 18),
        scale_pt(14, 21),
        scale_pt(19, 10),
        scale_pt(24, 14),
        scale_pt(28, 6)
    ]
    
    # Draw graph line (blue-ish #007cf0)
    draw.line(points, fill=(0, 124, 240, 255), width=int(3*s), joint="curve")
    
    # Draw nodes (cyan #00f0ff)
    def draw_circle(cx, cy, r, alpha=255):
        pt = scale_pt(cx, cy)
        box = [pt[0] - r*s, pt[1] - r*s, pt[0] + r*s, pt[1] + r*s]
        draw.ellipse(box, fill=(0, 240, 255, alpha))
        
    draw_circle(4, 24, 2, int(255 * 0.7))
    draw_circle(14, 21, 1.5, int(255 * 0.6))
    draw_circle(19, 10, 2, int(255 * 0.8))
    draw_circle(28, 6, 2.5, 255)
    
    # Diamond at peak (optional, let's keep it simple or approximate it)
    poly = [scale_pt(28, 3), scale_pt(30.5, 6), scale_pt(28, 9), scale_pt(25.5, 6)]
    draw.polygon(poly, outline=(0, 240, 255, 153), width=int(1.5*s))
    
    img.save(output_path)
    print(f"Created {output_path}")

create_transparent_icon(192, "d:/MigiArbitrage/frontend/public/icon-192x192.png")
create_transparent_icon(512, "d:/MigiArbitrage/frontend/public/icon-512x512.png")
create_transparent_icon(192, "d:/MigiArbitrage/frontend/public/icon-192.png")
