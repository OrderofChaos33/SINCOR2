def format_blog_post(task):
    content = task.get("content", "")
    if not content:
        return {"error": "No content provided"}
    # Simulated SEO format logic
    lines = content.strip().split("\n")
    formatted = "\n\n".join(f"<h2>{line.strip()}</h2>" for line in lines)
    return {"formatted_post": formatted}
