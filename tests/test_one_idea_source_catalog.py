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


if __name__ == "__main__":
    test_eight_story_scenes_have_distinct_attributed_free_source_clips()
    print("PASS: eight unique, item-attributed free-source clips are mapped to the eight story beats.")
