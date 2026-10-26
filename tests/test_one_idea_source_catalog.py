"""No-network guard for the eight real source clips in the One Idea campaign."""
from studio_ultimate import _ONE_IDEA_PEXELS_MOTION_SOURCES


def test_eight_story_scenes_have_distinct_attributed_free_source_clips():
    rows = _ONE_IDEA_PEXELS_MOTION_SOURCES
    assert set(rows) == set(range(1, 9)), sorted(rows)
    ids = [row["source_id"] for row in rows.values()]
    urls = [row["source_url"] for row in rows.values()]
    media = [row["media_url"] for row in rows.values()]
    creators = [row["creator"] for row in rows.values()]
    assert len(set(ids)) == 8, ids
    assert len(set(urls)) == 8, urls
    assert len(set(media)) == 8, media
    assert all(url.startswith("https://www.pexels.com/video/") for url in urls), urls
    assert all(url.startswith("https://videos.pexels.com/video-files/") for url in media), media
    assert all(name.strip() for name in creators), creators
    assert all(str(row["source_id"]) in row["media_url"] for row in rows.values())
    assert all(row["title"] and row["description"] and row["visual_match"] for row in rows.values())
    # These are the story's eight individually attributed shots, including a
    # blank-screen hook, warm sunrise mountains, a real cityscape, an editor
    # timeline and a restrained city-night signature background.
    expected = {
        1: (6611942, "Tima Miroshnichenko", "blank-screen"),
        2: (8448889, "ArtHouse Studio", "thinking-and-writing"),
        3: (7278330, "Anastasia Shuraeva", "notebook"),
        4: (8033493, "Anastasia Shuraeva", "mountain-scenery-at-sunrise"),
        5: (31013711, "Adis Resic", "futuristic-oceanfront-cityscape-at-night"),
        6: (15517230, "Ludovic Delot", "video-editing-screen"),
        7: (35686462, "Ahmed", "rocky-shoreline-at-sunrise"),
        8: (9544126, "K / 8 K Lover of Aerials and Photography", "city-at-night-with-lights-and-buildings"),
    }
    for index, (source_id, creator, slug) in expected.items():
        row = rows[index]
        assert row["source_id"] == source_id, (index, row)
        assert creator.lower() in row["creator"].lower(), (index, row["creator"])
        assert slug in row["source_url"].lower(), (index, row["source_url"])
    assert rows[1]["title"] == "Man Typing on Laptop with Blank Screen"
    assert "sunrise" in rows[4]["title"].lower()
    assert "timeline" in rows[6]["description"].lower()
    assert "timeline" in rows[6]["title"].lower()
    assert "blank laptop" in rows[1]["visual_match"].lower()


if __name__ == "__main__":
    test_eight_story_scenes_have_distinct_attributed_free_source_clips()
    print("PASS: eight unique, item-attributed free-source clips are mapped to the eight story beats.")
