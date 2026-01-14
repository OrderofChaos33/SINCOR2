def resize_banner(task):
    image = task.get("image", "unknown.jpg")
    size = task.get("size", "800x600")
    # Simulated resizing logic
    return {"image": image, "new_size": size, "status": "resized (simulated)"}
