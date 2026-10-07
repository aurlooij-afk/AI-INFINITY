            "The core idea": "The core idea should remain easy to apply.",
            "How it works": "The process is intent, action, feedback, and refinement.",
            "Real-world examples": "Examples should support the point without distracting from it.",
            "What changes": "The useful result is better decisions and clearer execution.",
            "Practical takeaway": "Turn the idea into one concrete next step.",
            "Closing": "That is the idea to carry forward.",
        }.get(name, "Keep the result practical and clear.")
        text = re.sub(r"\s+", " ", f"{lead} The focus is {topic_label}. {tail}").strip()
        if source_title and source_title.lower() not in clean_title.lower() and len(text.split()) < budget - 5:
            text += f" Source context: {source_title}."
        words = text.split()
        if len(words) > budget:
            text = " ".join(words[:budget]).rstrip(" ,.;:") + "."
        return text

    chapters = []
    for i, name in enumerate(names):
        src = usable_sources[i % len(usable_sources)]
        sec = max(4.0, round(duration * ratios[i], 1))
        narration = bounded_narration(name, sec, src)
        long_visual_focus = {
            "Hook": "human opening moment, compelling subject, clear place or action",
            "Context": "environment, setting, people and real-world context",
            "The core idea": "prototype, mechanism, close detail, explanatory visual",
            "How it works": "process, tools, sequence of action, practical demonstration",
            "Real-world examples": "people using the idea in a real environment",
            "What changes": "before-and-after or visible outcome",
            "Practical takeaway": "creator applying the idea successfully",
            "Closing": "finished work, confident human subject, memorable final frame",
        }
        focus = (visual_focus if fmt == "short" else long_visual_focus).get(name, "real-world editorial scene")
        chapters.append({
            "heading": f"{name} — {clean_title}" if name in {"Hook", "Closing"} else f"{name}: {clean_title}",
            "narration": narration,
            "visual_query": f"{topic_label} {focus} documentary photography",
            "image_prompt": f"Premium editorial documentary image about {topic_label}; visual focus: {focus}; realistic people, locations, objects or processes, natural cinematic lighting, coherent composition, strong subject separation, no logos, no text, visually distinct from other scenes",
            "on_screen": name,
            "duration": sec,
            "proof_needed": [src.get("title")] if src.get("title") and src.get("title") != clean_title else [],
        })
    return {