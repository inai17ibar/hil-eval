"""Seed six text/image examples for the local review UI."""

import argparse
from pathlib import Path

from PIL import Image, ImageDraw

import hil_eval as hil


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", default=".hil/hil.db")
    args = parser.parse_args()
    hil.init("demo", store=f"sqlite:///{args.db}", audit_rate=0)

    @hil.review(judge=lambda inputs, output: hil.Judgment(0.3, reason="Please verify"))
    def answer(question):
        return {"answer": "Maybe 42?", "question": question}

    for question in ["What is the meaning of life?", "How many moons does Mars have?"]:
        answer(question)
    hil.submit("Translate 'bonjour'", "Goodbye", confidence=0.2)
    hil.submit("Capital of France?", "Paris", confidence=0.7)

    @hil.review(
        judge=lambda inputs, output: hil.Judgment(0.4, reason="Portion size uncertain"),
        images=["image"],
    )
    def estimate_meal(image, description):
        return {"meal": description, "calories": 450, "protein_g": 12}

    image_dir = Path(args.db).parent / "demo-images"
    image_dir.mkdir(parents=True, exist_ok=True)
    for name, color in [("salad", "green"), ("pasta", "orange")]:
        image = Image.new("RGB", (160, 120), "ivory")
        draw = ImageDraw.Draw(image)
        draw.ellipse((15, 10, 145, 110), fill="white", outline="gray")
        draw.rectangle((45, 35, 115, 85), fill=color)
        path = image_dir / f"{name}.png"
        image.save(path)
        estimate_meal(path, name)
    print(f"hil-eval serve --db {args.db} then open http://127.0.0.1:8765")


if __name__ == "__main__":
    main()
